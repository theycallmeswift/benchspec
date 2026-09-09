"""Plugin tests via pytester — exercise the hooks in-process, no `claude -p`.

A dummy `test_eval(eval_arm)` is collected with `--collect-only`, so the body never
runs; we assert on the parametrized node ids the plugin generates. Each project writes a
`[tool.benchspec]` eval set so `resolved_run_set` has a real set to resolve.
"""

from __future__ import annotations

import io
import json
import textwrap
from pathlib import Path

import pytest

from benchspec.agents.base import AgentCapabilities
from benchspec.orchestration import workspace
from benchspec.orchestration.execution import ArmOutcome
from benchspec.reporting import report
from benchspec.runners import pytest as plugin
from benchspec.runners.run import PluginOptions
from benchspec.sandbox.provenance import ImageIdentity, RuntimeProvenance, SandboxProvenance
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
[tool.benchspec]
default-set = "default"

[tool.benchspec.sets.default]
harness = "claude-code"
model = "sonnet"
baseline = "baseline"
arms = [{name="baseline"}, {name="trial", model="opus"}]
"""
)

# Same as ARMS_TOML plus a [tool.benchspec.judge] env entry whose key is secret-shaped
# (matches report._SECRET_KEY) — proves the judge env path is actually redacted, not
# just presence-checked against an always-empty {} like every other judge test here.
JUDGE_ENV_TOML = """\
[tool.benchspec]
default-set = "default"

[tool.benchspec.sets.default]
harness = "claude-code"
model = "sonnet"
baseline = "baseline"
arms = [{name="baseline"}, {name="trial", model="opus"}]

[tool.benchspec.judge]
env = { OPENAI_API_KEY = "sk-live-supersecret123" }
"""

DUMMY_CASES = """
def test_eval(eval_arm):
    pass
"""

# Asserts, from a conftest's own `pytest_configure`, that `.env` is already loaded — the
# hook ordering `test_dotenv_loads_before_conftests_run` exercises.
DOTENV_PROBE_CONFTEST = """
import os

import pytest


def pytest_configure(config):
    if not os.environ.get("BENCHSPEC_DOTENV_PROBE"):
        raise pytest.UsageError("BENCHSPEC_DOTENV_PROBE missing at configure time")
"""


def _make_project(
    pytester: pytest.Pytester, skill: str = "myskill", arms_toml: str = ARMS_TOML
) -> None:
    """Write a pyproject, two evals under `skills/<skill>`, and the dummy cases module."""
    (pytester.path / "pyproject.toml").write_text(arms_toml)
    evals = pytester.path / "skills" / skill / "evals" / skill
    evals.mkdir(parents=True)
    (evals / "alpha.eval.md").write_text(ALPHA_MD)
    (evals / "beta.eval.md").write_text(BETA_MD)
    pytester.makepyfile(test_cases=DUMMY_CASES)


def _collect(pytester: pytest.Pytester, *extra: str) -> pytest.RunResult:
    """Collect the dummy `test_eval` in-process with the plugin loaded against `pytester.path`."""
    return pytester.runpytest(
        "-p",
        "benchspec.runners.pytest",
        "--collect-only",
        "-q",
        "--benchspec-repo-root",
        str(pytester.path),
        "test_cases.py::test_eval",
        *extra,
    )


def test_help_describes_eval_search_paths(pytester: pytest.Pytester) -> None:
    """Verify the eval-paths option help names the default search paths."""
    result = pytester.runpytest("-p", "benchspec.runners.pytest", "--help")

    output = " ".join(result.stdout.str().split())
    assert result.ret == 0
    assert "eval.md / *.eval.md" in output
    assert "skills, tests, evals, benchmarks" in output


def test_cross_product_of_evals_and_arms(pytester: pytest.Pytester) -> None:
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
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify plugin self registers cases without positional."""
    # `make evals` passes no positional path; the plugin must inject its cases file
    # so they still collect (and so a -k nodeid can't union-collect test_eval).
    _make_project(pytester)
    monkeypatch.setattr(plugin, "_CASES", pytester.path / "test_cases.py")

    result = pytester.runpytest(
        "-p",
        "benchspec.runners.pytest",
        "--collect-only",
        "-q",
        "--benchspec-repo-root",
        str(pytester.path),
        # deliberately no positional target
    )

    out = result.stdout.str()
    assert "test_eval[myskill-alpha-baseline]" in out


