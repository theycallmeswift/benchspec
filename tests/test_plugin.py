"""Plugin tests via pytester — exercise the hooks in-process, no `claude -p`.

A dummy `test_eval(eval_arm)` is collected with `--collect-only`, so the body never
runs; we assert on the parametrized node ids the plugin generates. Each project writes a
`[tool.evalspec]` eval set so `resolved_run_set` has a real set to resolve.
"""

import json
import textwrap
from pathlib import Path
from typing import NoReturn

import pytest

from evalspec import plugin, workspace
from evalspec.agents.base import AgentCapabilities
from tests.support import seed_arm

ALPHA_MD = textwrap.dedent(
    """\
---
{}
---

## Prompt

Archive the source note.

## Assertions

- [ ] source note archived
"""
)

BETA_MD = textwrap.dedent(
    """\
---
{}
---

## Prompt

t1

## Assertions

- [ ] summary written
"""
)

# A `default` eval set with two arms — the resolved columns are [baseline, trial] with
# baseline as the Δ reference. The set-level harness = "claude-code" means a sweep
# inherits a concrete harness, and the dummy collect never needs a real CLI.
ARMS_TOML = textwrap.dedent(
    """\
[tool.evalspec]
default-set = "default"

[tool.evalspec.sets.default]
harness = "claude-code"
model = "sonnet"
baseline = "baseline"
arms = [{name="baseline"}, {name="trial", model="opus"}]
"""
)

# Same as ARMS_TOML plus a [tool.evalspec.judge] env entry whose key is secret-shaped
# (matches report._SECRET_KEY) — proves the judge env path is actually redacted, not
# just presence-checked against an always-empty {} like every other judge test here.
JUDGE_ENV_TOML = """\
[tool.evalspec]
default-set = "default"

[tool.evalspec.sets.default]
harness = "claude-code"
model = "sonnet"
baseline = "baseline"
arms = [{name="baseline"}, {name="trial", model="opus"}]

[tool.evalspec.judge]
env = { OPENAI_API_KEY = "sk-live-supersecret123" }
"""

DUMMY_CASES = """
def test_eval(eval_arm):
    pass
"""


def _make_project(
    pytester: object, skill: object = "myskill", arms_toml: object = ARMS_TOML
) -> None:
    """Create project."""
    (pytester.path / "pyproject.toml").write_text(arms_toml)
    evals = pytester.path / "skills" / skill / "evals" / skill
    evals.mkdir(parents=True)
    (evals / "alpha.eval.md").write_text(ALPHA_MD)
    (evals / "beta.eval.md").write_text(BETA_MD)
    pytester.makepyfile(test_cases=DUMMY_CASES)


def _collect(pytester: object, *extra: object) -> object:
    """Build the collect test fixture."""
    return pytester.runpytest(
        "-p",
        "evalspec.plugin",
        "--collect-only",
        "-q",
        "--evalspec-repo-root",
        str(pytester.path),
        "test_cases.py::test_eval",
        *extra,
    )


def test_help_describes_eval_search_paths(pytester: object) -> None:
    """Verify the eval-paths option help names the default search paths."""
    result = pytester.runpytest("-p", "evalspec.plugin", "--help")

    output = " ".join(result.stdout.str().split())
    assert result.ret == 0
    assert "eval.md / *.eval.md" in output
    assert "skills, tests, evals, benchmarks" in output


def test_cross_product_of_evals_and_arms(pytester: object) -> None:
    """Verify cross product of evals and arms."""
    _make_project(pytester)

    out = _collect(pytester).stdout.str()

    for node_id in (
        "test_eval[myskill-alpha-baseline]",
        "test_eval[myskill-alpha-trial]",
        "test_eval[myskill-beta-baseline]",
        "test_eval[myskill-beta-trial]",
    ):
        assert node_id in out
    assert out.count("test_eval[") == 4  # 2 evals × 2 arms


def test_plugin_self_registers_cases_without_positional(
    pytester: object, monkeypatch: object
) -> None:
    """Verify plugin self registers cases without positional."""
    # `make evals` passes no positional path; the plugin must inject its cases file
    # so they still collect (and so a -k nodeid can't union-collect test_eval).
    _make_project(pytester)
    monkeypatch.setattr(plugin, "_CASES", pytester.path / "test_cases.py")

    result = pytester.runpytest(
        "-p",
        "evalspec.plugin",
        "--collect-only",
        "-q",
        "--evalspec-repo-root",
        str(pytester.path),
        # deliberately no positional target
    )

    out = result.stdout.str()
    assert "test_eval[myskill-alpha-baseline]" in out


def test_explicit_positional_is_respected(pytester: object, monkeypatch: object) -> None:
    """Verify explicit positional is respected."""
    # When the user passes their own target, the plugin must NOT override it.
    _make_project(pytester)
    monkeypatch.setattr(plugin, "_CASES", pytester.path / "should_not_be_used.py")

    result = pytester.runpytest(
        "-p",
        "evalspec.plugin",
        "--collect-only",
        "-q",
        "--evalspec-repo-root",
        str(pytester.path),
        "test_cases.py::test_eval",
    )

    assert result.ret == 0
    assert "test_eval[myskill-alpha-baseline]" in result.stdout.str()


def test_models_flag_sweeps_arms(pytester: object) -> None:
    """Verify models flag sweeps arms."""
    # --evalspec-models is a SWEEP, not a filter: it replaces the set's arms with one
    # arm per value (named by it), inheriting the set-level harness.
    _make_project(pytester)

    out = _collect(pytester, "--evalspec-models", "sonnet,opus").stdout.str()

    assert out.count("test_eval[") == 4  # 2 evals × 2 swept arms
    assert "test_eval[myskill-alpha-sonnet]" in out
    assert "test_eval[myskill-alpha-opus]" in out


def test_malformed_schema_fails_collection(pytester: object) -> None:
    """Verify malformed schema fails collection."""
    (pytester.path / "pyproject.toml").write_text(ARMS_TOML)
    evals = pytester.path / "skills" / "myskill" / "evals" / "myskill"
    evals.mkdir(parents=True)
    # an unknown `id` frontmatter key violates the self-contained eval schema
    (evals / "bad.eval.md").write_text(
        "---\nid: bad\n---\n\n## Prompt\n\np\n\n## Assertions\n\n- [ ] a\n"
    )
    pytester.makepyfile(test_cases=DUMMY_CASES)

    result = _collect(pytester)

    assert result.ret != 0


