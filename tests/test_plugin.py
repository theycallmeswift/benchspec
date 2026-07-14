"""Plugin tests via pytester — exercise the hooks in-process, no `claude -p`.

A dummy `test_eval(eval_arm)` is collected with `--collect-only`, so the body never
runs; we assert on the parametrized node ids the plugin generates. Each project writes a
`[tool.evalspec]` eval set so `resolved_run_set` has a real set to resolve.
"""

import json
import textwrap
from pathlib import Path

import pytest

from evalspec import plugin, report, workspace
from evalspec.agents.base import AgentCapabilities
from evalspec.sandbox.provenance import ImageIdentity, RuntimeProvenance, SandboxProvenance
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
    from evalspec.config.arms import Arm, Set

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

    from evalspec import cases
    from evalspec.grading import binder
    from evalspec.sandbox import sandbox

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


@pytest.fixture(autouse=True)
def _stub_judge_probe(monkeypatch: object) -> None:
    """Pin the host judge-version probe so sessionfinish tests stay hermetic.

    `_judge_meta` shells out to `<judge binary> --version`; on a dev box with the harness
    installed that is nondeterministic and non-hermetic. Pin it to a sentinel so the judge
    `actual_version` is predictable and the dedicated test can assert it flows through.
    """
    monkeypatch.setattr(plugin, "probe_judge_version", lambda harness: "judge-1.0.0")


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
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
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
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
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
        "set": "default",
        "runner": "pytest",
        "arms": [{"name": "baseline", "requested_version": "latest"}],
        "judge": {
            "harness": "claude-code",
            "model": "sonnet",
            "effort": "medium",
            "timeout": 300,
            "env": {},
            "harness_args": [],
            "actual_version": "1.2.3",
        },
        "binder": {"provider": "gemini", "model": "x", "api_path": "y"},
    }
    observed = {"baseline": {"actual_version": "1.2.3", "actual_version_status": "available"}}

    manifest = plugin.build_manifest(
        run_id="r" * 32,
        started_at="2026-06-13T00:00:00+00:00",
        commit="abc123",
        iteration="iteration_07",
        cfg=cfg,
        observed_arms=observed,
    )

    assert manifest["format_version"] == 2
    assert manifest["run_id"] == "r" * 32
    assert manifest["commit"] == "abc123"
    assert manifest["started_at"] == "2026-06-13T00:00:00+00:00"
    assert manifest["iteration"] == "iteration_07"
    assert manifest["set"] == "default"  # cfg spread in
    assert manifest["runner"] == "pytest"
    assert manifest["observed_arms"] == observed
    assert len(manifest["config_hash"]) == 12
    assert manifest["judge"]["harness"] == "claude-code"  # nested judge spread in whole
    assert manifest["binder"]["provider"] == "gemini"
    # No v1 run-level identity fields survive.
    assert "agent" not in manifest
    assert "agent_version" not in manifest
    assert "token_split" not in manifest


def test_build_manifest_config_hash_excludes_observed_arms() -> None:
    """Verify config_hash hashes only cfg, never the runtime observation."""
    # config_hash must be stable across runs of one config; folding observed_arms into it
    # would make two runs of the same config hash differently. Same cfg + different
    # observed_arms ⇒ identical config_hash.
    cfg = {
        "set": "default",
        "runner": "pytest",
        "arms": [{"name": "baseline"}],
        "judge": {"harness": "claude-code"},
        "binder": {"provider": "gemini"},
    }

    manifest_a = plugin.build_manifest(
        run_id="a" * 32,
        started_at="t1",
        commit="c1",
        iteration="iteration_01",
        cfg=cfg,
        observed_arms={"baseline": {"actual_version": "1.0.0"}},
    )
    manifest_b = plugin.build_manifest(
        run_id="b" * 32,
        started_at="t2",
        commit="c2",
        iteration="iteration_99",
        cfg=cfg,
        observed_arms={},
    )

    assert manifest_a["config_hash"] == manifest_b["config_hash"]


