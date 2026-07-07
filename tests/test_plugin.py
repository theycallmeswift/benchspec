"""Plugin tests via pytester — exercise the hooks in-process, no `claude -p`.

A dummy `test_eval(eval_arm)` is collected with `--collect-only`, so the body never
runs; we assert on the parametrized node ids the plugin generates. Each project writes a
`[tool.evalspec]` eval set so `resolved_run_set` has a real set to resolve.
"""

import json
from typing import NoReturn

import pytest

from evalspec import plugin, workspace
from evalspec.agents.base import AgentCapabilities
from tests.support import seed_arm, seed_trigger

ALPHA_MD = """\
---
{}
---

## Prompt

p

## Assertions

- [ ] a
"""

BETA_MD = """\
---
{}
---

## Prompt

t1

## Assertions

- [ ] x
"""

# A `default` eval set with two arms — the resolved columns are [baseline, trial] with
# baseline as the Δ reference. The set-level harness = "claude-code" means a sweep
# inherits a concrete harness, and the dummy collect never needs a real CLI.
ARMS_TOML = """\
[tool.evalspec]
default-set = "default"

[tool.evalspec.sets.default]
harness = "claude-code"
model = "sonnet"
baseline = "baseline"
arms = [{name="baseline"}, {name="trial", model="opus"}]
"""

DUMMY_CASES = """
def test_eval(eval_arm):
    pass

def test_trigger(trigger_query):
    pass
"""


def _make_project(
    pytester: object, skill: object = "myskill", arms_toml: object = ARMS_TOML
) -> None:
    """Create project."""
    (pytester.path / "pyproject.toml").write_text(arms_toml)
    evals = pytester.path / "skills" / skill / "evals"
    (evals / "alpha").mkdir(parents=True)
    (evals / "alpha" / "prompt.md").write_text(ALPHA_MD)
    (evals / "beta").mkdir(parents=True)
    (evals / "beta" / "prompt.md").write_text(BETA_MD)
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


