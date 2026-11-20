"""Serve the watch page and a run's live view on the loopback interface only."""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from benchspec.exit_codes import ExitCode
from benchspec.orchestration import workspace
from benchspec.watch.tail import (
    POLL_INTERVAL_S,
    EventTail,
    UnsupportedFormatError,
    check_format,
    event_files,
)
from benchspec.watch.view import RunView, assertions_view, read_gradings

LOOPBACK = "127.0.0.1"
RETRY_MS = 5000
DRAIN_TIMEOUT_S = 2

_PAGE = resources.files("benchspec.watch") / "page"
_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/bracket.svg": ("bracket.svg", "image/svg+xml"),
    **{
        f"/fonts/{name}": (f"fonts/{name}", "font/woff2")
        for name in (
            "jetbrains-mono-400.woff2",
            "jetbrains-mono-500.woff2",
            "jetbrains-mono-600.woff2",
            "jetbrains-mono-700.woff2",
            "dm-sans-opsz.woff2",
        )
    },
}
_HEADERS = {"Content-Security-Policy": "default-src 'self'", "Cache-Control": "no-store"}
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_ITERATION = re.compile(r"iteration_(\d+)")


class WatchError(Exception):
    """There is no readable event stream to watch."""


def find_iteration(repo_root: Path, name: str | None) -> Path:
    """Resolve the iteration to watch: the named one, else the newest with an event stream.

    Raises:
        WatchError: no iteration has an event stream, or the named one has none.
        UnsupportedFormatError: its events carry another `format_version`.
    """
    evals_root = workspace.evals_root(repo_root)
    if name is not None:
        iteration_dir = evals_root / name
        if not event_files(iteration_dir):
            raise WatchError(f"{iteration_dir} has no event stream")
    else:
        numbered = [
            (int(match.group(1)), path)
            for path in evals_root.glob("iteration_*")
            if (match := _ITERATION.fullmatch(path.name)) and event_files(path)
        ]
        if not numbered:
            raise WatchError(
                f"no iteration with an event stream under {evals_root}; "
                "start one with benchspec run"
            )

        iteration_dir = max(numbered)[1]

    check_format(iteration_dir)

    return iteration_dir


