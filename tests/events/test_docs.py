"""The event stream documented in `docs/results.md` matches `benchspec.events.schema`."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from benchspec.events.schema import (
    EVENT_TYPES,
    PHASES,
    ArmInfo,
    CapFields,
    Envelope,
    PlannedCell,
    StoppedField,
    TimingFields,
)

RESULTS_MD = (Path(__file__).resolve().parents[2] / "docs" / "results.md").read_text(
    encoding="utf-8"
)
SECTION = RESULTS_MD.split("### The event stream")[1].split("### Per-sample files")[0]
TIMING_KEYS = set(TimingFields.__annotations__) | set(CapFields.__annotations__)


def _row_names(first_cell: str) -> set[str]:
    """The field names (backticked identifiers) in the table row that starts with `first_cell`."""
    (row,) = [line for line in SECTION.splitlines() if line.startswith(f"| `{first_cell}` |")]
    fields = row.split("|")[2]
    parenthetical = re.compile(r"\([^)]*\)")

    return set(re.findall(r"`([a-z_]+)`", parenthetical.sub("", fields)))


def test_envelope_table_lists_the_envelope_fields() -> None:
    """Envelope table lists the envelope fields."""
    documented = {name for name in Envelope.__annotations__ if f"| `{name}` |" in SECTION}

    assert documented == set(Envelope.__annotations__)


@pytest.mark.parametrize("event_type", sorted(EVENT_TYPES))
def test_each_event_row_lists_its_fields(event_type: str) -> None:
    """Each event row lists its fields."""
    shape = EVENT_TYPES[event_type]
    own = (shape.__required_keys__ | shape.__optional_keys__) - set(Envelope.__annotations__)

    assert _row_names(event_type) == own - TIMING_KEYS - set(StoppedField.__annotations__) | (
        {"stopped"} & shape.__required_keys__
    )


def test_nested_shapes_phases_and_timing_fields_are_documented() -> None:
    """Nested shapes phases and timing fields are documented."""
    (arms,) = re.findall(r"`arms` \(each ([^)]*)\)", SECTION)
    (cells,) = re.findall(r"`--count`: (.*?), one\n", SECTION, flags=re.DOTALL)
    timing_bullet = RESULTS_MD.split("- `timing.json`:")[1].split("- `transcript.json`")[0]

    assert set(re.findall(r"`([a-z_]+)`", arms)) == set(ArmInfo.__annotations__)
    assert set(re.findall(r"`([a-z_]+)`", cells)) == set(PlannedCell.__annotations__)
    assert all(f"`{phase}`" in _phase_row() for phase in PHASES)
    assert all(f"`{key}`" in timing_bullet or f'"{key}"' in timing_bullet for key in TIMING_KEYS)


def _phase_row() -> str:
    """The `cell.phase` table row."""
    (row,) = [line for line in SECTION.splitlines() if line.startswith("| `cell.phase` |")]

    return row