def test_build_manifest_config_hash_ignores_judge_actual_version() -> None:
    """Verify the host-probed judge actual_version never shifts config_hash."""
    # actual_version is a probe of the host judge binary, not a configured selector; two
    # runs of one config on machines whose judge binary differs (or is absent → null) must
    # still hash identically. The full judge object, actual_version included, is still emitted.
    base_judge = {"harness": "claude-code", "model": "sonnet", "effort": "medium"}
    cfg_probed = {
        "set": "default",
        "runner": "pytest",
        "arms": [{"name": "baseline"}],
        "judge": {**base_judge, "actual_version": "1.2.3"},
        "binder": {"provider": "gemini"},
    }
    cfg_null = {**cfg_probed, "judge": {**base_judge, "actual_version": None}}

    probed = plugin.build_manifest(
        run_id="a" * 32, started_at="t", commit="c", iteration="i",
        cfg=cfg_probed, observed_arms={},
    )
    null = plugin.build_manifest(
        run_id="b" * 32, started_at="t", commit="c", iteration="i",
        cfg=cfg_null, observed_arms={},
    )

    assert probed["config_hash"] == null["config_hash"]
    assert probed["judge"]["actual_version"] == "1.2.3"  # still emitted in the output


def test_build_manifest_config_hash_is_order_independent() -> None:
    """Verify build manifest config hash is order independent."""
    # config_hash hashes cfg with sort_keys, so two cfgs that differ only in key
    # order (and in the non-cfg identity fields) hash identically.
    cfg = {
        "set": "default",
        "runner": "pytest",
        "arms": [{"name": "baseline"}],
        "judge": {
            "harness": "claude-code",
            "model": "sonnet",
            "effort": "medium",
            "timeout": 300,
            "env": {},
            "harness_args": [],
            "actual_version": None,
        },
        "binder": {"provider": "gemini", "model": "x", "api_path": "y"},
    }
    reordered = dict(reversed(list(cfg.items())))

    manifest_a = plugin.build_manifest(
        run_id="a" * 32,
        started_at="t1",
        commit="c1",
        iteration="iteration_01",
        cfg=cfg,
        observed_arms={},
    )
    manifest_b = plugin.build_manifest(
        run_id="b" * 32,
        started_at="t2",
        commit="c2",
        iteration="iteration_99",
        cfg=reordered,
        observed_arms={},
    )

    assert manifest_a["config_hash"] == manifest_b["config_hash"]


def test_sessionfinish_writes_run_manifest(tmp_path: object, monkeypatch: object) -> None:
    """Verify sessionfinish writes run manifest."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
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
    assert meta["format_version"] == 2
    assert len(meta["run_id"]) == 32  # uuid4 hex
    assert len(meta["config_hash"]) == 12
    assert meta["iteration"] == "iteration_01"
    # v1 run-level identity fields are fully removed — no aliases.
    assert "agent" not in meta
    assert "agent_version" not in meta
    assert "token_split" not in meta
    assert meta["set"] == "default"
    assert meta["runner"] == "pytest"
    assert [arm["name"] for arm in meta["arms"]] == ["baseline", "trial"]
    assert meta["arms"][0]["harness_args"] == ["--set-flag"]
    assert meta["arms"][1]["harness_args"] == ["--set-flag", "--plugin-dir", "/project"]
    assert meta["arms"][1]["model"] == "opus"  # trial arm declared its own model
    # Per-arm install selector + capabilities replace the removed run-level fields.
    assert meta["arms"][0]["requested_version"] == "9.9.9"
    assert meta["arms"][0]["capabilities"] == {"token_split": True}
    # observed_arms is present and, with no provenance.json seeded, empty (never synthesized).
    assert meta["observed_arms"] == {}
    assert "judge_model" not in meta
    assert meta["judge"]["harness"] == "claude-code"
    assert meta["judge"]["model"] == "sonnet"
    assert meta["judge"]["effort"] == "medium"
    assert meta["judge"]["timeout"] == 300
    assert meta["judge"]["env"] == {}
    assert meta["judge"]["harness_args"] == []
    assert meta["judge"]["actual_version"] == "judge-1.0.0"  # host probe path
    assert meta["binder"] == {
        "provider": "gemini",
        "model": "gemini-3.1-flash-lite",
        "api_path": "generativelanguage.googleapis.com/v1beta",
    }
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


def test_sessionfinish_retains_planned_config_when_all_evals_fail(
    tmp_path: object, monkeypatch: object
) -> None:
    """Verify a fully failed run retains its configured set, runner, and arms."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skills_root = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"
    skills_root.mkdir(parents=True)

    _finish_and_summarize(tmp_path)

    meta = json.loads((skills_root.parent / "meta.json").read_text())
    assert meta["set"] == "default"
    assert meta["runner"] == "pytest"
    assert [arm["name"] for arm in meta["arms"]] == ["baseline", "trial"]