def watch(repo_root: Path, *, iteration: str | None, port: int) -> int:
    """Serve one iteration's page until Ctrl-C.

    Raises:
        WatchError: there is no event stream to watch, or the port cannot be bound.
        UnsupportedFormatError: the events carry another `format_version`.
    """
    iteration_dir = find_iteration(repo_root, iteration)
    try:
        server = WatchServer(iteration_dir, port=port)
    except OSError as error:
        raise WatchError(f"cannot serve on port {port}: {error.strerror}") from error

    print(f"Watching {server.location}: {server.url}   (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()

    return ExitCode.SUCCESS


class WatchServer:
    """One iteration's watch page, bound to 127.0.0.1."""

    def __init__(
        self,
        iteration_dir: Path,
        *,
        port: int = 0,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        """Bind the socket; the iteration directory may not exist yet.

        Args:
            iteration_dir: The `tmp/evals/iteration_NN` directory to watch.
            port: The port to bind; 0 lets the OS choose.
            now_ms: The wall clock in epoch milliseconds, injectable so tests never wait.
        """
        self.iteration_dir = iteration_dir
        self.now_ms = now_ms
        self.stopping = threading.Event()
        self.streams: set[threading.Thread] = set()
        self._httpd = ThreadingHTTPServer((LOOPBACK, port), _handler_for(self))
        self._httpd.daemon_threads = True

    @property
    def location(self) -> str:
        """The iteration directory as the page names it: `tmp/evals/iteration_07`."""
        return "/".join(self.iteration_dir.parts[-3:])

    @property
    def port(self) -> int:
        """The bound port."""
        return self._httpd.server_address[1]

    @property
    def url(self) -> str:
        """The page's address."""
        return f"http://{LOOPBACK}:{self.port}/"

    def start(self) -> None:
        """Serve from a daemon thread, which never keeps the process alive."""
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def serve_forever(self) -> None:
        """Serve until `stop`."""
        self._httpd.serve_forever(poll_interval=POLL_INTERVAL_S)

    def stop(self) -> None:
        """Let every open stream send its last view, then close the socket."""
        self.stopping.set()
        for stream in list(self.streams):
            stream.join(DRAIN_TIMEOUT_S)

        self._httpd.shutdown()
        self._httpd.server_close()


def _handler_for(server: WatchServer) -> type[BaseHTTPRequestHandler]:
    """Build the request handler bound to one server."""

    class Handler(BaseHTTPRequestHandler):
        """Routes GET requests; everything it sends is same-origin and uncached."""

        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            """Serve the page, its assets, the live view or one sample's grading."""
            if self.headers.get("Host") not in (
                f"{LOOPBACK}:{server.port}",
                f"localhost:{server.port}",
            ):
                self._send(HTTPStatus.FORBIDDEN)
                return

            url = urlsplit(self.path)
            if url.path in _STATIC:
                name, content_type = _STATIC[url.path]
                self._send(HTTPStatus.OK, _PAGE.joinpath(name).read_bytes(), content_type)
            elif url.path == "/events":
                self._stream()
            elif url.path == "/grading":
                self._grading(parse_qs(url.query).get("cell", [""])[0])
            else:
                self._send(HTTPStatus.NOT_FOUND)

        def _refuse(self) -> None:
            """Answer any other method."""
            self._send(HTTPStatus.METHOD_NOT_ALLOWED)

        do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _refuse

        def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
            """Keep served requests out of the terminal."""

        def _send(self, status: HTTPStatus, body: bytes = b"", content_type: str = "") -> None:
            """Send one complete response."""
            self.send_response(status)
            for header, value in _HEADERS.items():
                self.send_header(header, value)
            if content_type:
                self.send_header("Content-Type", content_type)

            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _grading(self, cell: str) -> None:
            """Send one sample's assertions from the only path a cell can name."""
            parts = cell.split("/")
            if (
                len(parts) != 4
                or not all(_NAME.fullmatch(part) for part in parts[:3])
                or not parts[3].isdecimal()
            ):
                self._send(HTTPStatus.NOT_FOUND)
                return

            skill, eval_id, arm, sample = parts
            path = (
                server.iteration_dir
                / "skills"
                / skill
                / f"eval-{eval_id}"
                / arm
                / f"sample-{int(sample)}"
                / "grading.json"
            )
            try:
                grading = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._send(HTTPStatus.NOT_FOUND)
                return

            body = json.dumps(assertions_view(grading)).encode()
            self._send(HTTPStatus.OK, body, "application/json")

        def _stream(self) -> None:
            """Stream the view as Server-Sent Events until `run.end` or a disconnect."""
            self.send_response(HTTPStatus.OK)
            for header, value in _HEADERS.items():
                self.send_header(header, value)

            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            server.streams.add(threading.current_thread())
            try:
                self.wfile.write(f"retry: {RETRY_MS}\n\n".encode())
                self._follow()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                server.streams.discard(threading.current_thread())

        def _follow(self) -> None:
            """Fold new events into a fresh view and send it whenever it changes."""
            tail = EventTail(server.iteration_dir)
            view = RunView(server.location)
            finished = 0
            sent = None
            while True:
                last = server.stopping.is_set()
                try:
                    events = tail.poll()
                except UnsupportedFormatError as error:
                    self._event("error", str(error))
                    return

                for event in events:
                    view.apply(event)

                if view.finished_count() != finished:
                    finished = view.finished_count()
                    try:
                        view.gradings = read_gradings(
                            server.iteration_dir, view.arms, view.baseline
                        )
                    except ValueError:
                        # A grading.json caught mid-write: retry on the next poll.
                        finished = -1

                if server.iteration_dir.is_dir():
                    payload = json.dumps(view.to_json(server.now_ms()))
                else:
                    payload = json.dumps({"state": "none"})

                if payload != sent:
                    self._event("view", payload)
                    sent = payload

                # An empty poll after `run.end` shows every worker's file is drained.
                if view.ended and (last or (finished >= 0 and not events)):
                    self._event("end", "{}")
                    return
                if last:
                    return

                server.stopping.wait(POLL_INTERVAL_S)

        def _event(self, name: str, data: str) -> None:
            """Write one event to the stream."""
            self.wfile.write(f"event: {name}\ndata: {data}\n\n".encode())
            self.wfile.flush()

    return Handler
