"""Deterministic integrity test for the labeled binder corpus (gold-label set).

Runs under `make test`, no LLM/network. Validates that the corpus is well-formed and
enforces the invariants required by the false-positive gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import NotRequired, TypedDict

import pytest
import yaml
from conftest import DrawRecord, _latency_cost_summary, _recording_call_model, binder_config_for

from benchspec.grading import binder
from benchspec.grading.binder_config import BinderConfig
from benchspec.grading.checkers import derive_text
from benchspec.specs.schema import SchemaError

CORPUS_PATH = Path(__file__).resolve().parent / "corpus.yaml"


class CorpusEntry(TypedDict):
    """One gold-labeled corpus line: a bind (with its expected checker) or a punt."""

    text: str
    gold: str
    cohort: str
    expect_checker: NotRequired[str]
    expect: NotRequired[dict[str, object]]


def _bind_entry(checker: str, item: object) -> CorpusEntry:
    """Build one gold-bind entry from a `binds` line: bare text, or a mapping with `expect`.

    Raises:
        TypeError: the line is neither a string nor a mapping.
    """
    if isinstance(item, str):
        return {"text": item, "gold": "bind", "cohort": checker, "expect_checker": checker}
    if isinstance(item, dict):
        entry: CorpusEntry = {
            "text": item["text"],
            "gold": "bind",
            "cohort": checker,
            "expect_checker": checker,
        }
        if "expect" in item:
            entry["expect"] = item["expect"]
        return entry
    raise TypeError(
        f"corpus bind entry under {checker!r} must be text or a mapping, got {type(item).__name__}"
    )


def _load_corpus(path: Path) -> list[CorpusEntry]:
    """Flatten `corpus.yaml`'s binds-by-checker and punts-by-class tables into entries."""
    raw = yaml.safe_load(path.read_text())
    corpus: list[CorpusEntry] = []
    for checker, texts in raw["binds"].items():
        for item in texts:
            corpus.append(_bind_entry(checker, item))
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
            f"skill_invoked entry {entry['text'][:50]}... targets "
            f"{entry['expect_checker']!r}"
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
    rows: list[DrawRecord] = [
        {"test": "fields", "source": "regex", "model": None, "attempts": 0,
         "latency_ms": None, "prompt_tokens": None, "output_tokens": None},
        {"test": "fields", "source": "gemini", "model": "gemini-3.5-flash-lite", "attempts": 1,
         "latency_ms": 120.0, "prompt_tokens": 500, "output_tokens": 20},
        {"test": "fields", "source": "gemini", "model": "gemini-3.5-flash-lite", "attempts": 1,
         "latency_ms": 140.0, "prompt_tokens": 500, "output_tokens": 20},
    ]

    summary = _latency_cost_summary(rows)

    assert summary["regex_fast_path_count"] == 1
    assert summary["model_call_count"] == 2
    assert summary["latency_ms_mean"] == pytest.approx(130.0)
    assert summary["total_prompt_tokens"] == 1000
    assert summary["total_output_tokens"] == 40
    assert summary["estimated_cost_usd"] == pytest.approx(1.0 * 0.0003 + 0.04 * 0.0025)


def test_latency_cost_summary_prices_openrouter_rows_and_leaves_unknown_models_unpriced() -> None:
    """Verify cost follows the resolved model: OpenRouter's slug is priced, a stranger is not."""
    priced: list[DrawRecord] = [
        {"test": "fields", "source": "openrouter", "model": "google/gemini-3.5-flash-lite",
         "attempts": 1, "latency_ms": 100.0, "prompt_tokens": 1000, "output_tokens": 0},
    ]
    unpriced: list[DrawRecord] = [
        *priced,
        {"test": "fields", "source": "openrouter", "model": "google/gemini-3.5-flash",
         "attempts": 1, "latency_ms": 100.0, "prompt_tokens": 1000, "output_tokens": 0},
    ]

    assert _latency_cost_summary(priced)["estimated_cost_usd"] == pytest.approx(0.0003)
    assert _latency_cost_summary(unpriced)["estimated_cost_usd"] is None
    assert _latency_cost_summary(unpriced)["model_call_count"] == 2


def test_recording_call_model_delegates_to_the_configured_transport_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the corpus's recording call_model routes by the resolved config, recording replies."""
    captured: dict[str, str] = {}

    def fake_call_gemini(
        prompt: str, *, timeout: float = 60, model: str = binder.GEMINI_BINDER_MODEL
    ) -> binder.BinderReply:
        """Stand in for the transport, recording the model it was asked for."""
        captured["model"] = model
        return binder.BinderReply(text="{}", prompt_tokens=0, output_tokens=0, latency_ms=0.0)

    monkeypatch.setattr(binder, "_call_gemini", fake_call_gemini)
    sink: list[binder.BinderReply] = []

    call_model = _recording_call_model(sink, BinderConfig(model="gemini-3.1-flash"))
    call_model("prompt", timeout=60)

    assert captured["model"] == "gemini-3.1-flash"
    assert len(sink) == 1


def test_binder_config_for_reads_pyproject_and_honors_the_model_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the corpus resolves `[tool.benchspec.binder]` and lets BENCHSPEC_BINDER_MODEL win.

    This is where the model env-override behavior lives — the production transports take
    `model` as a plain keyword with no env fallback; only this corpus-side resolver reads
    the variable.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.benchspec.binder]\nprovider = "openrouter"\n'
    )

    resolved = binder_config_for(tmp_path)
    monkeypatch.setenv("BENCHSPEC_BINDER_MODEL", "google/gemini-3.5-flash")
    overridden = binder_config_for(tmp_path)

    assert resolved == BinderConfig(provider="openrouter", model="google/gemini-3.5-flash-lite")
    assert overridden == BinderConfig(provider="openrouter", model="google/gemini-3.5-flash")


def test_binder_config_for_rejects_an_unqualified_override_under_openrouter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the env override is validated like any other layer."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.benchspec.binder]\nprovider = "openrouter"\n'
    )
    monkeypatch.setenv("BENCHSPEC_BINDER_MODEL", "gemini-3.5-flash")

    with pytest.raises(SchemaError, match="vendor-qualified"):
        binder_config_for(tmp_path)