def test_sessionfinish_writes_nested_judge_object(tmp_path: object, monkeypatch: object) -> None:
    """Verify sessionfinish writes nested judge object."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
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
    assert meta["judge"]["actual_version"] == "judge-1.0.0"  # host-side probe
    assert "warnings" not in meta["judge"]


def test_sessionfinish_redacts_secret_shaped_judge_env(
    tmp_path: object, monkeypatch: object
) -> None:
    """Verify sessionfinish redacts secret shaped judge env."""
    # Every other judge test here uses JudgeConfig.env == {}, so redact_env({}) == {}
    # trivially passes even if the report.redact_env(...) call around the judge env
    # were dropped. Use a secret-shaped key (matches report._SECRET_KEY) so this test
    # only passes if the value is actually masked before it hits meta.json.
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
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


def test_manifest_writes_without_agent_credential(tmp_path: object, monkeypatch: object) -> None:
    """Verify a credential-less environment still writes a valid v2 manifest.

    `requested_version` is a pure install selector (`version()`), not a credentialed probe,
    so a run with no agent credential still records every planned arm with a concrete
    selector — here the real claude-code default of `latest`. Uses the real `make_agent`
    (no stub) precisely to prove the selector resolves without credentials.
    """
    for env_var in ("EVALSPEC_CLAUDE_VERSION", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(env_var, raising=False)
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=1, total=1)

    _finish_and_summarize(tmp_path)

    meta = json.loads((skill_results_dir.parent.parent / "meta.json").read_text())
    assert meta["format_version"] == 2
    assert meta["set"] == "default"
    assert [arm["requested_version"] for arm in meta["arms"]] == ["latest", "latest"]
    assert "agent_version" not in meta
    assert "token_split" not in meta


def _seed_provenance(
    sample_dir: object,
    arm: object,
    *,
    actual_version: object = "1.2.3",
    snapshot: object = "snap-abc",
    digest: object = "sha256:dead",
) -> None:
    """Write a `provenance.json` beside a seeded sample's grading.json.

    Mirrors what execution persists per sample, so the sessionfinish aggregation walk
    (`skills_root.rglob('provenance.json')`) picks it up into `observed_arms`.
    """
    record = RuntimeProvenance(
        arm=arm,
        actual_version=actual_version,
        actual_version_status="available",
        actual_version_error=None,
        sandbox=SandboxProvenance.from_image_identity(
            backend="microsandbox",
            snapshot=snapshot,
            fingerprint="ab12cd34",
            base_image_ref="ubuntu:latest",
            install_fingerprint="if-sha",
            env_script_sha256="env-sha",
            image_identity=ImageIdentity.available(digest),
        ),
    )
    (sample_dir / "provenance.json").write_text(json.dumps(record.to_disk_dict()))


def test_observed_arms_excludes_unrun_arm(tmp_path: object, monkeypatch: object) -> None:
    """Verify observed_arms holds only arms with a persisted record; planned holds all."""
    # `trial` ran and persisted provenance; `baseline` is configured but has no record.
    # Planned `arms` must list both; `observed_arms` must contain `trial` only, never a
    # synthesized `baseline`. The install selector ("9.9.9" here)
    # coexists with a distinct concrete guest `actual_version` ("1.2.3").
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    trial_sample = seed_arm(skill_results_dir, "alpha", "trial", passes=1, total=1)
    _seed_provenance(trial_sample, "trial", actual_version="1.2.3")
    seed_arm(skill_results_dir, "alpha", "baseline", passes=0, total=1)  # no provenance.json

    _finish_and_summarize(tmp_path)

    meta = json.loads((skill_results_dir.parent.parent / "meta.json").read_text())
    assert sorted(arm["name"] for arm in meta["arms"]) == ["baseline", "trial"]
    assert set(meta["observed_arms"]) == {"trial"}  # baseline excluded — never synthesized
    trial_planned = next(arm for arm in meta["arms"] if arm["name"] == "trial")
    assert trial_planned["requested_version"] == "9.9.9"  # install selector
    observed_trial = meta["observed_arms"]["trial"]
    assert observed_trial["actual_version"] == "1.2.3"  # concrete guest version
    assert observed_trial["actual_version_status"] == "available"
    assert observed_trial["sandbox"]["snapshot"] == "snap-abc"
    assert observed_trial["sandbox"]["image_digest"] == "sha256:dead"


def test_conflicting_provenance_raises(tmp_path: object, monkeypatch: object) -> None:
    """Verify two disagreeing records for one arm raise a clear aggregation error."""
    # Records that disagree on actual_version describe unlike environments; aggregation
    # must fail loudly rather than silently combine them into one report.
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    sample0 = seed_arm(skill_results_dir, "alpha", "trial", passes=1, total=1, sample=0)
    sample1 = seed_arm(skill_results_dir, "alpha", "trial", passes=1, total=1, sample=1)
    _seed_provenance(sample0, "trial", actual_version="1.2.3")
    _seed_provenance(sample1, "trial", actual_version="9.9.9")

    config = _FakeConfig(tmp_path)
    session = _FakeSession(config)
    with pytest.raises(ValueError, match="conflicting runtime provenance for arm `trial`"):
        plugin.pytest_sessionfinish(session, 0)


def test_provenance_arm_directory_mismatch_raises(tmp_path: object, monkeypatch: object) -> None:
    """A record whose `arm` disagrees with its own directory raises, naming both."""
    # `baseline` is a real roster arm, so only the directory-vs-record mismatch can fire
    # here (the record sits in the `trial/` dir): a mislocated label never keys a column.
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    trial_sample = seed_arm(skill_results_dir, "alpha", "trial", passes=1, total=1)
    _seed_provenance(trial_sample, "baseline")  # arm label disagrees with the trial/ dir

    config = _FakeConfig(tmp_path)
    session = _FakeSession(config)
    with pytest.raises(ValueError, match="provenance arm mismatch.*`baseline`.*`trial`"):
        plugin.pytest_sessionfinish(session, 0)


def test_provenance_arm_outside_roster_raises(tmp_path: object, monkeypatch: object) -> None:
    """A record for an arm the roster never configured raises, naming the arm."""
    # `ghost` sits in a matching `ghost/` dir (so the directory check passes) but is not
    # in the configured roster {baseline, trial} — the roster check must reject it.
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    ghost_sample = seed_arm(skill_results_dir, "alpha", "ghost", passes=1, total=1)
    _seed_provenance(ghost_sample, "ghost")

    config = _FakeConfig(tmp_path)
    session = _FakeSession(config)
    with pytest.raises(ValueError, match="provenance arm `ghost`.*not in the configured roster"):
        plugin.pytest_sessionfinish(session, 0)


def test_aggregate_observed_arms_trigger_only_skips_roster(tmp_path: object) -> None:
    """A trigger-only run (run_set None) aggregates without roster validation."""
    # No eval set resolves for a trigger-only run, so there is no roster to check against;
    # the walk must still aggregate the record rather than reject every arm.
    workspace.set_current_iteration("iteration_01")
    skills_root = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"
    ghost_sample = seed_arm(skills_root / "archive", "alpha", "ghost", passes=1, total=1)
    _seed_provenance(ghost_sample, "ghost")

    observed = plugin._aggregate_observed_arms(skills_root, None)

    assert set(observed) == {"ghost"}  # off-roster arm accepted when no set is configured


def test_binder_identity_carries_no_key_material(tmp_path: object, monkeypatch: object) -> None:
    """Verify meta.json binder identity never leaks GEMINI_API_KEY."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    monkeypatch.setenv("GEMINI_API_KEY", "sk-gemini-supersecret")
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=1, total=1)

    _finish_and_summarize(tmp_path)

    raw_text = (skill_results_dir.parent.parent / "meta.json").read_text()
    meta = json.loads(raw_text)
    assert set(meta["binder"]) == {"provider", "model", "api_path"}
    assert "sk-gemini-supersecret" not in raw_text