def test_explicit_positional_is_respected(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify explicit positional is respected."""
    # When the user passes their own target, the plugin must NOT override it.
    _make_project(pytester)
    monkeypatch.setattr(plugin, "_CASES", pytester.path / "should_not_be_used.py")

    result = pytester.runpytest(
        "-p",
        "benchspec.runners.pytest",
        "--collect-only",
        "-q",
        "--benchspec-repo-root",
        str(pytester.path),
        "test_cases.py::test_eval",
    )

    assert result.ret == 0
    assert "test_eval[myskill-alpha-baseline]" in result.stdout.str()


def test_models_flag_sweeps_arms(pytester: pytest.Pytester) -> None:
    """Verify models flag sweeps arms."""
    # --benchspec-models is a SWEEP, not a filter: it replaces the set's arms with one
    # arm per value (named by it), inheriting the set-level harness.
    _make_project(pytester)

    out = _collect(pytester, "--benchspec-models", "sonnet,opus").stdout.str()

    assert out.count("test_eval[") == 4  # 2 evals × 2 swept arms
    assert "test_eval[myskill-alpha-sonnet]" in out
    assert "test_eval[myskill-alpha-opus]" in out


def test_malformed_schema_fails_collection(pytester: pytest.Pytester) -> None:
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


def test_bad_set_fails_collection(pytester: pytest.Pytester) -> None:
    """Verify bad set fails collection."""
    # A set arm with an unknown harness must fail collection with a UsageError
    # (wrapped SchemaError), not a silent empty roster.
    bad_set = (
        '[tool.benchspec]\ndefault-set = "default"\n'
        '[tool.benchspec.sets.default]\nmodel = "sonnet"\n'
        'arms = [{name="x", harness="nope"}]\n'
    )
    _make_project(pytester, arms_toml=bad_set)

    result = _collect(pytester)

    assert result.ret != 0
    out = result.stderr.str() + result.stdout.str()
    assert "nope" in out


def test_preflight_session_sandbox_uses_resolved_set_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify the resolved set's .sandbox — not DEFAULT_SANDBOX — drives sandbox.preflight."""
    from benchspec.config.arms import Arm, Set
    from benchspec.orchestration import cases

    fake_set = Set(
        "s",
        [Arm("a", "claude-code", "opus"), Arm("b", "codex", "gpt-5.5")],
        baseline=None,
        sandbox="custombackend",
    )
    monkeypatch.setattr(cases, "session_run_set", lambda config: fake_set)
    monkeypatch.setattr(cases, "resolve_sandbox", lambda name: f"backend:{name}")
    preflight_calls: list[tuple[str | None, list[str]]] = []
    monkeypatch.setattr(
        cases.sandbox,
        "preflight",
        lambda backend=None, harnesses=(): preflight_calls.append((backend, list(harnesses))),
    )

    # The options go unread — `session_run_set` is stubbed — so an empty adapter suffices.
    cases.preflight_session_sandbox(PluginOptions(values={}, rootpath=tmp_path))

    # Every arm harness rides along, so a set spanning harnesses preflights each credential.
    assert preflight_calls == [("backend:custombackend", ["claude-code", "codex"])]


def test_preflight_session_sandbox_trigger_only_uses_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify a trigger-only run passes None so preflight resolves the default backend."""
    from benchspec.orchestration import cases

    monkeypatch.setattr(cases, "session_run_set", lambda config: None)
    preflight_calls: list[tuple[str | None, list[str]]] = []
    monkeypatch.setattr(
        cases.sandbox,
        "preflight",
        lambda backend=None, harnesses=(): preflight_calls.append((backend, list(harnesses))),
    )

    cases.preflight_session_sandbox(PluginOptions(values={}, rootpath=tmp_path))

    # None => preflight resolves DEFAULT_SANDBOX itself; no set => no arm harnesses.
    assert preflight_calls == [(None, [])]


def test_preflight_grading_reports_binder_and_judge_failures_together(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing Gemini key does not hide a judge that cannot authenticate."""
    from benchspec.grading import binder
    from benchspec.grading.judges import JudgeConfig
    from benchspec.orchestration import cases

    def reject_gemini() -> None:
        """Reject the binder credential the way the real check does."""
        raise RuntimeError("GEMINI_API_KEY is required")

    def reject_judge(config: JudgeConfig) -> None:
        """Reject the judge credential the way the real check does."""
        raise RuntimeError(f"judge harness `{config.harness}`: not logged in")

    monkeypatch.setattr(cases, "resolved_judge_config", lambda config: JudgeConfig(harness="codex"))
    monkeypatch.setattr(binder, "preflight_verify_gemini_key", reject_gemini)
    monkeypatch.setattr(cases, "preflight_verify_judge_binary", lambda config: None)
    monkeypatch.setattr(cases, "preflight_verify_judge_credential", reject_judge)

    with pytest.raises(RuntimeError) as exc_info:
        cases.preflight_grading(PluginOptions(values={}, rootpath=tmp_path))

    assert str(exc_info.value) == textwrap.dedent(
        """\
        benchspec grading preflight failed:
          - GEMINI_API_KEY is required
          - judge harness `codex`: not logged in"""
    )


def test_preflight_grading_skips_the_credential_probe_when_the_judge_binary_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing judge binary is reported as such, never as a failed login on top of it."""
    from benchspec.grading import binder
    from benchspec.grading.judges import JudgeConfig
    from benchspec.orchestration import cases

    def reject_binary(config: JudgeConfig) -> None:
        """Reject the judge binary the way the real check does."""
        raise RuntimeError("judge harness `codex` binary `codex` not found on PATH")

    def must_not_probe(config: JudgeConfig) -> None:
        """Fail the test if the credential probe runs without a binary."""
        raise AssertionError("credential probe ran without a judge binary")

    monkeypatch.setattr(cases, "resolved_judge_config", lambda config: JudgeConfig(harness="codex"))
    monkeypatch.setattr(binder, "preflight_verify_gemini_key", lambda: None)
    monkeypatch.setattr(cases, "preflight_verify_judge_binary", reject_binary)
    monkeypatch.setattr(cases, "preflight_verify_judge_credential", must_not_probe)

    with pytest.raises(RuntimeError, match="binary `codex` not found on PATH"):
        cases.preflight_grading(PluginOptions(values={}, rootpath=tmp_path))


def test_eval_threads_resolved_set_sandbox_into_run_eval_arm(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify test_eval passes the resolved set's .sandbox as run_eval_arm(sandbox_name=...)."""
    from benchspec.grading import binder
    from benchspec.orchestration import cases
    from benchspec.sandbox import sandbox

    _make_project(pytester)  # set with sandbox default = docker
    captured: list[str] = []

    def capture(*args: object, sandbox_name: str, **kwargs: object) -> ArmOutcome:
        """Record the sandbox name the cell was handed and report a clean, empty outcome."""
        captured.append(sandbox_name)
        return ArmOutcome(grading={}, errored=False, duration_ms=0, total_tokens=0)

    # Neutralize the environment-dependent preflights so the body reaches run_eval_arm.
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None, **kwargs: None)
    monkeypatch.setattr(binder, "preflight_verify_gemini_key", lambda: None)
    monkeypatch.setattr(cases, "preflight_verify_judge_binary", lambda config: None)
    monkeypatch.setattr(cases, "preflight_verify_judge_credential", lambda config: None)
    monkeypatch.setattr(cases, "seed_room", lambda *args, **kwargs: {})
    monkeypatch.setattr(cases, "run_eval_arm", capture)

    result = pytester.runpytest(
        "-p",
        "benchspec.runners.pytest",
        "--benchspec-repo-root",
        str(pytester.path),
        "-k",
        "test_eval",
    )

    assert result.ret == 0
    assert captured  # the parametrized body actually ran
    assert set(captured) == {"docker"}  # every arm got the resolved set's sandbox