def test_bad_set_fails_collection(pytester: object) -> None:
    """Verify bad set fails collection."""
    # A set arm with an unknown harness must fail collection with a UsageError
    # (wrapped SchemaError), not a silent empty roster.
    bad_set = (
        '[tool.evalspec]\ndefault-set = "default"\n'
        '[tool.evalspec.sets.default]\nmodel = "sonnet"\n'
        'arms = [{name="x", harness="nope"}]\n'
    )
    _make_project(pytester, arms_toml=bad_set)

    result = _collect(pytester)

    assert result.ret != 0
    out = result.stderr.str() + result.stdout.str()
    assert "nope" in out


class _SetConfig:
    """Config stub for resolved_run_set: repo root + the eval-set CLI options.

    Backs the unit-level resolved_run_set tests (no pytester collect) that drive the
    pyproject read + resolve_set wiring by value.
    """

    def __init__(
        self: object,
        repo_root: object,
        *,
        set_name: object = None,
        config: object = None,
        model: object = None,
        harness: object = None,
        effort_level: object = None,
        env: object = None,
        models: object = None,
    ) -> None:
        """Initialize the instance."""
        self.rootpath = repo_root
        self._root, self._opts = (
            repo_root,
            {
                "evalspec_repo_root": str(repo_root),
                "evalspec_set": set_name,
                "evalspec_config": config,
                "evalspec_model": model,
                "evalspec_harness": harness,
                "evalspec_effort": effort_level,
                "evalspec_env": env or [],
                "evalspec_models": models,
            },
        )

    def getoption(self: object, name: object) -> object:
        """Getoption."""
        return self._opts.get(name)


def _write_sets_pyproject(tmp_path: object) -> None:
    """Write sets pyproject."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.evalspec]\n"
        'default-set = "default"\n'
        "[tool.evalspec.sets.default]\n"
        'harness = "claude-code"\n'  # set-level default → sweep arms inherit a concrete harness
        'model = "sonnet"\n'
        'baseline = "baseline"\n'
        'arms = [{name="baseline"}, {name="trial"}]\n'
    )


def test_resolved_run_set_reads_pyproject(tmp_path: object) -> None:
    """Verify resolved run set reads pyproject."""
    _write_sets_pyproject(tmp_path)
    run_set = plugin.resolved_run_set(_SetConfig(tmp_path))
    assert [arm.name for arm in run_set.arms] == ["baseline", "trial"]
    assert run_set.baseline == "baseline"


def test_resolved_run_set_preserves_harness_args(tmp_path: object) -> None:
    """Verify resolved run set preserves harness args."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.evalspec]\n"
        'default-set = "default"\n'
        "[tool.evalspec.sets.default]\n"
        'harness = "claude-code"\n'
        'model = "sonnet"\n'
        'harness_args = ["--set-flag"]\n'
        'baseline = "baseline"\n'
        "arms = [\n"
        '  {name="baseline"},\n'
        '  {name="trial", harness_args=["--plugin-dir", "/project"]},\n'
        "]\n"
    )

    run_set = plugin.resolved_run_set(_SetConfig(tmp_path))

    assert run_set.arms[0].harness_args == ["--set-flag"]
    assert run_set.arms[1].harness_args == ["--set-flag", "--plugin-dir", "/project"]


def test_resolved_run_set_set_model_default_not_clobbered(tmp_path: object) -> None:
    """Verify resolved run set set model default not clobbered."""
    # A non-sonnet set default must survive a plain run (no --evalspec-model passed).
    (tmp_path / "pyproject.toml").write_text(
        '[tool.evalspec]\ndefault-set = "default"\n'
        '[tool.evalspec.sets.default]\nharness = "claude-code"\n'
        'model = "opus"\nbaseline = "b"\narms = [{name="b"}]\n'
    )
    run_set = plugin.resolved_run_set(_SetConfig(tmp_path))  # evalspec_model defaults to None
    assert run_set.arms[0].model == "opus"


def test_resolved_run_set_models_sweep(tmp_path: object) -> None:
    """Verify resolved run set models sweep."""
    _write_sets_pyproject(tmp_path)

    run_set = plugin.resolved_run_set(_SetConfig(tmp_path, models="sonnet,opus"))

    assert [arm.name for arm in run_set.arms] == ["sonnet", "opus"]
    assert run_set.baseline == "sonnet"
    assert all(arm.harness == "claude-code" for arm in run_set.arms)  # inherited, never None


def test_resolved_run_set_unknown_set_raises_usageerror(tmp_path: object) -> None:
    """Verify resolved run set unknown set raises for usageerror."""
    _write_sets_pyproject(tmp_path)
    with pytest.raises(pytest.UsageError, match="no eval set named"):
        plugin.resolved_run_set(_SetConfig(tmp_path, set_name="ghost"))


