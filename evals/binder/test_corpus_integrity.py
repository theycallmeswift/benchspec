"""Deterministic integrity test for the labeled binder corpus (gold-label set).

Runs under `make test`, no LLM/network. Validates that the corpus is well-formed and
enforces the invariants required by the false-positive gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import conftest
import pytest
import yaml
from conftest import _latency_cost_summary, _recording_call_model

from evalspec.grading import binder
from evalspec.grading.checkers import derive_text

CORPUS_PATH = Path(__file__).resolve().parent / "corpus.yaml"


def _load_corpus(path: Path) -> list[dict]:
    """Load corpus."""
    raw = yaml.safe_load(path.read_text())
    corpus = []
    for checker, texts in raw["binds"].items():
        for item in texts:
            if isinstance(item, dict):
                entry = {
                    "text": item["text"],
                    "gold": "bind",
                    "cohort": checker,
                    "expect_checker": checker,
                }
                if "expect" in item:
                    entry["expect"] = item["expect"]
            else:
                entry = {
                    "text": item,
                    "gold": "bind",
                    "cohort": checker,
                    "expect_checker": checker,
                }
            corpus.append(entry)
    for cohort, texts in raw["punts"].items():
        for text in texts:
            corpus.append({"text": text, "gold": "punt", "cohort": cohort})
    return corpus


CORPUS = _load_corpus(CORPUS_PATH)

_CHECKERS = {
    "file_exists",
    "not_file_exists",
    "glob_count",
    "sha256_match",
    "frontmatter_has",
    "regex",
    "skill_invoked",
    "not_skill_invoked",
}


def test_corpus_nontrivial() -> None:
    """Corpus must have enough entries to be meaningful (≥50)."""
    assert len(CORPUS) >= 50, f"Corpus has {len(CORPUS)} entries; expected ≥50"


def test_entry_shapes() -> None:
    """Each corpus entry must have the required shape and valid values."""
    for index, entry in enumerate(CORPUS):
        assert isinstance(entry, dict), f"Entry {index} is not a dict: {type(entry)}"
        assert "text" in entry, f"Entry {index} missing 'text' field"
        assert "gold" in entry, f"Entry {index} missing 'gold' field"
        assert "cohort" in entry, f"Entry {index} missing 'cohort' field"

        assert entry["text"].strip(), f"Entry {index}: text must be non-empty after strip"
        assert entry["gold"] in ("bind", "punt"), (
            f"Entry {index}: gold must be 'bind' or 'punt', got {entry['gold']!r}"
        )
        assert entry["cohort"] in (_CHECKERS | {"persistence", "semantic"}), (
            f"Entry {index}: cohort must be a checker name or a punt class "
            f"(persistence, semantic), got {entry['cohort']!r}"
        )

        # Bind entries REQUIRE expect_checker (== their checker sub-key); punts MUST NOT have it.
        if entry["gold"] == "bind":
            assert "expect_checker" in entry, f"Entry {index} (bind): missing expect_checker"
            assert entry["expect_checker"] in _CHECKERS, (
                f"Entry {index}: expect_checker {entry['expect_checker']!r} not in {_CHECKERS}"
            )
            if "expect" in entry:
                assert isinstance(entry["expect"], dict), (
                    f"Entry {index}: expect must be a dict, got {type(entry['expect'])}"
                )
                assert entry["expect"], f"Entry {index}: expect must be non-empty when present"
        else:
            assert "expect_checker" not in entry, (
                f"Entry {index} (punt): must not have expect_checker, "
                f"found {entry.get('expect_checker')!r}"
            )
            assert "expect" not in entry, (
                f"Entry {index} (punt): must not have expect, found {entry.get('expect')!r}"
            )


def test_no_persistence_entry_is_bind() -> None:
    """Ensure persistence assertions stay semantic punts, not deterministic binds."""
    persistence_entries = [entry for entry in CORPUS if entry["cohort"] == "persistence"]
    assert persistence_entries, "Corpus must have at least some persistence entries"
    for entry in persistence_entries:
        assert entry["gold"] == "punt", (
            f"Persistence entry {entry['text'][:50]}... is marked gold={entry['gold']!r}; "
            "persistence entries must ALWAYS be punt"
        )


def test_skill_invoked_binds_present() -> None:
    """Ensure the corpus exercises `skill_invoked` bind entries for every suite.

    The synthesized `Skill X invoked` activation assertions should bind, not punt.
    """
    acts = [entry for entry in CORPUS if entry["cohort"] == "skill_invoked"]
    assert len(acts) >= 6, (
        f"skill_invoked has {len(acts)} bind entries; expected ≥6 (one per suite)"
    )
    for entry in acts:
        assert entry["gold"] == "bind", (
            f"skill_invoked entry {entry['text'][:50]}... is marked "
            f"gold={entry['gold']!r}; must be bind"
        )
        assert entry["expect_checker"] == "skill_invoked", (
            f"skill_invoked entry {entry['text'][:50]}... targets {entry['expect_checker']!r}"
        )


def test_negative_matchers_bind_present() -> None:
    """Ensure the corpus exercises the negative matchers so the gate guards their binding.

    Both bind via the model now (no offline recognizer); they must stay binds, never punts,
    and target their own checker.
    """
    for checker in ("not_skill_invoked", "not_file_exists"):
        entries = [entry for entry in CORPUS if entry["cohort"] == checker]
        assert entries, f"{checker} has no corpus bind entries"
        for entry in entries:
            assert entry["gold"] == "bind", (
                f"{checker} entry {entry['text'][:50]}... is marked gold={entry['gold']!r}; "
                "must be bind"
            )
            assert entry["expect_checker"] == checker, (
                f"{checker} entry {entry['text'][:50]}... targets {entry['expect_checker']!r}"
            )


def test_has_persistence_and_semantic_punts() -> None:
    """Ensure punt examples cover persistence and semantic assertions."""
    punt_cohorts = {entry["cohort"] for entry in CORPUS if entry["gold"] == "punt"}
    assert "persistence" in punt_cohorts, "Corpus must have persistence punt entries"
    assert "semantic" in punt_cohorts, "Corpus must have semantic punt entries"


def test_derive_text_safe_for_skill_invoked() -> None:
    """A skill_invoked spec derives text cleanly without crashing (KeyError guard)."""
    result = derive_text({"checker": "skill_invoked", "skill": "ingest"})
    assert isinstance(result, str), f"derive_text returned {type(result)}, expected str"
    assert "ingest" in result, f"derive_text result {result!r} should contain 'ingest'"


def test_para_compound_punts_with_decomposed_children() -> None:
    """Keep the compound PARA assertion as a punt while children bind."""
    flat_para = [
        entry for entry in CORPUS if "contains all seven numbered PARA directories" in entry["text"]
    ]
    para_children = [
        entry
        for entry in CORPUS
        if entry["gold"] == "bind"
        and entry["expect_checker"] == "file_exists"
        and "directory exists under ./" in entry["text"]
    ]

    assert flat_para, "the flat seven-PARA-directories compound entry is missing"
    assert all(entry["gold"] == "punt" for entry in flat_para), (
        "the flat seven-PARA-directories line must stay a punt"
    )
    assert len(para_children) >= 7, (
        f"expected >=7 decomposed file_exists children for the PARA punt, got {len(para_children)}"
    )


def test_index_compound_punts_with_decomposed_children() -> None:
    """Keep compound index assertions as punts while child checks bind."""
    flat_index = [
        entry
        for entry in CORPUS
        if "opens with a '# Index' heading" in entry["text"] and "_Last updated" in entry["text"]
    ]
    index_file_child = [
        entry
        for entry in CORPUS
        if entry["gold"] == "bind"
        and entry["expect_checker"] == "file_exists"
        and entry["text"].strip() == "./.meta/index.md exists"
    ]
    index_regex_children = [
        entry
        for entry in CORPUS
        if entry["gold"] == "bind"
        and entry["expect_checker"] == "regex"
        and (
            "matches '# Index'" in entry["text"]
            or "line beginning '_Last updated'" in entry["text"]
        )
    ]

    assert flat_index, "the flat index.md compound entry is missing"
    assert all(entry["gold"] == "punt" for entry in flat_index), (
        "the flat index.md compound line must stay a punt"
    )
    assert index_file_child, "missing the decomposed './.meta/index.md exists' file_exists child"
    assert len(index_regex_children) >= 2, (
        f"expected >=2 decomposed regex children for the index.md punt, "
        f"got {len(index_regex_children)}"
    )


@pytest.mark.parametrize(
    ("case_label", "flat_substring", "checker", "child_substring"),
    [
        (
            "meta-contents",
            "exists and contains index.md (a file)",
            "file_exists",
            "The ./.meta/logs/ directory exists",
        ),
        (
            "meta-contents",
            "exists and contains index.md (a file)",
            "file_exists",
            "The ./.meta/templates/ directory exists",
        ),
        (
            "six-templates",
            "contains all six template files named exactly",
            "glob_count",
            "./.meta/templates/*.md",
        ),
        (
            "templates-restated",
            "previously-missing ./.meta/templates/ now exists and contains all six template files",
            "file_exists",
            "./.meta/templates/ directory exists",
        ),
        (
            "para-restated",
            "previously-missing PARA folders now all exist",
            "file_exists",
            "The 2. Entities/ directory exists under ./",
        ),
        (
            "whoami-frontmatter",
            "its frontmatter created and updated",
            "file_exists",
            "./1. Profile/whoami.md exists",
        ),
        (
            "whoami-frontmatter",
            "its frontmatter created and updated",
            "frontmatter_has",
            "./1. Profile/whoami.md frontmatter has a 'created' key",
        ),
        (
            "template-frontmatter",
            "begins with YAML frontmatter declaring at least the keys",
            "frontmatter_has",
            "entity-person.md frontmatter has a 'title' key",
        ),
        (
            "dated-log",
            "dated log file named YYYY-MM-DD.md",
            "glob_count",
            "./.meta/logs/*.md",
        ),
        (
            "dated-log",
            "dated log file named YYYY-MM-DD.md",
            "regex",
            "./.meta/logs/{TODAY}.md has a line containing 'bootstrap | '",
        ),
        (
            "spec-full-set",
            "with the full skim-first section set",
            "glob_count",
            "./docs/specs/{TODAY}-*.md",
        ),
        (
            "spec-sections",
            "contains all of these H2 sections: Problem",
            "regex",
            "'## Problem'",
        ),
    ],
)
def test_semantic_compound_punts_with_decomposed_children(
    case_label: str, flat_substring: str, checker: str, child_substring: str
) -> None:
    """Each compound semantic punt stays a punt while its atomic children bind.

    Mirrors `test_para_compound_punts_with_decomposed_children` and
    `test_index_compound_punts_with_decomposed_children`: the compound line must defer
    to the judge, while the single-fact child proves an eval author can decompose it for
    deterministic grading. Restated/dedup pairings (`templates-restated`, `para-restated`)
    assert the same contract against children reused from an earlier decomposition.
    """
    flat_entries = [entry for entry in CORPUS if flat_substring in entry["text"]]
    assert flat_entries, (
        f"{case_label}: flat compound entry containing {flat_substring!r} is missing"
    )
    assert all(entry["gold"] == "punt" for entry in flat_entries), (
        f"{case_label}: the flat compound line {flat_substring!r} must stay a punt"
    )

    children = [
        entry
        for entry in CORPUS
        if entry["gold"] == "bind"
        and entry["expect_checker"] == checker
        and child_substring in entry["text"]
    ]
    assert children, (
        f"{case_label}: missing decomposed {checker} child containing {child_substring!r}"
    )


def test_latency_cost_summary_excludes_regex_fast_path_rows() -> None:
    """Verify regex-sourced rows are excluded from latency/token/cost aggregates."""
    rows = [
        {
            "source": "regex",
            "attempts": 0,
            "latency_ms": None,
            "prompt_tokens": None,
            "output_tokens": None,
        },
        {
            "source": "gemini",
            "attempts": 1,
            "latency_ms": 120.0,
            "prompt_tokens": 500,
            "output_tokens": 20,
        },
        {
            "source": "gemini",
            "attempts": 1,
            "latency_ms": 140.0,
            "prompt_tokens": 500,
            "output_tokens": 20,
        },
    ]

    summary = _latency_cost_summary(rows)

    assert summary["regex_fast_path_count"] == 1
    assert summary["gemini_count"] == 2
    assert summary["latency_ms_mean"] == pytest.approx(130.0)
    assert summary["total_prompt_tokens"] == 1000
    assert summary["total_output_tokens"] == 40


def test_recording_call_model_honors_evalspec_binder_model_env_override(
    monkeypatch: object,
) -> None:
    """Verify the corpus's recording call_model reads EVALSPEC_BINDER_MODEL, not `_call_gemini`.

    This is where the model env-override behavior lives — `_call_gemini` itself takes
    `model` as a plain keyword with no env fallback; only this corpus-side wrapper
    reads the variable, and only this wrapper needs a test for it.
    """
    captured = {}

    def fake_call_gemini(prompt: object, *, timeout: object = 60, model: object = None) -> object:
        captured["model"] = model
        return binder.GeminiReply(text="{}", prompt_tokens=0, output_tokens=0, latency_ms=0.0)

    monkeypatch.setenv("EVALSPEC_BINDER_MODEL", "gemini-3.1-flash")
    monkeypatch.setattr(binder, "_call_gemini", fake_call_gemini)

    call_model = _recording_call_model([])
    call_model("prompt", timeout=60)

    assert captured["model"] == "gemini-3.1-flash"


def test_recording_call_model_uses_corpus_timeout_not_callers_timeout(monkeypatch: object) -> None:
    """The corpus's recording call_model overrides bind()'s 60s with the 15s corpus cap.

    `bind()` always calls `call_model(prompt, timeout=60)` (`src/evalspec/grading/binder.py:370`);
    production stays at 60s there. Only this corpus-side wrapper substitutes the
    suite-constant 15s cap when it delegates to `_call_gemini`.
    """
    captured = {}

    def fake_call_gemini(prompt: object, *, timeout: object = 60, model: object = None) -> object:
        captured["timeout"] = timeout
        return binder.GeminiReply(text="{}", prompt_tokens=0, output_tokens=0, latency_ms=0.0)

    monkeypatch.setattr(binder, "_call_gemini", fake_call_gemini)

    call_model = _recording_call_model([])
    call_model("prompt", timeout=60)  # bind() always passes 60 here

    assert captured["timeout"] == 15


def test_bind_resilient_exhausts_retries_and_reports_final_error(monkeypatch: object) -> None:
    """Two transient RuntimeErrors exhaust the retry budget; the final error survives."""
    attempt_count = {"calls": 0}

    def failing_call_gemini(
        prompt: object, *, timeout: object = 60, model: object = None
    ) -> object:
        attempt_count["calls"] += 1
        raise RuntimeError(f"Gemini API transport failure: attempt {attempt_count['calls']}")

    monkeypatch.setattr(binder, "_call_gemini", failing_call_gemini)

    attempt = conftest._bind_resilient(
        "the summary faithfully reflects the three key facts from the source", sink=[]
    )

    assert attempt.binding is None
    assert attempt.attempts == 2
    assert isinstance(attempt.error, RuntimeError)
    assert "attempt 2" in str(attempt.error)
    assert attempt.elapsed_ms >= 0


def test_bind_resilient_succeeds_after_one_transient_error(monkeypatch: object) -> None:
    """One transient RuntimeError followed by a valid reply succeeds; both attempts count."""
    attempt_count = {"calls": 0}

    def flaky_call_gemini(prompt: object, *, timeout: object = 60, model: object = None) -> object:
        attempt_count["calls"] += 1
        if attempt_count["calls"] == 1:
            raise RuntimeError("Gemini API transport failure: cold start")
        return binder.GeminiReply(
            text='{"punt": true, "reason": "semantic"}',
            prompt_tokens=10,
            output_tokens=2,
            latency_ms=5.0,
        )

    monkeypatch.setattr(binder, "_call_gemini", flaky_call_gemini)

    sink: list = []
    attempt = conftest._bind_resilient(
        "the summary faithfully reflects the three key facts from the source", sink=sink
    )

    assert attempt.error is None
    assert attempt.attempts == 2
    assert attempt.binding is None  # the model punted
    assert len(sink) == 1
    assert attempt.elapsed_ms >= 0


def test_bind_resilient_lets_binder_auth_error_propagate_uncaught(monkeypatch: object) -> None:
    """BinderAuthError bypasses the retry loop entirely — a bad credential must fail loud.

    Deliberately not a RuntimeError subclass (see BinderAuthError's own docstring in
    binder.py), so `_bind_resilient`'s `except RuntimeError` must never catch it.
    """

    def rejecting_call_gemini(
        prompt: object, *, timeout: object = 60, model: object = None
    ) -> object:
        raise binder.BinderAuthError("Gemini API rejected the credential (HTTP 401): bad key")

    monkeypatch.setattr(binder, "_call_gemini", rejecting_call_gemini)

    with pytest.raises(binder.BinderAuthError):
        conftest._bind_resilient(
            "the summary faithfully reflects the three key facts from the source", sink=[]
        )


def test_binder_corpus_fails_not_skips_when_gemini_exhausts_retries(
    pytester: object, monkeypatch: object
) -> None:
    """A draw that exhausts its Gemini retries must fail its pytest item, never skip it.

    Runs the real evals/binder/test_corpus.py items in-process against the real
    corpus.yaml — not a synthetic pytester project — with binder._call_gemini forced to
    always raise. `binder` is a proper package module (evalspec.grading.binder), so this
    monkeypatch carries through runpytest_inprocess via the shared sys.modules entry
    (conftest.py itself does NOT carry through: pytest deletes and reimports any
    unpackaged "conftest" module by name on every load, see _importconftest in
    _pytest/config/__init__.py, so a patch on THIS module's `conftest` reference would be
    invisible to the nested run — hence targeting `binder` here, not `conftest`).
    `--rootdir` pins the nested run's rootpath to pytester.path, so its JSONL output
    lands inside this test's own isolated temp dir rather than the real repo's
    tmp/binder_results/ (which pytest_configure wipes at the start of every
    binder_corpus-marked run — piggybacking on the real directory would risk deleting a
    developer's own live `make evals` artifacts).
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")  # any non-empty value satisfies preflight

    def always_fails(prompt: object, *, timeout: object = 60, model: object = None) -> object:
        raise RuntimeError("Gemini API transport failure: forced for this test")

    monkeypatch.setattr(binder, "_call_gemini", always_fails)
    test_corpus_path = Path(__file__).resolve().parent / "test_corpus.py"

    result = pytester.runpytest_inprocess(
        "--rootdir",
        str(pytester.path),
        "-m",
        "binder_corpus",
        "-k",
        "persistence and test_binder_corpus_blocks_punt_leaks",
        "--maxfail=1",
        str(test_corpus_path),
    )

    result.assert_outcomes(failed=1, passed=0, skipped=0)

    rows = [
        json.loads(line)
        for result_path in (pytester.path / "tmp" / "binder_results").glob("results-*.jsonl")
        for line in result_path.read_text().splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["result"] == "error"
    assert row["error_type"] == "RuntimeError"
    assert "forced for this test" in row["error_message"]
    assert row["elapsed_ms"] >= 0


def test_binder_corpus_stops_after_maxfail_three(pytester: object, monkeypatch: object) -> None:
    """`--maxfail=3` (the Makefile's circuit breaker) stops the run after 3 failed draws.

    Same in-process real-corpus setup as
    test_binder_corpus_fails_not_skips_when_gemini_exhausts_retries, with --maxfail=3
    instead of 1 so a fourth persistence draw never runs once three have failed.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    def always_fails(prompt: object, *, timeout: object = 60, model: object = None) -> object:
        raise RuntimeError("Gemini API transport failure: forced for this test")

    monkeypatch.setattr(binder, "_call_gemini", always_fails)
    test_corpus_path = Path(__file__).resolve().parent / "test_corpus.py"

    result = pytester.runpytest_inprocess(
        "--rootdir",
        str(pytester.path),
        "-m",
        "binder_corpus",
        "-k",
        "persistence and test_binder_corpus_blocks_punt_leaks",
        "--maxfail=3",
        str(test_corpus_path),
    )

    result.assert_outcomes(failed=3, passed=0, skipped=0)
    result.stdout.fnmatch_lines(["*stopping after 3 failures*"])