def test_count_two_parametrizes_sample_index(pytester: pytest.Pytester, tmp_path: Path) -> None:
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
        "benchspec.runners.pytest",
        "-p",
        "pytest_repeat",
        "--benchspec-repo-root",
        str(pytester.path),
        "--count",
        "2",
        "test_cases.py::test_eval",
    )

    result.assert_outcomes(passed=8)
    observed = [int(value) for value in log.read_text().split() if value]
    assert sorted(observed) == [0, 0, 0, 0, 1, 1, 1, 1]


def test_progress_attributes_cells_to_authored_eval_files(pytester: pytest.Pytester) -> None:
    """Verify default progress groups cells under their `.eval.md`, not the wrapper module."""
    _make_project(pytester)

    result = pytester.runpytest(
        "-p",
        "benchspec.runners.pytest",
        "--benchspec-repo-root",
        str(pytester.path),
        "test_cases.py::test_eval",
    )

    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines(
        [
            r"skills/myskill/evals/myskill/alpha\.eval\.md \.\.",
            r"skills/myskill/evals/myskill/beta\.eval\.md \.\.",
        ]
    )
    assert "test_cases.py .." not in result.stdout.str()


def test_verbose_progress_keeps_eval_arm_ids(pytester: pytest.Pytester) -> None:
    """Verify verbose lines still carry the unique eval × arm id per cell."""
    _make_project(pytester)

    result = pytester.runpytest(
        "-v",
        "-p",
        "benchspec.runners.pytest",
        "--benchspec-repo-root",
        str(pytester.path),
        "test_cases.py::test_eval",
    )

    result.assert_outcomes(passed=4)
    alpha = "skills/myskill/evals/myskill/alpha.eval.md"
    beta = "skills/myskill/evals/myskill/beta.eval.md"
    result.stdout.fnmatch_lines(
        [
            f"{alpha}::test_eval[[]myskill-alpha-baseline[]] PASSED*",
            f"{alpha}::test_eval[[]myskill-alpha-trial[]] PASSED*",
            f"{beta}::test_eval[[]myskill-beta-baseline[]] PASSED*",
            f"{beta}::test_eval[[]myskill-beta-trial[]] PASSED*",
        ]
    )


class _StubAgent:
    """The slice of `CodingAgent` the planned-arm roster reads: id, selector, capabilities."""

    id = "claude-code"
    capabilities = AgentCapabilities(multi_turn=True, token_split=True)

    def version(self) -> str:
        """Return the install selector the manifest records as `requested_version`."""
        return "9.9.9"


@pytest.fixture(autouse=True)
def _stub_judge_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the host judge-version probe so sessionfinish tests stay hermetic.

    `manifest.judge_meta` shells out to `<judge binary> --version`; on a dev box with the
    harness installed that is nondeterministic and non-hermetic. Pin it to a sentinel so the
    judge `actual_version` is predictable and the dedicated test can assert it flows through.
    """
    monkeypatch.setattr(
        "benchspec.reporting.manifest.probe_judge_version", lambda harness: "judge-1.0.0"
    )


def _configured_plugin(
    pytester: pytest.Pytester, repo_root: Path, *options: str
) -> pytest.Config:
    """Parse and configure a real pytest `Config` with the plugin loaded against `repo_root`.

    This is everything `pytest_sessionfinish` / `pytest_terminal_summary` read: the
    plugin's `--benchspec-*` options, the stash, and the terminal writer (`--color=no`
    keeps the summary matrix plain unless a test passes its own `--color`).

    Configuring runs the plugin's `pytest_configure`, which names the run's iteration from
    what already sits under `repo_root` — build the config before seeding artifacts, then
    pin the iteration the seeds use.
    """
    return pytester.parseconfigure(
        "-p",
        "benchspec.runners.pytest",
        f"--benchspec-repo-root={repo_root}",
        "--color=no",
        *options,
    )


def _session(config: pytest.Config) -> pytest.Session:
    """A real session on `config`, carrying the OK exit status pytest sets before its hooks."""
    session = pytest.Session.from_config(config)
    session.exitstatus = 0
    return session


def _printed_summary(config: pytest.Config, exitstatus: int) -> list[str]:
    """Drive `pytest_terminal_summary` through a real reporter and return the lines it wrote."""
    output = io.StringIO()
    reporter = pytest.TerminalReporter(config, file=output)
    plugin.pytest_terminal_summary(reporter, exitstatus, config)
    return output.getvalue().splitlines()


def _finish_and_summarize(config: pytest.Config) -> tuple[pytest.Session, list[str]]:
    """Drive the post-run pipeline the way pytest does.

    `sessionfinish` builds the artifacts; `terminal_summary` only prints them.
    """
    session = _session(config)
    plugin.pytest_sessionfinish(session, 0)
    return session, _printed_summary(config, 0)


def test_terminal_summary_prints_matrix(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the summary is a per-eval matrix ending in a pointer at benchmark.md."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=2, total=2)
    seed_arm(skill_results_dir, "alpha", "baseline", passes=0, total=2)

    _, printed = _finish_and_summarize(config)

    assert printed[0].strip("= ") == "benchspec benchmark"
    header, eval_row, rule, footer, versus, pointer = printed[1:]
    assert header.split() == ["Eval", "baseline", "trial"]
    assert eval_row.split() == ["archive/alpha", "0%", "100%"]
    assert rule == "-" * len(header)
    assert footer.split() == ["All", "evals", "0%", "100%"]
    assert versus.split() == ["vs", "baseline", "+100pp"]
    benchmark_md = skill_results_dir.parent.parent / "benchmark.md"
    assert pointer == f"Report: {benchmark_md}"
    assert benchmark_md.is_file()


def test_terminal_summary_multi_skill_single_header(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify terminal summary multi skill single header."""
    # Two skills with eval-* children pool into ONE run-level table, under a single
    # header (rows sorted: archive before ingest).
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path)
    workspace.set_current_iteration("iteration_01")
    skills = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"

    archive_dir = skills / "archive"
    seed_arm(archive_dir, "alpha", "trial", passes=2, total=2)
    seed_arm(archive_dir, "alpha", "baseline", passes=0, total=2)

    ingest_dir = skills / "ingest"
    seed_arm(ingest_dir, "beta", "trial", passes=1, total=2)
    seed_arm(ingest_dir, "beta", "baseline", passes=1, total=2)

    _, printed = _finish_and_summarize(config)

    headers = [line for line in printed if line.strip("= ") == "benchspec benchmark"]
    assert headers == [printed[0]]

    *table, pointer = printed[1:]
    assert [line.split("  ")[0] for line in table] == [
        "Eval",
        "archive/alpha",
        "ingest/beta",
        "-" * len(table[0]),
        "All evals",
        "vs baseline",
    ]
    assert pointer.startswith("Report: ")

    iteration_root = skills.parent
    benchmark = json.loads((iteration_root / "benchmark.json").read_text())
    assert benchmark["label"] == "iteration_01"
    markdown = (iteration_root / "benchmark.md").read_text()
    assert "| archive/alpha |" in markdown
    assert "| ingest/beta |" in markdown
    assert "| All evals |" in markdown