def test_resolved_run_set_legacy_config_raises_usageerror(tmp_path: object) -> None:
    """Verify resolved run set legacy config raises for usageerror."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.evalspec]\nreference = "x"\n'
        '[[tool.evalspec.arms]]\nname="x"\nharness="claude-code"\nmodel="opus"\n'
    )
    with pytest.raises(pytest.UsageError, match="eval set"):
        plugin.resolved_run_set(_SetConfig(tmp_path))


def test_resolved_run_set_missing_scratch_config_raises_usageerror(
    tmp_path: object,
) -> None:
    """Verify resolved run set missing scratch config raises for usageerror."""
    _write_sets_pyproject(tmp_path)

    with pytest.raises(pytest.UsageError, match="--evalspec-config"):
        plugin.resolved_run_set(_SetConfig(tmp_path, config=str(tmp_path / "missing.toml")))


def test_resolved_run_set_malformed_scratch_config_raises_usageerror(
    tmp_path: object,
) -> None:
    """Verify resolved run set malformed scratch config raises for usageerror."""
    _write_sets_pyproject(tmp_path)
    scratch = tmp_path / "scratch.toml"
    scratch.write_text("[tool.evalspec\n")

    with pytest.raises(pytest.UsageError, match="--evalspec-config"):
        plugin.resolved_run_set(_SetConfig(tmp_path, config=str(scratch)))


def test_resolved_run_set_invalid_utf8_scratch_config_raises_usageerror(
    tmp_path: object,
) -> None:
    """Verify resolved run set invalid utf8 scratch config raises for usageerror."""
    _write_sets_pyproject(tmp_path)
    scratch = tmp_path / "scratch.toml"
    scratch.write_bytes(b"\xff")

    with pytest.raises(pytest.UsageError, match="--evalspec-config"):
        plugin.resolved_run_set(_SetConfig(tmp_path, config=str(scratch)))


def test_session_run_set_resolves_when_cases_exist(
    tmp_path: object, monkeypatch: object
) -> None:
    """Verify session_run_set resolves the set (with its sandbox) when a run has cases."""
    _write_sets_pyproject(tmp_path)  # sandbox default = microsandbox
    monkeypatch.setattr(plugin, "discover_eval_cases", lambda root, paths: [object()])

    run_set = plugin.session_run_set(_FakeConfig(tmp_path))

    assert run_set is not None
    assert run_set.sandbox == "microsandbox"  # feeds the session sandbox preflight


def test_session_run_set_none_for_trigger_only(
    tmp_path: object, monkeypatch: object
) -> None:
    """Verify a trigger-only run (no cases, no sets table) degrades to None, never raises."""
    (tmp_path / "pyproject.toml").write_text("[tool.other]\nx = 1\n")
    monkeypatch.setattr(plugin, "discover_eval_cases", lambda root, paths: [])

    # resolved_run_set here WOULD raise UsageError (no sets table); the guard must not.
    assert plugin.session_run_set(_FakeConfig(tmp_path)) is None


def test_run_set_when_needed_degrades_without_resolving(tmp_path: object) -> None:
    """Verify needs_set=False returns None even where resolving would raise (no sets table)."""
    (tmp_path / "pyproject.toml").write_text("[tool.other]\nx = 1\n")

    assert plugin.run_set_when_needed(_FakeConfig(tmp_path), needs_set=False) is None


def test_preflight_session_sandbox_uses_resolved_set_backend(monkeypatch: object) -> None:
    """Verify the resolved set's .sandbox — not DEFAULT_SANDBOX — drives sandbox.preflight."""
    from evalspec import cases
    from evalspec.arms import Arm, Set

    fake_set = Set(
        "s", [Arm("a", "claude-code", "opus")], baseline=None, sandbox="custombackend"
    )
    monkeypatch.setattr(cases, "session_run_set", lambda config: fake_set)
    monkeypatch.setattr(cases, "resolve_sandbox", lambda name: f"backend:{name}")
    seen = {}
    monkeypatch.setattr(
        cases.sandbox, "preflight", lambda backend=None: seen.__setitem__("backend", backend)
    )

    cases.preflight_session_sandbox(object())  # config unused — session_run_set stubbed

    assert seen["backend"] == "backend:custombackend"


def test_preflight_session_sandbox_trigger_only_uses_default(monkeypatch: object) -> None:
    """Verify a trigger-only run passes None so preflight resolves the default backend."""
    from evalspec import cases

    monkeypatch.setattr(cases, "session_run_set", lambda config: None)
    seen = {}
    monkeypatch.setattr(
        cases.sandbox, "preflight", lambda backend=None: seen.setdefault("backend", backend)
    )

    cases.preflight_session_sandbox(object())

    assert seen["backend"] is None  # None => preflight resolves DEFAULT_SANDBOX itself


def test_eval_threads_resolved_set_sandbox_into_run_eval_arm(
    pytester: object, monkeypatch: object
) -> None:
    """Verify test_eval passes the resolved set's .sandbox as run_eval_arm(sandbox_name=...)."""
    import types

    from evalspec import binder, cases, sandbox

    _make_project(pytester)  # set with sandbox default = microsandbox
    captured: list = []

    def capture(*args: object, **kwargs: object) -> object:
        """Capture."""
        captured.append(kwargs["sandbox_name"])
        return types.SimpleNamespace(errored=False)

    # Neutralize the environment-dependent preflights so the body reaches run_eval_arm.
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None: None)
    monkeypatch.setattr(binder, "preflight_gemini_key", lambda: None)
    monkeypatch.setattr(cases, "preflight_judge_binary", lambda config: None)
    monkeypatch.setattr(cases, "seed_room", lambda *args, **kwargs: {})
    monkeypatch.setattr(cases, "run_eval_arm", capture)

    result = pytester.runpytest(
        "-p",
        "evalspec.plugin",
        "--evalspec-repo-root",
        str(pytester.path),
        "-k",
        "test_eval",
    )

    assert result.ret == 0
    assert captured  # the parametrized body actually ran
    assert set(captured) == {"microsandbox"}  # every arm got the resolved set's sandbox


def test_count_two_parametrizes_sample_index(pytester: object, tmp_path: object) -> None:
    """Verify count two parametrizes sample index."""
    # --count 2 must yield 8 items (2 evals × 2 arms × 2 samples) AND the
    # sample_index fixture must resolve to BOTH 0 and 1 — not just `in (0, 1)`,
    # which would silently pass if the fixture regressed to returning 0 for
    # every item (the exact overwrite bug this PR fixes). Each body writes its
    # observed sample_index to a shared file; after the run, both values must
    # appear with the right count.
    _make_project(pytester)
    log = tmp_path / "samples.log"
    pytester.makepyfile(
        test_cases=f"""
LOG = r"{log}"

def test_eval(eval_arm, sample_index):
    with open(LOG, "a") as sample_log:
        sample_log.write(f"{{sample_index}}\\n")
"""
    )

    result = pytester.runpytest(
        "-p",
        "evalspec.plugin",
        "-p",
        "pytest_repeat",
        "--evalspec-repo-root",
        str(pytester.path),
        "--count",
        "2",
        "test_cases.py::test_eval",
    )

    result.assert_outcomes(passed=8)
    observed = [int(value) for value in log.read_text().split() if value]
    assert sorted(observed) == [0, 0, 0, 0, 1, 1, 1, 1]


