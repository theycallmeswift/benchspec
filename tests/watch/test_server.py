"""The watch server against a recorded iteration, on an OS-chosen loopback port."""

from __future__ import annotations

import http.client
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from benchspec.watch.server import WatchError, WatchServer, find_iteration
from benchspec.watch.tail import UnsupportedFormatError
from tests.support.watch import Recording, finished_run, iteration, next_event

CELL = "hello/greets/trial/0"


class _Clock:
    """A wall clock the test moves by hand."""

    def __init__(self, now_ms: int) -> None:
        """Start at `now_ms`."""
        self.now_ms = now_ms

    def __call__(self) -> int:
        """The current time."""
        return self.now_ms


@pytest.fixture
def run(tmp_path: Path) -> Recording:
    """A live one-eval run whose `trial` sample is in its agent phase."""
    recording = Recording(iteration(tmp_path))
    recording.plan("hello", "greets")
    recording.begin()
    recording.play(CELL, 0)

    return recording


@pytest.fixture
def clock(run: Recording) -> _Clock:
    """A clock ten seconds into the run."""
    return _Clock(run.start_ms + 10_000)


@pytest.fixture
def server(run: Recording, clock: _Clock) -> Iterator[WatchServer]:
    """A started server on the run's iteration."""
    watch_server = WatchServer(run.iteration_dir, now_ms=clock)
    watch_server.start()

    yield watch_server

    watch_server.stop()


def _get(
    server: WatchServer, path: str, *, host: str | None = None, method: str = "GET"
) -> http.client.HTTPResponse:
    """One request to the server, with an overridable `Host` header."""
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    connection.putrequest(method, path, skip_host=True)
    connection.putheader("Host", host or f"127.0.0.1:{server.port}")
    connection.endheaders()

    return connection.getresponse()


def _view_with(stream: http.client.HTTPResponse, *, done: int) -> dict:
    """Read views until one counts `done` finished samples."""
    while True:
        name, view = next_event(stream)
        if name == "view" and view["counter"]["done"] == done:
            return view


def _box(view: dict) -> str:
    """The `trial` sample's box in a streamed view."""
    return view["matrix"]["groups"][0]["rows"][0]["cells"][1]["boxes"][0]


def test_binds_the_loopback_interface_only(server: WatchServer) -> None:
    """Binds the loopback interface only."""
    assert server.url == f"http://127.0.0.1:{server.port}/"
    assert server._httpd.server_address[0] == "127.0.0.1"


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/app.js",
        "/app.css",
        "/bracket.svg",
        "/fonts/jetbrains-mono-400.woff2",
        "/fonts/jetbrains-mono-500.woff2",
        "/fonts/dm-sans-opsz.woff2",
    ],
)
def test_serves_each_packaged_file_same_origin_and_uncached(server: WatchServer, path: str) -> None:
    """Serves each packaged file same origin and uncached."""
    response = _get(server, path)

    assert response.status == 200
    assert response.getheader("Content-Security-Policy") == "default-src 'self'"
    assert response.getheader("Cache-Control") == "no-store"
    assert response.read()


@pytest.mark.parametrize(
    "path",
    [
        "/fonts/jetbrains-mono-700.woff2",
        "/fonts/LICENSE-dm-sans.txt",
        "/fonts/../app.js",
        "/index.html",
        "/events-main.jsonl",
    ],
)
def test_anything_else_is_not_found(server: WatchServer, path: str) -> None:
    """Anything else is not found."""
    assert _get(server, path).status == 404


def test_only_get_is_allowed(server: WatchServer) -> None:
    """Only get is allowed."""
    assert _get(server, "/", method="POST").status == 405


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/events",
        "/grading?cell=hello/greets/trial/0",
        "/plan?cell=hello/greets/trial/0",
        "/nope",
    ],
)
def test_another_host_header_is_forbidden_on_every_route(server: WatchServer, path: str) -> None:
    """Another host header is forbidden on every route."""
    assert _get(server, path, host="evil.example").status == 403
    assert _get(server, path, host=f"localhost:{server.port}").status != 403


def test_streams_a_view_per_change_then_end_once_the_run_ends(
    server: WatchServer, run: Recording
) -> None:
    """Streams a view per change then end once the run ends."""
    stream = _get(server, "/events")

    first = next_event(stream)
    run.done("hello/greets/baseline/0", 70)
    second = _view_with(stream, done=1)
    run.end(80)
    third = next_event(stream)
    last = next_event(stream)

    assert stream.getheader("Content-Type") == "text/event-stream"
    assert (first[0], first[1]["counter"]["done"]) == ("view", 0)
    assert second["leaderboard"]["rows"][0]["cells"][0]["value"] == "100%"
    assert (third[0], third[1]["state"]) == ("view", "stopped")
    assert last == ("end", {})
    assert stream.read().strip() == b""


def test_stopping_sends_each_open_stream_its_last_view(server: WatchServer, run: Recording) -> None:
    """Stopping sends each open stream its last view."""
    stream = _get(server, "/events")
    next_event(stream)

    run.end(80)
    server.stop()
    events = [next_event(stream), next_event(stream)]

    assert events == [("view", events[0][1]), ("end", {})]
    assert events[0][1]["state"] == "stopped"


def test_a_sample_going_quiet_sends_a_view_with_no_new_event(
    server: WatchServer, clock: _Clock
) -> None:
    """A sample going quiet sends a view with no new event."""
    stream = _get(server, "/events")
    before = next_event(stream)[1]

    clock.now_ms += 30_000
    after = next_event(stream)[1]

    assert _box(before) == "bs-b-running"
    assert _box(after) == "bs-b-quiet"