def test_cross_product_of_evals_and_arms(pytester: object) -> None:
    """Verify cross product of evals and arms."""
    _make_project(pytester)

    out = _collect(pytester).stdout.str()

    for ident in (
        "test_eval[myskill-alpha-baseline]",
        "test_eval[myskill-alpha-trial]",
        "test_eval[myskill-beta-baseline]",
        "test_eval[myskill-beta-trial]",
    ):
        assert ident in out
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
    evals = pytester.path / "skills" / "myskill" / "evals"
    (evals / "bad").mkdir(parents=True)
    # an unknown `id` frontmatter key violates the self-contained eval schema
    (evals / "bad" / "prompt.md").write_text(
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


# ---------------------------------------------------------------------------
# resolved_run_set — unit-level (no pytester collect): drives the pyproject read +
# resolve_set wiring by value.
# ---------------------------------------------------------------------------


class _SetConfig:
    """Config stub for resolved_run_set: repo root + the eval-set CLI options."""

    def __init__(
        self: object,
        repo_root: object,
        *,
        set_name: object = None,
        config: object = None,
        model: object = None,
        harness: object = None,
        effort: object = None,
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
                "evalspec_effort": effort,
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
    s = plugin.resolved_run_set(_SetConfig(tmp_path))
    assert [a.name for a in s.arms] == ["baseline", "trial"]
    assert s.baseline == "baseline"


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

    s = plugin.resolved_run_set(_SetConfig(tmp_path))

    assert s.arms[0].harness_args == ["--set-flag"]
    assert s.arms[1].harness_args == ["--set-flag", "--plugin-dir", "/project"]


def test_resolved_run_set_set_model_default_not_clobbered(tmp_path: object) -> None:
    """Verify resolved run set set model default not clobbered."""
    # A non-sonnet set default must survive a plain run (no --evalspec-model passed).
    (tmp_path / "pyproject.toml").write_text(
        '[tool.evalspec]\ndefault-set = "default"\n'
        '[tool.evalspec.sets.default]\nharness = "claude-code"\n'
        'model = "opus"\nbaseline = "b"\narms = [{name="b"}]\n'
    )
    s = plugin.resolved_run_set(_SetConfig(tmp_path))  # evalspec_model defaults to None
    assert s.arms[0].model == "opus"


def test_resolved_run_set_models_sweep(tmp_path: object) -> None:
    """Verify resolved run set models sweep."""
    _write_sets_pyproject(tmp_path)

    s = plugin.resolved_run_set(_SetConfig(tmp_path, models="sonnet,opus"))

    assert [a.name for a in s.arms] == ["sonnet", "opus"]
    assert s.baseline == "sonnet"
    assert all(a.harness == "claude-code" for a in s.arms)  # inherited, never None


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


def test_trigger_queries_parametrized(pytester: object) -> None:
    """Verify trigger queries parametrized."""
    evals = pytester.path / "skills" / "myskill" / "evals"
    evals.mkdir(parents=True)
    (evals / "trigger-evals.md").write_text(
        "---\nskill_name: myskill\n---\n## Trigger\n\n- q1: do it\n\n## No Trigger\n\n- q2: nope\n"
    )
    pytester.makepyfile(test_cases=DUMMY_CASES)

    result = pytester.runpytest(
        "-p",
        "evalspec.plugin",
        "--collect-only",
        "-q",
        "--evalspec-repo-root",
        str(pytester.path),
        "test_cases.py::test_trigger",
    )

    out = result.stdout.str()
    assert "test_trigger[myskill-q1]" in out
    assert "test_trigger[myskill-q2]" in out


def test_trigger_xfail_marks_known_failure_on_listed_tier(pytester: object) -> None:
    """Verify trigger xfail marks known failure on listed tier."""
    # On a listed tier (sonnet), the xfail mark attaches: the dummy body passes,
    # so the marked param shows XPASS while the unmarked one passes — proving the
    # mark was attached without real routing.
    evals = pytester.path / "skills" / "myskill" / "evals"
    evals.mkdir(parents=True)
    (evals / "trigger-evals.md").write_text(
        "---\nskill_name: myskill\n---\n## Trigger\n\n- routes-fine: routes fine\n\n"
        "- known-miss: known miss\n"
        "  - fails-on [sonnet]: documented routing boundary\n"
    )
    pytester.makepyfile(test_cases=DUMMY_CASES)

    result = pytester.runpytest(
        "-p",
        "evalspec.plugin",
        "-rX",
        "--evalspec-model",
        "sonnet",
        "--evalspec-repo-root",
        str(pytester.path),
        "test_cases.py::test_trigger",
    )

    result.assert_outcomes(passed=1, xpassed=1)


def test_trigger_xfail_strict_on_unlisted_tier(pytester: object) -> None:
    """Verify trigger xfail strict on unlisted tier."""
    # On a tier NOT listed (opus), the mark is withheld: the same query runs strict.
    # The dummy body passes, so BOTH params show plain PASS (no xpassed) — proving
    # the gate stays strict where the xfail doesn't apply.
    evals = pytester.path / "skills" / "myskill" / "evals"
    evals.mkdir(parents=True)
    (evals / "trigger-evals.md").write_text(
        "---\nskill_name: myskill\n---\n## Trigger\n\n- routes-fine: routes fine\n\n"
        "- known-miss: known miss\n"
        "  - fails-on [sonnet]: documented routing boundary\n"
    )
    pytester.makepyfile(test_cases=DUMMY_CASES)

    result = pytester.runpytest(
        "-p",
        "evalspec.plugin",
        "-rX",
        "--evalspec-model",
        "opus",
        "--evalspec-repo-root",
        str(pytester.path),
        "test_cases.py::test_trigger",
    )

    result.assert_outcomes(passed=2)


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
    with open(LOG, "a") as f:
        f.write(f"{{sample_index}}\\n")
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
    observed = [int(x) for x in log.read_text().split() if x]
    assert sorted(observed) == [0, 0, 0, 0, 1, 1, 1, 1]


# ---------------------------------------------------------------------------
# pytest_sessionfinish / pytest_terminal_summary — manifest + benchmark glue
# ---------------------------------------------------------------------------


class _FakeConfig:
    """Provide a fake config for tests."""

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
            "evalspec_judge_model": "sonnet",
            "evalspec_trigger_effort": "low",
            "evalspec_trigger_mode": "asymmetric",
            "evalspec_fail_under": self._fail_under,
        }.get(name)


class _FakeTR:
    """Provide a fake t r for tests."""

    def __init__(self: object) -> None:
        """Initialize the instance."""
        self.events = []
        self.lines = []

    def write_sep(self: object, sep: object, title: object) -> None:
        """Write sep."""
        self.events.append(("sep", title))

    def line(self: object, msg: object) -> None:
        """Line."""
        self.events.append(("line", msg))
        self.lines.append(msg)


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
    """Drive the post-run pipeline the way pytest does: sessionfinish builds the.

    artifacts, terminal_summary only prints.
    """
    config = _FakeConfig(tmp_path)
    session = _FakeSession(config)
    plugin.pytest_sessionfinish(session, 0)
    tr = _FakeTR()
    plugin.pytest_terminal_summary(tr, 0, config)
    return session, tr


def test_terminal_summary_prints_delta(tmp_path: object, monkeypatch: object) -> None:
    """Verify terminal summary prints delta."""
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    it = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(it, "alpha", "trial", passes=2, total=2)
    seed_arm(it, "alpha", "baseline", passes=0, total=2)

    _, tr = _finish_and_summarize(tmp_path)

    assert ("sep", "evalspec benchmark") in tr.events
    lines = [m for kind, m in tr.events if kind == "line"]
    assert any("archive" in m and "+100pp" in m for m in lines)
    assert (it / "benchmark.md").is_file()


def test_terminal_summary_multi_skill_single_header(tmp_path: object, monkeypatch: object) -> None:
    """Verify terminal summary multi skill single header."""
    # Two skills with eval-* children emit exactly one header and one delta line
    # each (sorted: archive before ingest). A third skill dir that contains only
    # a trigger-q1 subdir now reports too — trigger-only skills are no longer skipped.
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

    trigger_only_dir = skills / "trigger-only"
    seed_trigger(trigger_only_dir, 1, should_trigger=True, fires=0)

    _, tr = _finish_and_summarize(tmp_path)

    assert tr.events.count(("sep", "evalspec benchmark")) == 1

    lines = [m for kind, m in tr.events if kind == "line"]
    # trigger-only skill now reports too
    assert len(lines) == 3
    assert any("trigger-only" in m and "trigger 0/1" in m for m in lines)
    assert (trigger_only_dir / "benchmark.md").exists()

    benchmark = json.loads((archive_dir / "benchmark.json").read_text())
    assert benchmark["label"] == "iteration_01 · archive"


def test_build_manifest_assembles_shape_by_value() -> None:
    """Verify build manifest assembles shape by value."""
    # The pure assembler is testable by value (no uuid/clock/git IO) — the shell
    # injects identity. Pins the spread of cfg and the hash, which the IO-bound
    # sessionfinish test below can only presence-check.
    cfg = {
        "agent": "claude-code",
        "agent_version": "9.9.9",
        "model": "sonnet",
        "judge_model": "sonnet",
        "eval_effort": "medium",
        "trigger_effort": "low",
        "trigger_mode": "asymmetric",
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
    assert manifest["trigger_mode"] == "asymmetric"
    assert len(manifest["config_hash"]) == 12


def test_build_manifest_config_hash_is_order_independent() -> None:
    """Verify build manifest config hash is order independent."""
    # config_hash hashes cfg with sort_keys, so two cfgs that differ only in key
    # order (and in the non-cfg identity fields) hash identically.
    cfg = {
        "agent": "claude-code",
        "agent_version": None,
        "model": "sonnet",
        "judge_model": "sonnet",
        "eval_effort": "medium",
        "trigger_effort": "low",
        "trigger_mode": "asymmetric",
    }
    reordered = dict(reversed(list(cfg.items())))

    a = plugin.build_manifest(
        run_id="a" * 32,
        started_at="t1",
        commit="c1",
        iteration="iteration_01",
        cfg=cfg,
        token_split=True,
    )
    b = plugin.build_manifest(
        run_id="b" * 32,
        started_at="t2",
        commit="c2",
        iteration="iteration_99",
        cfg=reordered,
        token_split=False,
    )

    assert a["config_hash"] == b["config_hash"]


def test_sessionfinish_writes_run_manifest(tmp_path: object, monkeypatch: object) -> None:
    """Verify sessionfinish writes run manifest."""
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(
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
    workspace.set_current_iteration("iteration_01")
    it = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(it, "alpha", "trial", passes=1, total=1)
    seed_arm(it, "alpha", "baseline", passes=0, total=1)

    _finish_and_summarize(tmp_path)

    meta = json.loads((it.parent.parent / "meta.json").read_text())
    assert meta["format_version"] == 1
    assert len(meta["run_id"]) == 32  # uuid4 hex
    assert len(meta["config_hash"]) == 12
    assert meta["iteration"] == "iteration_01"
    assert meta["agent"] == "claude-code"
    assert meta["agent_version"] == "9.9.9"
    assert meta["set"] == "default"
    assert [a["name"] for a in meta["arms"]] == ["baseline", "trial"]
    assert meta["arms"][0]["harness_args"] == ["--set-flag"]
    assert meta["arms"][1]["harness_args"] == ["--set-flag", "--plugin-dir", "/project"]
    assert meta["arms"][1]["model"] == "opus"  # trial arm declared its own model
    assert meta["judge_model"] == "sonnet"
    assert meta["trigger_effort"] == "low"
    assert meta["trigger_mode"] == "asymmetric"
    assert meta["token_split"] is True
    assert "commit" in meta
    assert "started_at" in meta
    assert "evalspec_version" in meta

    benchmark = json.loads((it / "benchmark.json").read_text())
    assert benchmark["arms"]["baseline"]["harness_args"] == ["--set-flag"]
    assert benchmark["arms"]["trial"]["harness_args"] == [
        "--set-flag",
        "--plugin-dir",
        "/project",
    ]
    md = (it / "benchmark.md").read_text()
    assert "- Harness args: `--set-flag` `--plugin-dir` `/project`" in md


def test_sessionfinish_trigger_only_without_eval_set(tmp_path: object, monkeypatch: object) -> None:
    """Verify sessionfinish trigger only without eval set."""
    # A trigger-only run that declared no eval set must finish, not crash: sessionfinish
    # resolves the set only when an `eval-` artifact exists. Here there are only `trigger-`
    # dirs, so the set is never resolved and the manifest degrades to set=None/arms=[].
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    workspace.set_current_iteration("iteration_01")
    skills = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"
    trigger_only = skills / "router"
    seed_trigger(trigger_only, 1, should_trigger=True, fires=1)

    _, tr = _finish_and_summarize(tmp_path)  # old code: UsageError from resolved_run_set

    meta = json.loads((skills.parent / "meta.json").read_text())
    assert meta["set"] is None
    assert meta["arms"] == []
    assert (trigger_only / "benchmark.md").exists()
    assert any("router" in m and "trigger 1/1" in m for m in tr.lines)


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
    it = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(it, "alpha", "trial", passes=1, total=1)

    _finish_and_summarize(tmp_path)

    meta = json.loads((it.parent.parent / "meta.json").read_text())
    assert meta["agent_version"] is None
    assert meta["token_split"] is None
    assert meta["set"] == "default"


def test_terminal_summary_noop_without_artifacts(tmp_path: object, monkeypatch: object) -> None:
    """Verify terminal summary noop without artifacts."""
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    workspace.set_current_iteration("iteration_01")

    _, tr = _finish_and_summarize(tmp_path)

    assert tr.events == []


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


def test_sessionfinish_writes_index_jsonl(tmp_path: object, monkeypatch: object) -> None:
    """Verify sessionfinish writes index jsonl."""
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    it = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"
    seed_arm(it / "archive", "alpha", "trial", passes=1, total=1)
    seed_arm(it / "archive", "alpha", "baseline", passes=0, total=1)
    seed_trigger(it / "bootstrap", 1, should_trigger=True, fires=1)

    _finish_and_summarize(tmp_path)

    lines = [json.loads(x) for x in (it.parent / "index.jsonl").read_text().splitlines()]
    assert {r["skill"] for r in lines} == {"archive", "bootstrap"}
    assert sum(1 for r in lines if r["kind"] == "eval") == 2
    assert sum(1 for r in lines if r["kind"] == "trigger") == 1


def test_fail_under_sets_exit_status(tmp_path: object, monkeypatch: object) -> None:
    """Verify fail under sets exit status."""
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    it = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(it, "alpha", "trial", passes=0, total=2)  # 0%
    seed_arm(it, "alpha", "baseline", passes=2, total=2)  # 100% → delta -100pp

    config = _FakeConfig(tmp_path, fail_under=0.0)
    session = _FakeSession(config)
    plugin.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 1
    tr = _FakeTR()
    plugin.pytest_terminal_summary(tr, 1, config)
    assert any("fail-under" in line for line in tr.lines)


def test_fail_under_quiet_when_met(tmp_path: object, monkeypatch: object) -> None:
    """Verify fail under quiet when met."""
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    it = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(it, "alpha", "trial", passes=2, total=2)  # 100%
    seed_arm(it, "alpha", "baseline", passes=0, total=2)  # 0% → delta +100pp

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
    it = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(it, "alpha", "trial-opus", passes=0, total=2)
    seed_arm(it, "alpha", "trial-sonnet", passes=0, total=2)

    config = _FakeConfig(tmp_path, fail_under=0.0)
    session = _FakeSession(config)
    plugin.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 0