class _FakeConfig:
    """Fake config for the pytest_sessionfinish / pytest_terminal_summary tests.

    Those hooks are the manifest + benchmark glue; this stub supplies the option
    surface they read.
    """

    def __init__(self: object, repo_root: object, fail_under: object = None) -> None:
        """Initialize the instance."""
        self.rootpath = repo_root
        self._repo_root = str(repo_root)
        self._fail_under = fail_under
        self.stash = pytest.Stash()

    def getoption(self: object, name: object) -> object:
        """Getoption."""
        if name == "evalspec_repo_root":
            return self._repo_root
        return {
            "evalspec_model": None,
            "evalspec_set": None,
            "evalspec_config": None,
            "evalspec_harness": None,
            "evalspec_effort": None,
            "evalspec_env": [],
            "evalspec_models": None,
            "evalspec_judge_harness": None,
            "evalspec_judge_model": None,
            "evalspec_judge_effort": None,
            "evalspec_judge_timeout": None,
            "evalspec_judge_harness_arg": [],
            "evalspec_judge_env": [],
            "evalspec_fail_under": self._fail_under,
        }.get(name)


class _FakeTR:
    """Provide a fake t r for tests."""

    def __init__(self: object) -> None:
        """Initialize the instance."""
        self.events = []
        self.lines = []

    def write_sep(self: object, separator: object, title: object) -> None:
        """Write separator."""
        self.events.append(("separator", title))

    def line(self: object, message: object) -> None:
        """Line."""
        self.events.append(("line", message))
        self.lines.append(message)


class _FakeSession:
    """Provide a fake session for tests."""

    def __init__(self: object, config: object) -> None:
        """Initialize the instance."""
        self.config = config
        self.exitstatus = 0


class _StubAgent:
    """Store stub agent data."""

    id = "claude-code"
    capabilities = AgentCapabilities(
        efforts=("low", "medium", "high"),
        multi_turn=True,
        token_split=True,
    )

    def version(self: object) -> str:
        """Version."""
        return "9.9.9"


def _finish_and_summarize(tmp_path: object, monkeypatch: object = None) -> object:
    """Drive the post-run pipeline the way pytest does.

    `sessionfinish` builds the artifacts; `terminal_summary` only prints them.
    """
    config = _FakeConfig(tmp_path)
    session = _FakeSession(config)
    plugin.pytest_sessionfinish(session, 0)
    terminal_reporter = _FakeTR()
    plugin.pytest_terminal_summary(terminal_reporter, 0, config)
    return session, terminal_reporter


def test_terminal_summary_prints_delta(tmp_path: object, monkeypatch: object) -> None:
    """Verify terminal summary prints delta."""
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=2, total=2)
    seed_arm(skill_results_dir, "alpha", "baseline", passes=0, total=2)

    _, terminal_reporter = _finish_and_summarize(tmp_path)

    assert ("separator", "evalspec benchmark") in terminal_reporter.events
    lines = [message for kind, message in terminal_reporter.events if kind == "line"]
    assert any("iteration_01" in message and "+100pp" in message for message in lines)
    assert (skill_results_dir.parent.parent / "benchmark.md").is_file()


def test_terminal_summary_multi_skill_single_header(tmp_path: object, monkeypatch: object) -> None:
    """Verify terminal summary multi skill single header."""
    # Two skills with eval-* children pool into ONE run-level table and one run-level
    # delta line, under a single header (rows sorted: archive before ingest).
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skills = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"

    archive_dir = skills / "archive"
    seed_arm(archive_dir, "alpha", "trial", passes=2, total=2)
    seed_arm(archive_dir, "alpha", "baseline", passes=0, total=2)

    ingest_dir = skills / "ingest"
    seed_arm(ingest_dir, "beta", "trial", passes=1, total=2)
    seed_arm(ingest_dir, "beta", "baseline", passes=1, total=2)

    _, terminal_reporter = _finish_and_summarize(tmp_path)

    assert terminal_reporter.events.count(("separator", "evalspec benchmark")) == 1

    lines = [message for kind, message in terminal_reporter.events if kind == "line"]
    assert len(lines) == 1

    iteration_root = skills.parent
    benchmark = json.loads((iteration_root / "benchmark.json").read_text())
    assert benchmark["label"] == "iteration_01"
    markdown = (iteration_root / "benchmark.md").read_text()
    assert "| archive/alpha |" in markdown
    assert "| ingest/beta |" in markdown
    assert "| All evals |" in markdown


def test_build_manifest_assembles_shape_by_value() -> None:
    """Verify build manifest assembles shape by value."""
    # The pure assembler is testable by value (no uuid/clock/git IO) — the shell
    # injects identity. Pins the spread of cfg and the hash, which the IO-bound
    # sessionfinish test below can only presence-check.
    cfg = {
        "agent": "claude-code",
        "agent_version": "9.9.9",
        "model": "sonnet",
        "judge": {
            "harness": "claude-code",
            "model": "sonnet",
            "effort_level": "medium",
            "timeout": 300,
            "env": {},
            "harness_args": [],
        },
        "eval_effort": "medium",
    }

    manifest = plugin.build_manifest(
        run_id="r" * 32,
        started_at="2026-06-13T00:00:00+00:00",
        commit="abc123",
        iteration="iteration_07",
        cfg=cfg,
        token_split=True,
    )

    assert manifest["format_version"] == 1
    assert manifest["run_id"] == "r" * 32
    assert manifest["commit"] == "abc123"
    assert manifest["started_at"] == "2026-06-13T00:00:00+00:00"
    assert manifest["iteration"] == "iteration_07"
    assert manifest["token_split"] is True
    assert manifest["model"] == "sonnet"  # cfg spread in
    assert len(manifest["config_hash"]) == 12
    assert manifest["judge"]["harness"] == "claude-code"  # nested judge spread in whole
    assert manifest["judge"]["model"] == "sonnet"