def test_reports_no_run_until_the_iteration_directory_exists(tmp_path: Path) -> None:
    """Reports no run until the iteration directory exists."""
    server = WatchServer(iteration(tmp_path))
    server.start()

    stream = _get(server, "/events")
    waiting = next_event(stream)
    Recording(iteration(tmp_path)).iteration_dir.mkdir(parents=True)
    loading = next_event(stream)
    server.stop()

    assert waiting == ("view", {"state": "none"})
    assert loading == ("view", {"state": "loading"})


def test_a_live_tail_reports_an_unsupported_format_and_closes(
    server: WatchServer, run: Recording
) -> None:
    """A live tail reports an unsupported format and closes."""
    stream = _get(server, "/events")
    next_event(stream)

    with (run.iteration_dir / "events-gw9.jsonl").open("w", encoding="utf-8") as file:
        file.write('{"format_version": 3, "type": "cell.start", "ts_ms": 1, "worker": "gw9"}\n')

    lines = stream.read().decode().strip().splitlines()

    assert lines[:2] == [
        "event: error",
        "data: events format_version 3 is not supported by this benchspec",
    ]


def test_grading_returns_one_samples_verdicts(server: WatchServer, run: Recording) -> None:
    """Grading returns one samples verdicts."""
    run.done(CELL, 70, passed=2)

    response = _get(server, f"/grading?cell={CELL}")
    body = json.loads(response.read())

    assert response.status == 200
    assert body["caption"] == "[ assertions · 1 failed · 2 passed ]"
    assert [entry["state"] for entry in body["assertions"]] == ["fail", "pass", "pass"]


@pytest.mark.parametrize(
    "cell",
    [
        "hello/greets/baseline/0",
        "hello/missing/trial/0",
        "hello/greets/trial",
        "hello/greets/trial/0/extra",
        "hello/greets/trial/zero",
        "../greets/trial/0",
        "hello/../trial/0",
        "hello/greets/../0",
        "",
    ],
)
def test_grading_reads_nothing_but_a_finished_samples_file(
    server: WatchServer, run: Recording, cell: str
) -> None:
    """Grading reads nothing but a finished samples file."""
    run.done(CELL, 70)
    (run.iteration_dir / "skills" / "grading.json").write_text("{}", encoding="utf-8")

    assert _get(server, f"/grading?cell={cell}").status == 404


def test_plan_returns_a_samples_lines_before_it_is_graded(server: WatchServer) -> None:
    """Plan returns a sample's lines, as run.start lists them, before it is graded."""
    response = _get(server, f"/plan?cell={CELL}")
    body = json.loads(response.read())

    assert response.status == 200
    assert body == {
        "caption": "[ assertions · 3 planned ]",
        "assertions": [
            {"index": index, "state": "planned", "text": f"line {index + 1} holds"}
            for index in range(3)
        ],
    }


def test_plan_lists_a_line_scoped_off_the_arm_last_as_na(tmp_path: Path) -> None:
    """Plan lists a line scoped off the arm last, as n/a."""
    run = Recording(iteration(tmp_path))
    run.plan("hello", "greets", checks=2, only={0: ["trial"]})
    run.begin()
    watch_server = WatchServer(run.iteration_dir)
    watch_server.start()

    try:
        body = json.loads(_get(watch_server, "/plan?cell=hello/greets/baseline/0").read())
    finally:
        watch_server.stop()

    assert body["caption"] == "[ assertions · 1 planned · 1 n/a ]"
    assert [(entry["index"], entry["state"]) for entry in body["assertions"]] == [
        (1, "planned"),
        (0, "n/a"),
    ]


@pytest.mark.parametrize("cell", ["hello/missing/trial/0", "../greets/trial/0", ""])
def test_plan_finds_nothing_for_a_sample_the_run_did_not_plan(
    server: WatchServer, cell: str
) -> None:
    """Plan finds nothing for a sample the run did not plan."""
    assert _get(server, f"/plan?cell={cell}").status == 404


def test_finds_the_newest_iteration_with_an_event_stream(tmp_path: Path) -> None:
    """Finds the newest iteration with an event stream."""
    finished_run(tmp_path)
    older = Recording(iteration(tmp_path, "iteration_02"))
    older.plan("hello", "greets")
    older.begin()
    iteration(tmp_path, "iteration_13").mkdir()

    assert find_iteration(tmp_path, None).name == "iteration_12"
    assert find_iteration(tmp_path, "iteration_02").name == "iteration_02"


def test_refuses_a_root_or_an_iteration_without_an_event_stream(tmp_path: Path) -> None:
    """Refuses a root or an iteration without an event stream."""
    iteration(tmp_path, "iteration_01").mkdir(parents=True)

    with pytest.raises(WatchError, match="no iteration with an event stream"):
        find_iteration(tmp_path, None)

    with pytest.raises(WatchError, match="iteration_01 has no event stream"):
        find_iteration(tmp_path, "iteration_01")


def test_refuses_an_iteration_in_another_format(tmp_path: Path) -> None:
    """Refuses an iteration in another format."""
    events = iteration(tmp_path) / "events-main.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text('{"format_version": 3, "type": "run.start"}\n', encoding="utf-8")

    with pytest.raises(UnsupportedFormatError, match="format_version 3 is not supported"):
        find_iteration(tmp_path, None)
