"""`benchspec run --watch` around a real pytest child."""

from __future__ import annotations

import argparse
import subprocess
import textwrap

import pytest

from benchspec.runners import run
from tests.runners.test_cell_progress import EVALS, PYPROJECT, STAND_IN_CONFTEST
from tests.support.watch import next_event, open_stream

# The stand-in cells finish at once; slow them so the page is seen live.
SLOW_CELLS = textwrap.dedent(
    """\
    import time

    instant_arm = cases.run_eval_arm


    def slow_arm(*args, **kwargs):
        time.sleep(0.4)

        return instant_arm(*args, **kwargs)


    cases.run_eval_arm = slow_arm
    """
)


def test_watch_follows_a_real_run_from_no_run_to_its_final_view(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify the page goes from no run to live, and gets the final view before the server stops."""
    (pytester.path / "pyproject.toml").write_text(PYPROJECT.format(extra=""), encoding="utf-8")
    evals = pytester.path / "skills" / "myskill" / "evals" / "myskill"
    evals.mkdir(parents=True)
    for eval_id, assertions in EVALS.items():
        checklist = "\n".join(f"- [ ] {assertion}" for assertion in assertions)
        (evals / f"{eval_id}.eval.md").write_text(
            f"---\n{{}}\n---\n\n## Prompt\n\nDo the task.\n\n## Assertions\n\n{checklist}\n",
            encoding="utf-8",
        )

    pytester.makeconftest(STAND_IN_CONFTEST + SLOW_CELLS)
    args = argparse.Namespace(
        root=pytester.path, eval_paths=None, workers=1, watch=True, passthrough=[], env=[]
    )
    streams = []

    def runner(argv: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        """Open the page's stream before the real pytest child starts."""
        streams.append(open_stream(capsys.readouterr().out.split("Watching: ")[1].strip()))

        return subprocess.run(argv, env=env, capture_output=True, check=False)

    exit_code = run.run(args, runner=runner, preflight=lambda args: None)
    events = [next_event(streams[0])]
    while events[-1][0] != "end":
        events.append(next_event(streams[0]))

    states = [view["state"] for name, view in events if name == "view"]
    assert exit_code == 1
    assert states[0] == "none"
    assert "live" in states
    assert states[-1] == "stopped"
    assert events[-2][1]["top"]["status"] == "ended · exit 1"