def test_build_manifest_config_hash_is_order_independent() -> None:
    """Verify build manifest config hash is order independent."""
    # config_hash hashes cfg with sort_keys, so two cfgs that differ only in key
    # order (and in the non-cfg identity fields) hash identically.
    cfg = {
        "agent": "claude-code",
        "agent_version": None,
        "model": "sonnet",
        "judge": {
            "harness": "claude-code",
            "model": "sonnet",
            "effort_level": "medium",
            "timeout": 300,
            "env": {},
            "harness_args": [],
        },
        "eval_effort": "medium",
    }
    reordered = dict(reversed(list(cfg.items())))

    manifest_a = plugin.build_manifest(
        run_id="a" * 32,
        started_at="t1",
        commit="c1",
        iteration="iteration_01",
        cfg=cfg,
        token_split=True,
    )
    manifest_b = plugin.build_manifest(
        run_id="b" * 32,
        started_at="t2",
        commit="c2",
        iteration="iteration_99",
        cfg=reordered,
        token_split=False,
    )

    assert manifest_a["config_hash"] == manifest_b["config_hash"]


def test_sessionfinish_writes_run_manifest(tmp_path: object, monkeypatch: object) -> None:
    """Verify sessionfinish writes run manifest."""
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
[tool.evalspec]
default-set = "default"