def test_terminal_summary_noop_without_artifacts(tmp_path: object, monkeypatch: object) -> None:
    """Verify terminal summary noop without artifacts."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
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

    from evalspec.sandbox import sandbox

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

    from evalspec.sandbox import sandbox

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
    from evalspec.sandbox import sandbox

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
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
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


def test_index_jsonl_rows_carry_core_axes(tmp_path: object, monkeypatch: object) -> None:
    """Verify index.jsonl rows gain harness/model/effort from the planned arm roster."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skills = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"
    seed_arm(skills / "archive", "alpha", "trial", passes=1, total=1)
    seed_arm(skills / "archive", "alpha", "baseline", passes=0, total=1)

    _finish_and_summarize(tmp_path)

    rows = [json.loads(line) for line in (skills.parent / "index.jsonl").read_text().splitlines()]
    trial_row = next(row for row in rows if row["arm"] == "trial")
    assert trial_row["harness"] == "claude-code"
    assert trial_row["model"] == "opus"  # trial overrides the set default
    assert "effort" in trial_row
    baseline_row = next(row for row in rows if row["arm"] == "baseline")
    assert baseline_row["model"] == "sonnet"  # inherits the set default


def test_benchmark_json_carries_runner_binder_observed(
    tmp_path: object, monkeypatch: object
) -> None:
    """Verify the run-level benchmark.json carries runner + binder + observed_arms."""
    # These must agree with meta.json written by the same sessionfinish: the observed
    # provenance is aggregated once and threaded into both artifacts.
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skills = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"
    trial_sample = seed_arm(skills / "archive", "alpha", "trial", passes=1, total=1)
    _seed_provenance(trial_sample, "trial", actual_version="1.2.3")
    seed_arm(skills / "archive", "alpha", "baseline", passes=0, total=1)  # no provenance.json

    _finish_and_summarize(tmp_path)

    iteration_root = skills.parent
    benchmark = json.loads((iteration_root / "benchmark.json").read_text())
    meta = json.loads((iteration_root / "meta.json").read_text())

    assert benchmark["format_version"] == 3
    assert benchmark["runner"] == meta["runner"] == "pytest"
    assert benchmark["binder"] == meta["binder"]
    assert benchmark["binder"]["provider"] == "gemini"
    # Observed provenance agrees with meta.json and excludes the unrun baseline arm.
    assert benchmark["observed_arms"] == meta["observed_arms"]
    assert set(benchmark["observed_arms"]) == {"trial"}
    assert "absent" not in benchmark["observed_arms"]
    # `absent`/`baseline` still valid empty matrix columns; provenance simply not fabricated.
    assert "baseline" in benchmark["arms"]
    assert {arm["name"] for arm in benchmark["planned_arms"]} == {"baseline", "trial"}
    # The markdown Provenance section labels the unobserved baseline arm.
    markdown = (iteration_root / "benchmark.md").read_text()
    assert "## Provenance" in markdown
    assert "- **baseline**: not observed" in markdown


