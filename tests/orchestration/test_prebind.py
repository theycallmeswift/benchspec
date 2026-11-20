"""The controller's once-per-run binding: which texts it binds, and how cells read the table."""

from __future__ import annotations

import threading
from pathlib import Path
from textwrap import dedent

import pytest

from benchspec.config.arms import Arm
from benchspec.orchestration import prebind
from benchspec.orchestration.execution import _bind_all
from benchspec.specs import mdformat
from benchspec.specs.discovery import EvalCase

BASELINE = Arm("baseline", "claude-code", "sonnet")
TRIAL = Arm("trial", "claude-code", "sonnet")

CHECKLIST = dedent("""\
    ---
    ---

    ## Prompt

    Greet Bob.

    ## Assertions

    - [ ] ./out.md exists
    - [ ] Skill `hello` invoked
      - if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}
    - [ ] Skill `other` invoked
      - if: {BENCHSPEC_ARM} == "never"
    - [ ] ./{TODAY}.md exists
""")


@pytest.fixture
def eval_case(tmp_path: Path) -> EvalCase:
    """A parsed eval with an unscoped line, a line scoped off the baseline, and a dead line."""
    eval_dir = tmp_path / "skills" / "myskill" / "evals" / "alpha"
    eval_dir.mkdir(parents=True)
    eval_file = eval_dir / "eval.md"
    eval_file.write_text(CHECKLIST)

    return EvalCase(
        group="myskill",
        eval_dir=eval_dir,
        eval_file=eval_file,
        eval=mdformat.parse_eval_md(eval_file),
    )


def test_each_active_text_appears_once_across_arms(eval_case: EvalCase) -> None:
    """Scoped lines appear when any arm grades them, never when no arm does; TODAY is filled."""
    texts = prebind.unique_active_texts(
        [(eval_case, BASELINE), (eval_case, TRIAL)],
        baseline="baseline",
        eval_set="",
        today="2099-01-01",
    )

    assert texts == ["./out.md exists", "./2099-01-01.md exists", "Skill `hello` invoked"]


def test_a_line_scoped_off_the_only_arm_is_absent(eval_case: EvalCase) -> None:
    """With only the baseline arm, the line scoped off the baseline is never bound."""
    texts = prebind.unique_active_texts(
        [(eval_case, BASELINE)], baseline="baseline", eval_set="", today="2099-01-01"
    )

    assert "Skill `hello` invoked" not in texts


def test_a_failed_bind_is_omitted_and_a_punt_is_stored() -> None:
    """RuntimeError leaves the text out of the table; None is the binder's own outcome."""

    def flaky(text: str) -> dict | None:
        if text == "boom":
            raise RuntimeError("blip")
        return {"kind": "regex"} if text == "spec" else None

    table = prebind.bind_unique(["spec", "punt", "boom"], flaky)

    assert table == {"spec": {"kind": "regex"}, "punt": None}


def test_a_hit_never_reaches_the_live_binder_and_a_miss_does() -> None:
    """Key presence, not truthiness, decides: a stored punt is a hit."""
    live: list[str] = []

    def record(text: str) -> None:
        live.append(text)

    bind = prebind.table_or_live({"spec": {"kind": "regex"}, "punt": None}, record)

    assert bind("spec") == {"kind": "regex"}
    assert bind("punt") is None
    assert live == []
    bind("missing")
    assert live == ["missing"]


def test_a_cell_rebinds_a_failed_text_and_counts_the_degrade() -> None:
    """The failed pre-bind leaves the text out, so the cell's own live bind degrades as today."""

    def failing(_text: str) -> dict | None:
        raise RuntimeError("blip")

    bind = prebind.table_or_live({"ok": None}, failing)

    specs, binder_degraded = _bind_all(["ok", "lost"], bind)

    assert specs == [None, None]
    assert binder_degraded == 1


def test_in_flight_binds_never_exceed_the_concurrency() -> None:
    """Calls are held at a barrier, so the cap itself, not timing, bounds the peak."""
    cap = 3
    lock = threading.Lock()
    in_flight = peak = 0
    release = threading.Event()

    def hold(_text: str) -> None:
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
            if in_flight == cap:
                release.set()
        release.wait(timeout=5)
        with lock:
            in_flight -= 1

    prebind.bind_unique([str(index) for index in range(cap * 4)], hold, concurrency=cap)

    assert peak == cap