[tool.evalspec.sets.default]
harness = "claude-code"
model = "sonnet"
baseline = "baseline"
harness_args = ["--set-flag"]
arms = [
  {name="baseline"},
  {name="trial", model="opus", harness_args=["--plugin-dir", "/project"]},
]
"""
        )
    )
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=1, total=1)
    seed_arm(skill_results_dir, "alpha", "baseline", passes=0, total=1)

    _finish_and_summarize(tmp_path)

    meta = json.loads((skill_results_dir.parent.parent / "meta.json").read_text())
    assert meta["format_version"] == 1
    assert len(meta["run_id"]) == 32  # uuid4 hex
    assert len(meta["config_hash"]) == 12
    assert meta["iteration"] == "iteration_01"
    assert meta["agent"] == "claude-code"
    assert meta["agent_version"] == "9.9.9"
    assert meta["set"] == "default"
    assert [arm["name"] for arm in meta["arms"]] == ["baseline", "trial"]
    assert meta["arms"][0]["harness_args"] == ["--set-flag"]
    assert meta["arms"][1]["harness_args"] == ["--set-flag", "--plugin-dir", "/project"]
    assert meta["arms"][1]["model"] == "opus"  # trial arm declared its own model
    assert "judge_model" not in meta
    assert meta["judge"]["harness"] == "claude-code"
    assert meta["judge"]["model"] == "sonnet"
    assert meta["judge"]["effort"] == "medium"
    assert meta["judge"]["timeout"] == 300
    assert meta["judge"]["env"] == {}
    assert meta["judge"]["harness_args"] == []
    assert meta["token_split"] is True
    assert "commit" in meta
    assert "started_at" in meta
    assert "evalspec_version" in meta

    benchmark = json.loads((skill_results_dir.parent.parent / "benchmark.json").read_text())
    assert benchmark["arms"]["baseline"]["harness_args"] == ["--set-flag"]
    assert benchmark["arms"]["trial"]["harness_args"] == [
        "--set-flag",
        "--plugin-dir",
        "/project",
    ]
    benchmark_markdown = (skill_results_dir.parent.parent / "benchmark.md").read_text()
    assert "- Harness args: `--set-flag` `--plugin-dir` `/project`" in benchmark_markdown


def test_sessionfinish_writes_nested_judge_object(tmp_path: object, monkeypatch: object) -> None:
    """Verify sessionfinish writes nested judge object."""
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    # harness=claude-code, model=sonnet/opus arms
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=1, total=1)
    seed_arm(skill_results_dir, "alpha", "baseline", passes=0, total=1)

    _finish_and_summarize(tmp_path)

    meta = json.loads((skill_results_dir.parent.parent / "meta.json").read_text())
    assert "judge_model" not in meta
    assert meta["judge"]["harness"] == "claude-code"
    assert meta["judge"]["model"] == "sonnet"
    assert meta["judge"]["effort"] == "medium"
    assert meta["judge"]["timeout"] == 300
    assert meta["judge"]["env"] == {}
    assert meta["judge"]["harness_args"] == []
    assert "warnings" not in meta["judge"]


def test_sessionfinish_redacts_secret_shaped_judge_env(
    tmp_path: object, monkeypatch: object
) -> None:
    """Verify sessionfinish redacts secret shaped judge env."""
    # Every other judge test here uses JudgeConfig.env == {}, so redact_env({}) == {}
    # trivially passes even if the report.redact_env(...) call around the judge env
    # were dropped. Use a secret-shaped key (matches report._SECRET_KEY) so this test
    # only passes if the value is actually masked before it hits meta.json.
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(JUDGE_ENV_TOML)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=1, total=1)
    seed_arm(skill_results_dir, "alpha", "baseline", passes=0, total=1)

    _finish_and_summarize(tmp_path)

    raw_text = (skill_results_dir.parent.parent / "meta.json").read_text()
    meta = json.loads(raw_text)
    assert meta["judge"]["env"]["OPENAI_API_KEY"] == "***"
    assert "sk-live-supersecret123" not in raw_text


def test_manifest_survives_missing_agent_credential(tmp_path: object, monkeypatch: object) -> None:
    """Verify manifest survives missing agent credential."""

    # No credential / missing CLI must not kill the manifest: identity fields go
    # null, the run configuration is still recorded.
    def boom() -> NoReturn:
        """Boom."""
        raise RuntimeError("no credential")

    monkeypatch.setattr(plugin, "make_agent", boom)
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=1, total=1)

    _finish_and_summarize(tmp_path)

    meta = json.loads((skill_results_dir.parent.parent / "meta.json").read_text())
    assert meta["agent_version"] is None
    assert meta["token_split"] is None
    assert meta["set"] == "default"


def test_terminal_summary_noop_without_artifacts(tmp_path: object, monkeypatch: object) -> None:
    """Verify terminal summary noop without artifacts."""
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    workspace.set_current_iteration("iteration_01")

    _, terminal_reporter = _finish_and_summarize(tmp_path)

    assert terminal_reporter.events == []


def test_unknown_agent_flag_fails_at_startup(pytester: object) -> None:
    """Verify unknown agent flag fails at startup."""
    _make_project(pytester)

    result = _collect(pytester, "--evalspec-agent", "not-a-harness")

    assert result.ret != 0
    out = result.stderr.str() + result.stdout.str()
    assert "--evalspec-agent" in out
    assert "not-a-harness" in out


def test_agent_flag_beats_env(pytester: object, monkeypatch: object) -> None:
    """Verify agent flag beats env."""
    monkeypatch.setenv("EVALSPEC_AGENT", "opencode")
    _make_project(pytester)

    result = _collect(pytester, "--evalspec-agent", "claude-code")

    assert result.ret == 0


def test_judge_model_flag_is_accepted(pytester: object) -> None:
    """Verify judge model flag is accepted."""
    _make_project(pytester)

    result = _collect(pytester, "--evalspec-judge-model", "haiku")

    assert result.ret == 0


def test_judge_harness_flag_is_accepted(pytester: object) -> None:
    """Verify judge harness flag is accepted."""
    _make_project(pytester)
    result = _collect(
        pytester, "--evalspec-judge-harness", "codex", "--evalspec-judge-model", "gpt-5.5"
    )

    assert result.ret == 0


def test_judge_effort_and_timeout_flags_are_accepted(pytester: object) -> None:
    """Verify judge effort and timeout flags are accepted."""
    _make_project(pytester)
    result = _collect(
        pytester, "--evalspec-judge-effort", "high", "--evalspec-judge-timeout", "120"
    )

    assert result.ret == 0


def test_judge_harness_arg_flag_is_repeatable(pytester: object) -> None:
    """Verify judge harness arg flag is repeatable."""
    # `=` form (not two bare tokens): argparse's `action="append"` treats a bare
    # token starting with `--` as a new option, not this option's value — a stock
    # argparse gotcha, unrelated to this flag's own parsing.
    _make_project(pytester)
    result = _collect(
        pytester,
        "--evalspec-judge-harness-arg=--plugin-dir",
        "--evalspec-judge-harness-arg=/project",
    )

    assert result.ret == 0


def test_judge_env_flag_is_repeatable(pytester: object) -> None:
    """Verify judge env flag is repeatable."""
    _make_project(pytester)
    result = _collect(pytester, "--evalspec-judge-env", "A=1", "--evalspec-judge-env", "B=2")

    assert result.ret == 0


def test_eval_test_body_resolves_judge_config_fixture_without_error(pytester: object) -> None:
    """Verify eval test body resolves judge config fixture without error."""
    # A real (non --collect-only) run still needs a microVM to get past sandbox
    # preflight, so this only proves collection succeeds with the fixture renamed —
    # full execution is covered by tests/test_execution.py's run_eval_arm tests.
    _make_project(pytester)
    result = _collect(pytester)

    assert result.ret == 0


def test_judge_preflight_fixture_raises_when_binary_missing(
    pytester: object, monkeypatch: object
) -> None:
    """Verify judge preflight fixture raises when binary missing."""
    import shutil

    from evalspec import sandbox

    _make_project(pytester)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    # No-op the sandbox preflight so it cannot fail first for unrelated reasons (no
    # microVM/credentials) and mask the judge-binary assertion below.
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None: None)
    # Deliberately NO positional target here (unlike `_collect`'s "test_cases.py::
    # test_eval"): `pytest_configure` only self-registers the real `evalspec/cases.py`
    # (whose `judge_config` fixture runs the binary preflight under test) when
    # `config.args_source` isn't `ARGS` — i.e. when no explicit positional is given.
    # Passing "test_cases.py::test_eval" would instead collect _make_project's dummy
    # `test_cases.py` stub (`def test_eval(eval_arm): pass`), which never requests
    # `judge_config` and so could never exercise this preflight at all — see
    # `test_plugin_self_registers_cases_without_positional` for the same mechanic.
    # `-k test_eval` keeps this to the real (parametrized) test_eval items.
    result = pytester.runpytest(
        "-p",
        "evalspec.plugin",
        "--evalspec-repo-root",
        str(pytester.path),
        "-k",
        "test_eval",
    )
    assert result.ret != 0
    out = result.stdout.str() + result.stderr.str()
    assert "not found on PATH" in out


def test_gemini_key_preflight_fixture_raises_when_missing(
    pytester: object, monkeypatch: object
) -> None:
    """Verify the judge_config fixture fails fast on a missing GEMINI_API_KEY."""
    import shutil

    from evalspec import sandbox

    _make_project(pytester)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")  # binary IS present
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None: None)
    # A dev-machine repo-root .env can otherwise repopulate GEMINI_API_KEY inside the
    # inner run's own pytest_configure (plugin.py calls load_dotenv() unconditionally) —
    # no-op it so this test is deterministic regardless of local .env contents.
    monkeypatch.setattr(plugin, "load_dotenv", lambda *args: None)

    result = pytester.runpytest(
        "-p",
        "evalspec.plugin",
        "--evalspec-repo-root",
        str(pytester.path),
        "-k",
        "test_eval",
    )
    assert result.ret != 0
    out = result.stdout.str() + result.stderr.str()
    assert "GEMINI_API_KEY" in out


def test_gemini_key_preflight_skipped_under_collect_only(
    pytester: object, monkeypatch: object
) -> None:
    """Verify --collect-only never triggers the GEMINI_API_KEY preflight."""
    from evalspec import sandbox

    _make_project(pytester)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None: None)

    result = pytester.runpytest(
        "-p",
        "evalspec.plugin",
        "--collect-only",
        "--evalspec-repo-root",
        str(pytester.path),
    )
    assert result.ret == 0


def test_judge_model_flag_no_longer_shadows_pyproject_default_when_unset(pytester: object) -> None:
    """Verify judge model flag no longer shadows pyproject default when unset."""
    # Regression guard for the precedence bug: --evalspec-judge-model must default to
    # None so an unset flag never overrides [tool.evalspec.judge] model.
    project_toml = (
        ARMS_TOML
        + """