def test_fail_under_sets_exit_status(tmp_path: object, monkeypatch: object) -> None:
    """Verify fail under sets exit status."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
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
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
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
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
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
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
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
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
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
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
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
    assert benchmark["format_version"] == 3
    assert {(entry["group"], entry["eval_id"]) for entry in benchmark["roster"]} == {
        ("archive", "alpha"),
        ("ingest", "beta"),
    }


def test_binder_degraded_warns_in_terminal_summary(tmp_path: object, monkeypatch: object) -> None:
    """Verify a nonzero binder_degraded total prints a WARN line, doesn't fail the run."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
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
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
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
    # (a string) and does NOT raise until evalspec.grading.judges.run_judge actually expands
    # it. Full end-to-end (a real arm run reaching the judge) needs live credentials
    # and a microVM — out of scope for `make test`; see tests/grading/judges/test_judge_registry.py
    # ::test_run_judge_env_unset_var_raises_schemaerror for the unit-level proof, and
    # tests/config/test_arms.py for expand_env's own unset-var coverage (the same function).
    import tomllib

    from evalspec.grading.judges import resolve_judge_config

    with (_FIXTURES / "unset-judge-env.toml").open("rb") as fixture_file:
        raw = tomllib.load(fixture_file)
    judge_table = raw["tool"]["evalspec"]["judge"]
    config = resolve_judge_config(pyproject_table=judge_table)  # no raise — structural only
    assert config.env == {"SOME_JUDGE_KEY": "$EVALSPEC_JUDGE_FIXTURE_UNSET_VAR"}

    import os

    from evalspec.grading.judges.registry import run_judge
    from evalspec.specs.schema import SchemaError

    os.environ.pop("EVALSPEC_JUDGE_FIXTURE_UNSET_VAR", None)
    with pytest.raises(SchemaError, match="EVALSPEC_JUDGE_FIXTURE_UNSET_VAR"):
        run_judge("prompt", config=config)
