"""Deterministic integrity test for the labeled binder corpus (gold-label set).

Runs under `make test`, no LLM/network. Validates that the corpus is well-formed
and enforces the invariants required by the false-positive gate.
"""

from __future__ import annotations

import yaml
from pathlib import Path

from evalspec.checkers import derive_text


CORPUS_PATH = Path(__file__).resolve().parent / "binder_corpus.yaml"


def _load_corpus(path: Path) -> list[dict]:
    """Reconstruct each entry's positional `gold`/`cohort`/`expect_checker` from its place.

    Under `binds`, the sub-key is the checker the assertion should bind to (so `cohort` and
    `expect_checker` are both that sub-key); under `punts`, the sub-key is the punt class.
    Bind entries may be bare assertion strings or mappings with `text` plus expected fields.
    """
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
                entry = {"text": item, "gold": "bind", "cohort": checker, "expect_checker": checker}
            corpus.append(entry)
    for cohort, texts in raw["punts"].items():
        for text in texts:
            corpus.append({"text": text, "gold": "punt", "cohort": cohort})
    return corpus


CORPUS = _load_corpus(CORPUS_PATH)

_CHECKERS = {"file_exists", "glob_count", "sha256_match", "frontmatter_has", "regex", "skill_invoked"}


def test_corpus_nontrivial():
    """Corpus must have enough entries to be meaningful (≥50)."""
    assert len(CORPUS) >= 50, f"Corpus has {len(CORPUS)} entries; expected ≥50"


def test_entry_shapes():
    """Each corpus entry must have the required shape and valid values."""
    for i, e in enumerate(CORPUS):
        assert isinstance(e, dict), f"Entry {i} is not a dict: {type(e)}"
        assert "text" in e, f"Entry {i} missing 'text' field"
        assert "gold" in e, f"Entry {i} missing 'gold' field"
        assert "cohort" in e, f"Entry {i} missing 'cohort' field"

        assert e["text"].strip(), f"Entry {i}: text must be non-empty after strip"
        assert e["gold"] in ("bind", "punt"), f"Entry {i}: gold must be 'bind' or 'punt', got {e['gold']!r}"
        assert e["cohort"] in (_CHECKERS | {"persistence", "semantic"}), (
            f"Entry {i}: cohort must be a checker name or a punt class (persistence, semantic), "
            f"got {e['cohort']!r}"
        )

        # Bind entries REQUIRE expect_checker (== their checker sub-key); punts MUST NOT have it.
        if e["gold"] == "bind":
            assert "expect_checker" in e, f"Entry {i} (bind): missing expect_checker"
            assert e["expect_checker"] in _CHECKERS, (
                f"Entry {i}: expect_checker {e['expect_checker']!r} not in {_CHECKERS}"
            )
            if "expect" in e:
                assert isinstance(e["expect"], dict), (
                    f"Entry {i}: expect must be a dict, got {type(e['expect'])}"
                )
                assert e["expect"], f"Entry {i}: expect must be non-empty when present"
        else:
            assert "expect_checker" not in e, (
                f"Entry {i} (punt): must not have expect_checker, found {e.get('expect_checker')!r}"
            )
            assert "expect" not in e, (
                f"Entry {i} (punt): must not have expect, found {e.get('expect')!r}"
            )


def test_no_persistence_entry_is_bind():
    """The persistence cohort (presence/persistence/negation) defines the false-positive
    gate cohort; a persistence entry marked `bind` would be a corpus authoring bug that
    masks the gate.
    """
    persistence_entries = [e for e in CORPUS if e["cohort"] == "persistence"]
    assert persistence_entries, "Corpus must have at least some persistence entries"
    for e in persistence_entries:
        assert e["gold"] == "punt", (
            f"Persistence entry {e['text'][:50]}... is marked gold={e['gold']!r}; "
            "persistence entries must ALWAYS be punt"
        )


def test_skill_invoked_binds_present():
    """The skill_invoked checker must be exercised by ≥6 bind entries (one per suite) —
    the synthesized `Skill X invoked` activation assertions.
    """
    acts = [e for e in CORPUS if e["cohort"] == "skill_invoked"]
    assert len(acts) >= 6, (
        f"skill_invoked has {len(acts)} bind entries; expected ≥6 (one per suite)"
    )
    for e in acts:
        assert e["gold"] == "bind", (
            f"skill_invoked entry {e['text'][:50]}... is marked gold={e['gold']!r}; must be bind"
        )
        assert e["expect_checker"] == "skill_invoked", (
            f"skill_invoked entry {e['text'][:50]}... targets {e['expect_checker']!r}"
        )


def test_has_persistence_and_semantic_punts():
    """The corpus must have both persistence and semantic punt cohorts to exercise the gate."""
    punt_cohorts = {e["cohort"] for e in CORPUS if e["gold"] == "punt"}
    assert "persistence" in punt_cohorts, "Corpus must have persistence punt entries"
    assert "semantic" in punt_cohorts, "Corpus must have semantic punt entries"


def test_derive_text_safe_for_skill_invoked():
    """A skill_invoked spec derives text cleanly without crashing (KeyError guard)."""
    result = derive_text({"checker": "skill_invoked", "skill": "ingest"})
    assert isinstance(result, str), f"derive_text returned {type(result)}, expected str"
    assert "ingest" in result, f"derive_text result {result!r} should contain 'ingest'"


def test_para_compound_punts_with_decomposed_children():
    """Flat seven-PARA-directories line stays a punt while its seven file_exists atoms are gold:bind."""
    flat_para = [e for e in CORPUS if "contains all seven numbered PARA directories" in e["text"]]
    para_children = [
        e for e in CORPUS
        if e["gold"] == "bind"
        and e["expect_checker"] == "file_exists"
        and "directory exists under ./" in e["text"]
    ]

    assert flat_para, "the flat seven-PARA-directories compound entry is missing"
    assert all(e["gold"] == "punt" for e in flat_para), (
        "the flat seven-PARA-directories line must stay a punt"
    )
    assert len(para_children) >= 7, (
        f"expected >=7 decomposed file_exists children for the PARA punt, "
        f"got {len(para_children)}"
    )


def test_index_compound_punts_with_decomposed_children():
    """Flat index.md line stays a punt while its file_exists + two regex atoms are gold:bind."""
    flat_index = [
        e for e in CORPUS
        if "opens with a '# Index' heading" in e["text"]
        and "_Last updated" in e["text"]
    ]
    index_file_child = [
        e for e in CORPUS
        if e["gold"] == "bind" and e["expect_checker"] == "file_exists"
        and e["text"].strip() == "./.meta/index.md exists"
    ]
    index_regex_children = [
        e for e in CORPUS
        if e["gold"] == "bind" and e["expect_checker"] == "regex"
        and ("matches '# Index'" in e["text"] or "line beginning '_Last updated'" in e["text"])
    ]

    assert flat_index, "the flat index.md compound entry is missing"
    assert all(e["gold"] == "punt" for e in flat_index), (
        "the flat index.md compound line must stay a punt"
    )
    assert index_file_child, "missing the decomposed './.meta/index.md exists' file_exists child"
    assert len(index_regex_children) >= 2, (
        f"expected >=2 decomposed regex children for the index.md punt, "
        f"got {len(index_regex_children)}"
    )