[tool.evalspec.judge]
harness = "codex"
model = "gpt-5.5"
"""
    )
    _make_project(pytester, arms_toml=project_toml)
    result = _collect(pytester)  # no --evalspec-judge-model passed
    assert result.ret == 0


def test_unsupported_judge_harness_fails_at_collection(pytester: object) -> None:
    """Verify unsupported judge harness fails at collection."""
    project_toml = (
        ARMS_TOML
        + """
[tool.evalspec.judge]
harness = "cursor"
"""
    )
    _make_project(pytester, arms_toml=project_toml)
    result = _collect(pytester)
    assert result.ret != 0
    out = result.stderr.str() + result.stdout.str()
    assert "not a supported judge harness" in out


def test_non_dict_pyproject_judge_table_fails_loudly(pytester: object) -> None:
    """Verify non dict pyproject judge table fails loudly."""
    # Regression guard: a present-but-non-dict [tool.evalspec.judge] (e.g. `judge =
    # "codex"` from a fat-fingered TOML edit) must raise, not silently coerce to
    # None and fall through to defaults. `judge` is inserted into the *existing*
    # [tool.evalspec] table (not a re-opened header) — TOML forbids declaring the
    # same table twice.
    project_toml = ARMS_TOML.replace(
        'default-set = "default"',
        'default-set = "default"\njudge = "codex"',
    )
    _make_project(pytester, arms_toml=project_toml)
    result = _collect(pytester)
    assert result.ret != 0
    out = result.stderr.str() + result.stdout.str()
    assert "[tool.evalspec.judge] must be a table" in out


def test_non_dict_scratch_judge_table_fails_loudly(pytester: object) -> None:
    """Verify non dict scratch judge table fails loudly."""
    # Same regression guard, but for a scratch --evalspec-config file's
    # [tool.evalspec.judge] — a distinct code path (`_read_scratch_evalspec_table` +
    # the scratch branch in `resolved_judge_config`) with its own error message.
    _make_project(pytester)
    scratch = pytester.path / "scratch.toml"
    scratch.write_text('[tool.evalspec]\njudge = "codex"\n')

    result = _collect(pytester, "--evalspec-config", str(scratch))

    assert result.ret != 0
    out = result.stderr.str() + result.stdout.str()
    assert "[tool.evalspec.judge] must be a table" in out
    assert "--evalspec-config" in out


def test_sessionfinish_writes_index_jsonl(tmp_path: object, monkeypatch: object) -> None:
    """Verify sessionfinish writes index jsonl."""
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"
    seed_arm(skill_results_dir / "archive", "alpha", "trial", passes=1, total=1)
    seed_arm(skill_results_dir / "archive", "alpha", "baseline", passes=0, total=1)

    _finish_and_summarize(tmp_path)

    lines = [
        json.loads(line)
        for line in (skill_results_dir.parent / "index.jsonl").read_text().splitlines()
    ]
    assert {record["skill"] for record in lines} == {"archive"}
    assert sum(1 for record in lines if record["kind"] == "eval") == 2


def test_fail_under_sets_exit_status(tmp_path: object, monkeypatch: object) -> None:
    """Verify fail under sets exit status."""
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=0, total=2)  # 0%
    seed_arm(skill_results_dir, "alpha", "baseline", passes=2, total=2)  # 100% → delta -100pp

    config = _FakeConfig(tmp_path, fail_under=0.0)
    session = _FakeSession(config)
    plugin.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 1
    terminal_reporter = _FakeTR()
    plugin.pytest_terminal_summary(terminal_reporter, 1, config)
    assert any("fail-under" in line for line in terminal_reporter.lines)


def test_fail_under_quiet_when_met(tmp_path: object, monkeypatch: object) -> None:
    """Verify fail under quiet when met."""
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=2, total=2)  # 100%
    seed_arm(skill_results_dir, "alpha", "baseline", passes=0, total=2)  # 0% → delta +100pp

    config = _FakeConfig(tmp_path, fail_under=0.0)
    session = _FakeSession(config)
    plugin.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 0


def test_fail_under_skipped_without_reference(tmp_path: object, monkeypatch: object) -> None:
    """Verify fail under skipped without reference."""
    # A set with no `baseline` → absolute scores, no Δ to gate; the fail-under
    # threshold is a no-op rather than failing the run.
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(
        '[tool.evalspec]\ndefault-set = "default"\n'
        '[tool.evalspec.sets.default]\nharness = "claude-code"\nmodel = "sonnet"\n'
        'arms = [{name="trial-opus"}, {name="trial-sonnet"}]\n'
    )
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial-opus", passes=0, total=2)
    seed_arm(skill_results_dir, "alpha", "trial-sonnet", passes=0, total=2)

    config = _FakeConfig(tmp_path, fail_under=0.0)
    session = _FakeSession(config)
    plugin.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 0


def test_fail_under_isolates_regressing_group(tmp_path: object, monkeypatch: object) -> None:
    """Verify a single regressing group trips the per-group gate even when the pool is up."""
    # Group A dominates by sample weight (trial +100pp over 4 samples) while group B
    # regresses (trial -100pp). The run-level pooled trial Δ is +33pp, so a pooled gate
    # would NOT fire — only a per-group gate catches group B.
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skills = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"
    for sample_index in range(4):
        seed_arm(skills / "archive", "alpha", "baseline", passes=0, total=1, sample=sample_index)
        seed_arm(skills / "archive", "alpha", "trial", passes=1, total=1, sample=sample_index)
    for sample_index in range(2):
        seed_arm(skills / "ingest", "beta", "baseline", passes=1, total=1, sample=sample_index)
        seed_arm(skills / "ingest", "beta", "trial", passes=0, total=1, sample=sample_index)

    config = _FakeConfig(tmp_path, fail_under=0.0)
    session = _FakeSession(config)
    plugin.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 1
    terminal_reporter = _FakeTR()
    plugin.pytest_terminal_summary(terminal_reporter, 1, config)
    assert any("FAIL fail-under: ingest/trial" in line for line in terminal_reporter.lines)
    benchmark = json.loads((skills.parent / "benchmark.json").read_text())
    assert benchmark["arms"]["trial"]["delta_pp"] > 0  # pooled Δ is positive → pooled gate misses


def test_fail_under_exempts_group_missing_baseline_arm(
    tmp_path: object, monkeypatch: object
) -> None:
    """Verify a group that never ran the baseline stays exempt from the per-group gate."""
    # Group A runs both arms with trial below threshold → it fails. Group B ran only
    # trial, so its configured baseline column has no rate (pass_rate=None), coercing
    # the group's baseline to None → no Δ to gate → exempt.
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skills = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"
    seed_arm(skills / "archive", "alpha", "baseline", passes=2, total=2)  # 100%
    seed_arm(skills / "archive", "alpha", "trial", passes=0, total=2)  # 0% → Δ -100pp
    seed_arm(skills / "ingest", "beta", "trial", passes=0, total=2)  # only trial, no baseline

    config = _FakeConfig(tmp_path, fail_under=0.0)
    session = _FakeSession(config)
    plugin.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 1
    terminal_reporter = _FakeTR()
    plugin.pytest_terminal_summary(terminal_reporter, 1, config)
    assert any("FAIL fail-under: archive/trial" in line for line in terminal_reporter.lines)
    assert not any("FAIL fail-under: ingest" in line for line in terminal_reporter.lines)


def test_sessionfinish_writes_single_run_level_benchmark(
    tmp_path: object, monkeypatch: object
) -> None:
    """Verify exactly one run-level benchmark lands at the iteration root, none per group."""
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skills = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"
    seed_arm(skills / "archive", "alpha", "trial", passes=2, total=2)
    seed_arm(skills / "archive", "alpha", "baseline", passes=1, total=2)
    seed_arm(skills / "ingest", "beta", "trial", passes=1, total=2)
    seed_arm(skills / "ingest", "beta", "baseline", passes=1, total=2)

    _finish_and_summarize(tmp_path)

    iteration_root = skills.parent
    assert (iteration_root / "benchmark.json").is_file()
    assert not (skills / "archive" / "benchmark.json").exists()
    assert not (skills / "ingest" / "benchmark.json").exists()
    benchmark = json.loads((iteration_root / "benchmark.json").read_text())
    assert benchmark["format_version"] == 2
    assert {(entry["group"], entry["eval_id"]) for entry in benchmark["roster"]} == {
        ("archive", "alpha"),
        ("ingest", "beta"),
    }


def test_binder_degraded_warns_in_terminal_summary(tmp_path: object, monkeypatch: object) -> None:
    """Verify a nonzero binder_degraded total prints a WARN line, doesn't fail the run."""
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=2, total=2, binder_degraded=3)
    seed_arm(skill_results_dir, "alpha", "baseline", passes=2, total=2)

    config = _FakeConfig(tmp_path)
    session = _FakeSession(config)
    plugin.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 0
    terminal_reporter = _FakeTR()
    plugin.pytest_terminal_summary(terminal_reporter, 0, config)
    assert any("WARN" in line and "binder" in line for line in terminal_reporter.lines)