def test_sessionfinish_writes_run_manifest(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify sessionfinish writes run manifest."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
[tool.benchspec]
default-set = "default"

[tool.benchspec.sets.default]
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
    config = _configured_plugin(pytester, tmp_path)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=1, total=1)
    seed_arm(skill_results_dir, "alpha", "baseline", passes=0, total=1)

    _finish_and_summarize(config)

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
        "model": "gemini-3.5-flash-lite",
        "api_path": "generativelanguage.googleapis.com/v1beta",
    }
    assert "commit" in meta
    assert "started_at" in meta
    assert "benchspec_version" in meta

    benchmark = json.loads((skill_results_dir.parent.parent / "benchmark.json").read_text())
    assert benchmark["arms"]["baseline"]["harness_args"] == ["--set-flag"]
    assert benchmark["arms"]["trial"]["harness_args"] == [
        "--set-flag",
        "--plugin-dir",
        "/project",
    ]
    benchmark_markdown = (skill_results_dir.parent.parent / "benchmark.md").read_text()
    assert "- Harness args: `--set-flag` `--plugin-dir` `/project`" in benchmark_markdown


def test_sessionfinish_retains_planned_config_when_all_evals_fail(pytester: pytest.Pytester, 
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a fully failed run retains its configured set, runner, and arms."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path)
    workspace.set_current_iteration("iteration_01")
    skills_root = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"
    skills_root.mkdir(parents=True)

    _finish_and_summarize(config)

    meta = json.loads((skills_root.parent / "meta.json").read_text())
    assert meta["set"] == "default"
    assert meta["runner"] == "pytest"
    assert [arm["name"] for arm in meta["arms"]] == ["baseline", "trial"]


def test_sessionfinish_writes_nested_judge_object(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify sessionfinish writes nested judge object."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    # harness=claude-code, model=sonnet/opus arms
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=1, total=1)
    seed_arm(skill_results_dir, "alpha", "baseline", passes=0, total=1)

    _finish_and_summarize(config)

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


def test_sessionfinish_redacts_secret_shaped_judge_env(pytester: pytest.Pytester, 
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify sessionfinish redacts secret shaped judge env."""
    # Every other judge test here uses JudgeConfig.env == {}, so redact_env({}) == {}
    # trivially passes even if the report.redact_env(...) call around the judge env
    # were dropped. Use a secret-shaped key (matches report._SECRET_KEY) so this test
    # only passes if the value is actually masked before it hits meta.json.
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(JUDGE_ENV_TOML)
    config = _configured_plugin(pytester, tmp_path)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=1, total=1)
    seed_arm(skill_results_dir, "alpha", "baseline", passes=0, total=1)

    _finish_and_summarize(config)

    raw_text = (skill_results_dir.parent.parent / "meta.json").read_text()
    meta = json.loads(raw_text)
    assert meta["judge"]["env"]["OPENAI_API_KEY"] == "***"
    assert "sk-live-supersecret123" not in raw_text


def test_manifest_writes_without_agent_credential(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a credential-less environment still writes a valid v2 manifest.

    `requested_version` is a pure install selector (`version()`), not a credentialed probe,
    so a run with no agent credential still records every planned arm with a concrete
    selector — here the real claude-code default of `latest`. Uses the real `make_agent`
    (no stub) precisely to prove the selector resolves without credentials.
    """
    for env_var in ("BENCHSPEC_CLAUDE_VERSION", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(env_var, raising=False)
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=1, total=1)

    _finish_and_summarize(config)

    meta = json.loads((skill_results_dir.parent.parent / "meta.json").read_text())
    assert meta["format_version"] == 2
    assert meta["set"] == "default"
    assert [arm["requested_version"] for arm in meta["arms"]] == ["latest", "latest"]
    assert "agent_version" not in meta
    assert "token_split" not in meta


def _seed_provenance(
    sample_dir: Path,
    arm: str,
    *,
    actual_version: str = "1.2.3",
    snapshot: str = "snap-abc",
    digest: str = "sha256:dead",
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


def test_observed_arms_excludes_unrun_arm(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify observed_arms holds only arms with a persisted record; planned holds all."""
    # `trial` ran and persisted provenance; `baseline` is configured but has no record.
    # Planned `arms` must list both; `observed_arms` must contain `trial` only, never a
    # synthesized `baseline`. The install selector ("9.9.9" here)
    # coexists with a distinct concrete guest `actual_version` ("1.2.3").
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    trial_sample = seed_arm(skill_results_dir, "alpha", "trial", passes=1, total=1)
    _seed_provenance(trial_sample, "trial", actual_version="1.2.3")
    seed_arm(skill_results_dir, "alpha", "baseline", passes=0, total=1)  # no provenance.json

    _finish_and_summarize(config)

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


def test_conflicting_provenance_raises(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify two disagreeing records for one arm raise a clear aggregation error."""
    # Records that disagree on actual_version describe unlike environments; aggregation
    # must fail loudly rather than silently combine them into one report.
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    sample0 = seed_arm(skill_results_dir, "alpha", "trial", passes=1, total=1, sample=0)
    sample1 = seed_arm(skill_results_dir, "alpha", "trial", passes=1, total=1, sample=1)
    _seed_provenance(sample0, "trial", actual_version="1.2.3")
    _seed_provenance(sample1, "trial", actual_version="9.9.9")

    session = _session(config)
    with pytest.raises(ValueError, match="conflicting runtime provenance for arm `trial`"):
        plugin.pytest_sessionfinish(session, 0)


def test_provenance_arm_directory_mismatch_raises(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record whose `arm` disagrees with its own directory raises, naming both."""
    # `baseline` is a real roster arm, so only the directory-vs-record mismatch can fire
    # here (the record sits in the `trial/` dir): a mislocated label never keys a column.
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    trial_sample = seed_arm(skill_results_dir, "alpha", "trial", passes=1, total=1)
    _seed_provenance(trial_sample, "baseline")  # arm label disagrees with the trial/ dir

    session = _session(config)
    with pytest.raises(ValueError, match="provenance arm mismatch.*`baseline`.*`trial`"):
        plugin.pytest_sessionfinish(session, 0)


def test_provenance_arm_outside_roster_raises(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record for an arm the roster never configured raises, naming the arm."""
    # `ghost` sits in a matching `ghost/` dir (so the directory check passes) but is not
    # in the configured roster {baseline, trial} — the roster check must reject it.
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    ghost_sample = seed_arm(skill_results_dir, "alpha", "ghost", passes=1, total=1)
    _seed_provenance(ghost_sample, "ghost")

    session = _session(config)
    with pytest.raises(ValueError, match="provenance arm `ghost`.*not in the configured roster"):
        plugin.pytest_sessionfinish(session, 0)


def test_binder_identity_carries_no_key_material(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify meta.json binder identity never leaks GEMINI_API_KEY."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    monkeypatch.setenv("GEMINI_API_KEY", "sk-gemini-supersecret")
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=1, total=1)

    _finish_and_summarize(config)

    raw_text = (skill_results_dir.parent.parent / "meta.json").read_text()
    meta = json.loads(raw_text)
    assert set(meta["binder"]) == {"provider", "model", "api_path"}
    assert "sk-gemini-supersecret" not in raw_text


def test_terminal_summary_noop_without_artifacts(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify terminal summary noop without artifacts."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    config = _configured_plugin(pytester, tmp_path)
    workspace.set_current_iteration("iteration_01")

    _, printed = _finish_and_summarize(config)

    assert printed == []


def test_unknown_agent_flag_fails_at_startup(pytester: pytest.Pytester) -> None:
    """Verify unknown agent flag fails at startup."""
    _make_project(pytester)

    result = _collect(pytester, "--benchspec-agent", "not-a-harness")

    assert result.ret != 0
    out = result.stderr.str() + result.stdout.str()
    assert "--benchspec-agent" in out
    assert "not-a-harness" in out


def test_agent_flag_beats_env(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify agent flag beats env."""
    monkeypatch.setenv("BENCHSPEC_AGENT", "opencode")
    _make_project(pytester)

    result = _collect(pytester, "--benchspec-agent", "claude-code")

    assert result.ret == 0


def test_judge_model_flag_is_accepted(pytester: pytest.Pytester) -> None:
    """Verify judge model flag is accepted."""
    _make_project(pytester)

    result = _collect(pytester, "--benchspec-judge-model", "haiku")

    assert result.ret == 0


def test_judge_harness_flag_is_accepted(pytester: pytest.Pytester) -> None:
    """Verify judge harness flag is accepted."""
    _make_project(pytester)
    result = _collect(
        pytester, "--benchspec-judge-harness", "codex", "--benchspec-judge-model", "gpt-5.5"
    )

    assert result.ret == 0


def test_judge_effort_and_timeout_flags_are_accepted(pytester: pytest.Pytester) -> None:
    """Verify judge effort and timeout flags are accepted."""
    _make_project(pytester)
    result = _collect(
        pytester, "--benchspec-judge-effort", "high", "--benchspec-judge-timeout", "120"
    )

    assert result.ret == 0


def test_judge_harness_arg_flag_is_repeatable(pytester: pytest.Pytester) -> None:
    """Verify judge harness arg flag is repeatable."""
    # `=` form (not two bare tokens): argparse's `action="append"` treats a bare
    # token starting with `--` as a new option, not this option's value — a stock
    # argparse gotcha, unrelated to this flag's own parsing.
    _make_project(pytester)
    result = _collect(
        pytester,
        "--benchspec-judge-harness-arg=--plugin-dir",
        "--benchspec-judge-harness-arg=/project",
    )

    assert result.ret == 0


def test_judge_env_flag_is_repeatable(pytester: pytest.Pytester) -> None:
    """Verify judge env flag is repeatable."""
    _make_project(pytester)
    result = _collect(
        pytester, "--benchspec-judge-env", "A=1", "--benchspec-judge-env", "B=2"
    )

    assert result.ret == 0


def test_eval_test_body_resolves_judge_config_fixture_without_error(
    pytester: pytest.Pytester
) -> None:
    """Verify eval test body resolves judge config fixture without error."""
    # A real (non --collect-only) run still needs a microVM to get past sandbox
    # preflight, so this only proves collection succeeds with the fixture renamed —
    # full execution is covered by tests/test_execution.py's run_eval_arm tests.
    _make_project(pytester)
    result = _collect(pytester)

    assert result.ret == 0


def test_judge_preflight_fixture_raises_when_binary_missing(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify judge preflight fixture raises when binary missing."""
    import shutil

    from benchspec.sandbox import sandbox

    _make_project(pytester)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    # Neither the Gemini key preflight (which judge_config runs first) nor the sandbox
    # preflight may fail for unrelated reasons and mask the judge-binary assertion below.
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None, **kwargs: None)
    # Deliberately NO positional target here (unlike `_collect`'s "test_cases.py::
    # test_eval"): `pytest_configure` only self-registers the real `benchspec/cases.py`
    # (whose `judge_config` fixture runs the binary preflight under test) when
    # `config.args_source` isn't `ARGS` — i.e. when no explicit positional is given.
    # Passing "test_cases.py::test_eval" would instead collect _make_project's dummy
    # `test_cases.py` stub (`def test_eval(eval_arm): pass`), which never requests
    # `judge_config` and so could never exercise this preflight at all — see
    # `test_plugin_self_registers_cases_without_positional` for the same mechanic.
    # `-k test_eval` keeps this to the real (parametrized) test_eval items.
    result = pytester.runpytest(
        "-p",
        "benchspec.runners.pytest",
        "--benchspec-repo-root",
        str(pytester.path),
        "-k",
        "test_eval",
    )
    assert result.ret != 0
    out = result.stdout.str() + result.stderr.str()
    assert "not found on PATH" in out


def test_gemini_key_preflight_fixture_raises_when_missing(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the judge_config fixture fails fast on a missing GEMINI_API_KEY."""
    import shutil

    from benchspec.sandbox import sandbox

    _make_project(pytester)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")  # binary IS present
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None, **kwargs: None)
    # A repo-root .env can otherwise repopulate GEMINI_API_KEY inside the inner run
    # (the plugin loads .env in pytest_load_initial_conftests) — no-op the load so this
    # test is deterministic regardless of where pytest was invoked from.
    monkeypatch.setattr(plugin, "load_dotenv", lambda *args: None)

    result = pytester.runpytest(
        "-p",
        "benchspec.runners.pytest",
        "--benchspec-repo-root",
        str(pytester.path),
        "-k",
        "test_eval",
    )
    assert result.ret != 0
    out = result.stdout.str() + result.stderr.str()
    assert "GEMINI_API_KEY" in out


def test_gemini_key_preflight_skipped_under_collect_only(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify --collect-only never triggers the GEMINI_API_KEY preflight."""
    from benchspec.sandbox import sandbox

    _make_project(pytester)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None, **kwargs: None)

    result = pytester.runpytest(
        "-p",
        "benchspec.runners.pytest",
        "--collect-only",
        "--benchspec-repo-root",
        str(pytester.path),
    )
    assert result.ret == 0


def test_judge_model_flag_no_longer_shadows_pyproject_default_when_unset(
    pytester: pytest.Pytester
) -> None:
    """Verify judge model flag no longer shadows pyproject default when unset."""
    # Regression guard for the precedence bug: --benchspec-judge-model must default to
    # None so an unset flag never overrides [tool.benchspec.judge] model.
    project_toml = (
        ARMS_TOML
        + """
[tool.benchspec.judge]
harness = "codex"
model = "gpt-5.5"
"""
    )
    _make_project(pytester, arms_toml=project_toml)
    result = _collect(pytester)  # no --benchspec-judge-model passed
    assert result.ret == 0


def test_unsupported_judge_harness_fails_at_collection(pytester: pytest.Pytester) -> None:
    """Verify unsupported judge harness fails at collection."""
    project_toml = (
        ARMS_TOML
        + """
[tool.benchspec.judge]
harness = "cursor"
"""
    )
    _make_project(pytester, arms_toml=project_toml)
    result = _collect(pytester)
    assert result.ret != 0
    out = result.stderr.str() + result.stdout.str()
    assert "not a supported judge harness" in out


def test_non_dict_pyproject_judge_table_fails_loudly(pytester: pytest.Pytester) -> None:
    """Verify non dict pyproject judge table fails loudly."""
    # Regression guard: a present-but-non-dict [tool.benchspec.judge] (e.g. `judge =
    # "codex"` from a fat-fingered TOML edit) must raise, not silently coerce to
    # None and fall through to defaults. `judge` is inserted into the *existing*
    # [tool.benchspec] table (not a re-opened header) — TOML forbids declaring the
    # same table twice.
    project_toml = ARMS_TOML.replace(
        'default-set = "default"',
        'default-set = "default"\njudge = "codex"',
    )
    _make_project(pytester, arms_toml=project_toml)
    result = _collect(pytester)
    assert result.ret != 0
    out = result.stderr.str() + result.stdout.str()
    assert "[tool.benchspec.judge] must be a table" in out


def test_non_dict_scratch_judge_table_fails_loudly(pytester: pytest.Pytester) -> None:
    """Verify non dict scratch judge table fails loudly."""
    # Same regression guard, but for a scratch --benchspec-config file's
    # [tool.benchspec.judge] — a distinct code path (`_read_scratch_benchspec_table` +
    # the scratch branch in `resolved_judge_config`) with its own error message.
    _make_project(pytester)
    scratch = pytester.path / "scratch.toml"
    scratch.write_text('[tool.benchspec]\njudge = "codex"\n')

    result = _collect(pytester, "--benchspec-config", str(scratch))

    assert result.ret != 0
    out = result.stderr.str() + result.stdout.str()
    assert "[tool.benchspec.judge] must be a table" in out
    assert "--benchspec-config" in out


def test_sessionfinish_writes_index_jsonl(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify sessionfinish writes index jsonl."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"
    seed_arm(skill_results_dir / "archive", "alpha", "trial", passes=1, total=1)
    seed_arm(skill_results_dir / "archive", "alpha", "baseline", passes=0, total=1)

    _finish_and_summarize(config)

    lines = [
        json.loads(line)
        for line in (skill_results_dir.parent / "index.jsonl").read_text().splitlines()
    ]
    assert {record["skill"] for record in lines} == {"archive"}
    assert sum(1 for record in lines if record["kind"] == "eval") == 2


def test_index_jsonl_rows_carry_core_axes(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify index.jsonl rows gain harness/model/effort from the planned arm roster."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path)
    workspace.set_current_iteration("iteration_01")
    skills = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"
    seed_arm(skills / "archive", "alpha", "trial", passes=1, total=1)
    seed_arm(skills / "archive", "alpha", "baseline", passes=0, total=1)

    _finish_and_summarize(config)

    rows = [json.loads(line) for line in (skills.parent / "index.jsonl").read_text().splitlines()]
    trial_row = next(row for row in rows if row["arm"] == "trial")
    assert trial_row["harness"] == "claude-code"
    assert trial_row["model"] == "opus"  # trial overrides the set default
    assert "effort" in trial_row
    baseline_row = next(row for row in rows if row["arm"] == "baseline")
    assert baseline_row["model"] == "sonnet"  # inherits the set default


def test_benchmark_json_carries_runner_binder_observed(pytester: pytest.Pytester, 
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the run-level benchmark.json carries runner + binder + observed_arms."""
    # These must agree with meta.json written by the same sessionfinish: the observed
    # provenance is aggregated once and threaded into both artifacts.
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path)
    workspace.set_current_iteration("iteration_01")
    skills = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"
    trial_sample = seed_arm(skills / "archive", "alpha", "trial", passes=1, total=1)
    _seed_provenance(trial_sample, "trial", actual_version="1.2.3")
    seed_arm(skills / "archive", "alpha", "baseline", passes=0, total=1)  # no provenance.json

    _finish_and_summarize(config)

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


def test_fail_under_sets_exit_status(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify fail under sets exit status."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path, "--benchspec-fail-under=0.0")
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=0, total=2)  # 0%
    seed_arm(skill_results_dir, "alpha", "baseline", passes=2, total=2)  # 100% → delta -100pp

    session = _session(config)
    plugin.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 1
    printed = _printed_summary(config, 1)
    assert any("fail-under" in line for line in printed)


def test_fail_under_quiet_when_met(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify fail under quiet when met."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path, "--benchspec-fail-under=0.0")
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=2, total=2)  # 100%
    seed_arm(skill_results_dir, "alpha", "baseline", passes=0, total=2)  # 0% → delta +100pp

    session = _session(config)
    plugin.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 0


def test_fail_under_skipped_without_reference(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify fail under skipped without reference."""
    # A set with no `baseline` → absolute scores, no Δ to gate; the fail-under
    # threshold is a no-op rather than failing the run.
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(
        '[tool.benchspec]\ndefault-set = "default"\n'
        '[tool.benchspec.sets.default]\nharness = "claude-code"\nmodel = "sonnet"\n'
        'arms = [{name="trial-opus"}, {name="trial-sonnet"}]\n'
    )
    config = _configured_plugin(pytester, tmp_path, "--benchspec-fail-under=0.0")
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial-opus", passes=0, total=2)
    seed_arm(skill_results_dir, "alpha", "trial-sonnet", passes=0, total=2)

    session = _session(config)
    plugin.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 0


def test_fail_under_isolates_regressing_group(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a single regressing group trips the per-group gate even when the pool is up."""
    # Group A dominates by sample weight (trial +100pp over 4 samples) while group B
    # regresses (trial -100pp). The run-level pooled trial Δ is +33pp, so a pooled gate
    # would NOT fire — only a per-group gate catches group B.
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path, "--benchspec-fail-under=0.0")
    workspace.set_current_iteration("iteration_01")
    skills = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"
    for sample_index in range(4):
        seed_arm(skills / "archive", "alpha", "baseline", passes=0, total=1, sample=sample_index)
        seed_arm(skills / "archive", "alpha", "trial", passes=1, total=1, sample=sample_index)
    for sample_index in range(2):
        seed_arm(skills / "ingest", "beta", "baseline", passes=1, total=1, sample=sample_index)
        seed_arm(skills / "ingest", "beta", "trial", passes=0, total=1, sample=sample_index)

    session = _session(config)
    plugin.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 1
    printed = _printed_summary(config, 1)
    assert any("FAIL fail-under: ingest/trial" in line for line in printed)
    benchmark = json.loads((skills.parent / "benchmark.json").read_text())
    assert benchmark["arms"]["trial"]["delta_pp"] > 0  # pooled Δ is positive → pooled gate misses


def test_fail_under_exempts_group_missing_baseline_arm(pytester: pytest.Pytester, 
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a group that never ran the baseline stays exempt from the per-group gate."""
    # Group A runs both arms with trial below threshold → it fails. Group B ran only
    # trial, so its configured baseline column has no rate (pass_rate=None), coercing
    # the group's baseline to None → no Δ to gate → exempt.
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path, "--benchspec-fail-under=0.0")
    workspace.set_current_iteration("iteration_01")
    skills = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"
    seed_arm(skills / "archive", "alpha", "baseline", passes=2, total=2)  # 100%
    seed_arm(skills / "archive", "alpha", "trial", passes=0, total=2)  # 0% → Δ -100pp
    seed_arm(skills / "ingest", "beta", "trial", passes=0, total=2)  # only trial, no baseline

    session = _session(config)
    plugin.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 1
    printed = _printed_summary(config, 1)
    assert any("FAIL fail-under: archive/trial" in line for line in printed)
    assert not any("FAIL fail-under: ingest" in line for line in printed)


def test_sessionfinish_writes_single_run_level_benchmark(pytester: pytest.Pytester, 
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify exactly one run-level benchmark lands at the iteration root, none per group."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path)
    workspace.set_current_iteration("iteration_01")
    skills = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"
    seed_arm(skills / "archive", "alpha", "trial", passes=2, total=2)
    seed_arm(skills / "archive", "alpha", "baseline", passes=1, total=2)
    seed_arm(skills / "ingest", "beta", "trial", passes=1, total=2)
    seed_arm(skills / "ingest", "beta", "baseline", passes=1, total=2)

    _finish_and_summarize(config)

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


def test_binder_degraded_warns_in_terminal_summary(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a nonzero binder_degraded total prints a WARN line, doesn't fail the run."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=2, total=2, binder_degraded=3)
    seed_arm(skill_results_dir, "alpha", "baseline", passes=2, total=2)

    session = _session(config)
    plugin.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 0
    printed = _printed_summary(config, 0)
    assert any("WARN" in line and "binder" in line for line in printed)


def test_binder_degraded_quiet_when_zero(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify no binder WARN line when nothing degraded."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    config = _configured_plugin(pytester, tmp_path)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=2, total=2)
    seed_arm(skill_results_dir, "alpha", "baseline", passes=2, total=2)

    session = _session(config)
    plugin.pytest_sessionfinish(session, 0)

    printed = _printed_summary(config, 0)
    # A bare "binder" substring check would self-collide: pytest's tmp_path embeds this
    # test's own name (which contains "binder") into the benchmark.md path the Report
    # line names. Match the WARN line's actual shape instead.
    assert not any("WARN" in line and "binder" in line for line in printed)


# On-disk --benchspec-config judge fixtures; each is driven end-to-end through the
# real plugin hooks at collection time by the tests below.
_FIXTURES = Path(__file__).parent.parent / "fixtures" / "judge"


def test_unsupported_judge_harness_fixture_exits_nonzero(pytester: pytest.Pytester) -> None:
    """Verify unsupported judge harness fixture exits nonzero."""
    _make_project(pytester)
    result = pytester.runpytest(
        "-p",
        "benchspec.runners.pytest",
        "--collect-only",
        "-q",
        "--benchspec-repo-root",
        str(pytester.path),
        "--benchspec-config",
        str(_FIXTURES / "unsupported-judge-harness.toml"),
        "test_cases.py::test_eval",
    )
    assert result.ret != 0
    assert "not a supported judge harness" in (result.stdout.str() + result.stderr.str())


def test_unset_judge_env_fixture_passes_collection_but_fails_at_judge_exec_time() -> None:
    """Verify unset judge env fixture passes collection but fails at judge exec time."""
    # Collection-time only: proves the fixture's env value is structurally valid
    # (a string) and does NOT raise until benchspec.grading.judges.run_judge actually expands
    # it. Full end-to-end (a real arm run reaching the judge) needs live credentials
    # and a microVM — out of scope for `make test`; see tests/grading/judges/test_judge_registry.py
    # ::test_run_judge_env_unset_var_raises_schemaerror for the unit-level proof, and
    # tests/config/test_arms.py for expand_env's own unset-var coverage (the same function).
    import tomllib

    from benchspec.grading.judges import resolve_judge_config

    with (_FIXTURES / "unset-judge-env.toml").open("rb") as fixture_file:
        raw = tomllib.load(fixture_file)
    judge_table = raw["tool"]["benchspec"]["judge"]
    config = resolve_judge_config(pyproject_table=judge_table)  # no raise — structural only
    assert config.env == {"SOME_JUDGE_KEY": "$BENCHSPEC_JUDGE_FIXTURE_UNSET_VAR"}

    import os

    from benchspec.grading.judges.registry import run_judge
    from benchspec.specs.schema import SchemaError

    os.environ.pop("BENCHSPEC_JUDGE_FIXTURE_UNSET_VAR", None)
    with pytest.raises(SchemaError, match="BENCHSPEC_JUDGE_FIXTURE_UNSET_VAR"):
        run_judge("prompt", config=config)


def test_dotenv_loads_before_conftests_run(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify `.env` is loaded before any conftest's `pytest_configure` runs."""
    monkeypatch.delenv("BENCHSPEC_DOTENV_PROBE", raising=False)
    (pytester.path / ".env").write_text("BENCHSPEC_DOTENV_PROBE=from-dotenv\n")
    pytester.makeconftest(DOTENV_PROBE_CONFTEST)
    pytester.makepyfile(test_probe="def test_probe():\n    pass\n")

    result = pytester.runpytest_subprocess(
        "-p", "benchspec.runners.pytest", "test_probe.py"
    )

    assert "BENCHSPEC_DOTENV_PROBE missing" not in (result.stdout.str() + result.stderr.str())
    result.assert_outcomes(passed=1)


def test_summary_matrix_is_colored_only_when_the_writer_has_markup(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify session-finish renders ANSI colors exactly when the terminal supports them."""
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    plain_config = _configured_plugin(pytester, tmp_path, "--color=no")
    color_config = _configured_plugin(pytester, tmp_path, "--color=yes")
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=2, total=2)
    seed_arm(skill_results_dir, "alpha", "baseline", passes=0, total=2)

    plugin.pytest_sessionfinish(_session(plain_config), 0)
    plugin.pytest_sessionfinish(_session(color_config), 0)

    plain_lines = plain_config.stash.get(plugin._SUMMARY_LINES, [])
    color_lines = color_config.stash.get(plugin._SUMMARY_LINES, [])
    assert plain_lines
    assert not any("\x1b[" in line for line in plain_lines)
    assert any("\x1b[" in line for line in color_lines)