def test_binder_degraded_quiet_when_zero(tmp_path: object, monkeypatch: object) -> None:
    """Verify no binder WARN line when nothing degraded."""
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=2, total=2)
    seed_arm(skill_results_dir, "alpha", "baseline", passes=2, total=2)

    config = _FakeConfig(tmp_path)
    session = _FakeSession(config)
    plugin.pytest_sessionfinish(session, 0)

    terminal_reporter = _FakeTR()
    plugin.pytest_terminal_summary(terminal_reporter, 0, config)
    # A bare "binder" substring check would self-collide: pytest's tmp_path embeds this
    # test's own name (which contains "binder") into the benchmark.md path the delta
    # line reports. Match the WARN line's actual shape instead.
    assert not any("WARN" in line and "binder" in line for line in terminal_reporter.lines)


# On-disk --evalspec-config judge fixtures; each is driven end-to-end through the
# real plugin hooks at collection time by the tests below.
_FIXTURES = Path(__file__).parent / "fixtures" / "judge"


def test_unsupported_judge_harness_fixture_exits_nonzero(pytester: object) -> None:
    """Verify unsupported judge harness fixture exits nonzero."""
    _make_project(pytester)
    result = pytester.runpytest(
        "-p",
        "evalspec.plugin",
        "--collect-only",
        "-q",
        "--evalspec-repo-root",
        str(pytester.path),
        "--evalspec-config",
        str(_FIXTURES / "unsupported-judge-harness.toml"),
        "test_cases.py::test_eval",
    )
    assert result.ret != 0
    assert "not a supported judge harness" in (result.stdout.str() + result.stderr.str())


def test_unset_judge_env_fixture_passes_collection_but_fails_at_judge_exec_time() -> None:
    """Verify unset judge env fixture passes collection but fails at judge exec time."""
    # Collection-time only: proves the fixture's env value is structurally valid
    # (a string) and does NOT raise until evalspec.judges.run_judge actually expands
    # it. Full end-to-end (a real arm run reaching the judge) needs live credentials
    # and a microVM — out of scope for `make test`; see tests/judges/test_judge_registry.py
    # ::test_run_judge_env_unset_var_raises_schemaerror for the unit-level proof, and
    # tests/test_arms.py for expand_env's own unset-var coverage (the same function).
    import tomllib

    from evalspec.judges import resolve_judge_config

    with (_FIXTURES / "unset-judge-env.toml").open("rb") as fixture_file:
        raw = tomllib.load(fixture_file)
    judge_table = raw["tool"]["evalspec"]["judge"]
    config = resolve_judge_config(pyproject_table=judge_table)  # no raise — structural only
    assert config.env == {"SOME_JUDGE_KEY": "$EVALSPEC_JUDGE_FIXTURE_UNSET_VAR"}

    import os

    from evalspec.judges.registry import run_judge
    from evalspec.schema import SchemaError

    os.environ.pop("EVALSPEC_JUDGE_FIXTURE_UNSET_VAR", None)
    with pytest.raises(SchemaError, match="EVALSPEC_JUDGE_FIXTURE_UNSET_VAR"):
        run_judge("prompt", config=config)
