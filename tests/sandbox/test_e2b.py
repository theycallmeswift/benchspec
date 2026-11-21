"""Tests for the E2B backend against a recording fake of the SDK; no E2B credentials."""

from __future__ import annotations

import asyncio
import io
import tarfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

import e2b
import pytest

from benchspec.agents import CodingAgent
from benchspec.agents.claude import ClaudeCodeAgent
from benchspec.agents.codex import CodexAgent
from benchspec.sandbox import e2b as e2b_mod
from benchspec.sandbox.errors import SandboxError
from benchspec.sandbox.project import stage_archive
from benchspec.sandbox.sandbox import _agent_extra_volumes
from benchspec.specs.discovery import EnvConfig
from benchspec.testing import FakeSandbox


def _exit(code: int, stdout: str = "", stderr: str = "") -> e2b.CommandResult:
    """A finished guest command, as the SDK reports it."""
    return e2b.CommandResult(stdout=stdout, stderr=stderr, exit_code=code, error=None)


@dataclass
class _FakeCommand:
    """A background command handle: resolves to `outcome`, raises it, or hangs on None."""

    outcome: e2b.CommandResult | Exception | None
    stdin: list[object] = field(default_factory=list)
    killed: bool = False

    async def wait(self) -> e2b.CommandResult:
        """Finish the way the SDK does: a nonzero exit is raised, not returned."""
        if self.outcome is None:
            await asyncio.Event().wait()

        if isinstance(self.outcome, Exception):
            raise self.outcome

        if self.outcome is None or self.outcome.exit_code != 0:
            raise e2b.CommandExitException(**vars(self.outcome))

        return self.outcome

    async def kill(self) -> bool:
        """Record the kill."""
        self.killed = True

        return True

    async def send_stdin(self, data: bytes) -> None:
        """Record stdin bytes."""
        self.stdin.append(data)

    async def close_stdin(self) -> None:
        """Record the EOF."""
        self.stdin.append("closed")


@dataclass
class _FakeNative:
    """A live SDK sandbox recording every call; it is its own `commands` and `files`."""

    outcomes: list[e2b.CommandResult | Exception | None] = field(default_factory=list)
    running: bool = True
    runs: list[dict[str, object]] = field(default_factory=list)
    started: list[_FakeCommand] = field(default_factory=list)
    written: dict[str, bytes] = field(default_factory=dict)
    readable: dict[str, bytes] = field(default_factory=dict)
    write_error: Exception | None = None
    snapshots: list[tuple[str, str]] = field(default_factory=list)
    killed: bool = False

    @property
    def commands(self) -> _FakeNative:
        """The command API."""
        return self

    @property
    def files(self) -> _FakeNative:
        """The file API."""
        return self

    async def run(self, cmd: str, **options: object) -> _FakeCommand:
        """Start a background command that ends with the next queued outcome."""
        self.runs.append({"cmd": cmd, **options})
        on_stdout = options.get("on_stdout")
        if callable(on_stdout):
            on_stdout("out")

        command = _FakeCommand(self.outcomes.pop(0) if self.outcomes else _exit(0))
        self.started.append(command)

        return command

    async def write(self, path: str, data: bytes | IO[bytes], *, user: str) -> None:
        """Record an upload."""
        if self.write_error is not None:
            raise self.write_error

        self.written[path] = data if isinstance(data, bytes) else data.read()

    async def read(self, path: str, **options: object) -> bytearray:
        """Serve a download."""
        return bytearray(self.readable[path])

    async def is_running(self) -> bool:
        """Report whether the sandbox is alive."""
        return self.running

    async def kill(self) -> bool:
        """Record the kill."""
        self.killed = True

        return True

    async def create_snapshot(self, name: str, *, mode: str) -> None:
        """Record a snapshot."""
        self.snapshots.append((name, mode))


@dataclass
class _Pages:
    """A one-page SDK paginator."""

    items: list[object]
    has_next: bool = True

    async def next_items(self) -> list[object]:
        """Return the only page."""
        self.has_next = False

        return self.items


@dataclass
class _Named:
    """A listed sandbox, snapshot or template."""

    names: list[str] = field(default_factory=list)
    sandbox_id: str = ""
    snapshot_id: str = ""
    template_id: str = ""


@dataclass
class _FakeProject:
    """The E2B project: what exists in it and every lifecycle call made against it."""

    native: _FakeNative = field(default_factory=_FakeNative)
    sandboxes: list[_Named] = field(default_factory=list)
    snapshots: list[_Named] = field(default_factory=list)
    templates: list[_Named] = field(default_factory=list)
    calls: list[tuple[object, ...]] = field(default_factory=list)


def _install(monkeypatch: pytest.MonkeyPatch, project: _FakeProject) -> None:
    """Point the backend at `project`, with a key set and DNS answered locally."""

    class _Sandbox:
        """The `AsyncSandbox` statics the backend drives."""

        @staticmethod
        async def create(**options: object) -> _FakeNative:
            """Record or answer the SDK's `create`."""
            project.calls.append(("create", options))

            return project.native

        @staticmethod
        def list(*, query: e2b.SandboxQuery) -> _Pages:
            """Record or answer the SDK's `list`."""
            project.calls.append(("list", query.metadata))

            return _Pages(list(project.sandboxes))

        @staticmethod
        async def kill(sandbox_id: str) -> bool:
            """Record or answer the SDK's `kill`."""
            project.calls.append(("kill", sandbox_id))

            return True

        @staticmethod
        def list_snapshots(*, name: str | None) -> _Pages:
            """Record or answer the SDK's `list_snapshots`."""
            wanted = [
                snapshot
                for snapshot in project.snapshots
                if name is None or any(name in listed for listed in snapshot.names)
            ]

            return _Pages(list(wanted))

        @staticmethod
        async def delete_snapshot(snapshot_id: str) -> bool:
            """Record or answer the SDK's `delete_snapshot`."""
            project.calls.append(("delete_snapshot", snapshot_id))

            return True

    class _Template:
        """The `AsyncTemplate` statics the backend drives."""

        @staticmethod
        async def build(template: object, name: str, **options: object) -> None:
            """Record or answer the SDK's `build`."""
            project.calls.append(("build", name, options))

        @staticmethod
        def list() -> _Pages:
            """Record or answer the SDK's `list`."""
            return _Pages(list(project.templates))

        @staticmethod
        async def delete(name_or_id: str) -> bool:
            """Record or answer the SDK's `delete`."""
            project.calls.append(("delete_template", name_or_id))

            return True

    monkeypatch.setattr(e2b, "AsyncSandbox", _Sandbox)
    monkeypatch.setattr(e2b, "AsyncTemplate", _Template)
    monkeypatch.setattr(e2b_mod.socket, "gethostbyname", lambda host: "203.0.113.7")
    monkeypatch.setenv(e2b_mod.API_KEY_ENV, "e2b_test")


def _claude() -> ClaudeCodeAgent:
    """A Claude Code agent whose credential may reach `api.anthropic.com`."""
    return ClaudeCodeAgent(auth_value="test-token", version="1.2.3")


def _boot(
    room: Path, stage: Path | None = None, agent: CodingAgent | None = None
) -> e2b_mod.E2BSandbox:
    """Boot a cell through the real backend."""
    return asyncio.run(
        e2b_mod.E2BBackend().create_sandbox(
            agent=agent or _claude(),
            snapshot="benchspec-e2b-snap",
            name="benchspec-eval-hello-trial-main",
            host_workdir=room,
            staged_project=stage,
            extra_volumes=_agent_extra_volumes,
            cap_seconds=900,
        )
    )


def _guest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    native: _FakeNative,
    outcomes: list[e2b.CommandResult | Exception | None] | None = None,
) -> e2b_mod.E2BSandbox:
    """A booted cell over `native`, its boot calls forgotten and `outcomes` queued."""
    _install(monkeypatch, _FakeProject(native=native))
    boot_room = tmp_path / "boot-room"
    boot_room.mkdir()
    guest = _boot(boot_room)
    native.runs.clear()
    native.started.clear()
    native.outcomes = list(outcomes or [])

    return guest


def _tar_gz(build: Callable[[tarfile.TarFile], None]) -> bytes:
    """A gzipped tar assembled by `build`."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        build(tar)

    return buffer.getvalue()


def _add_file(tar: tarfile.TarFile, name: str, content: bytes, mode: int = 0o644) -> None:
    """Add a regular file member."""
    member = tarfile.TarInfo(name)
    member.size = len(content)
    member.mode = mode
    tar.addfile(member, io.BytesIO(content))


def _add_link(tar: tarfile.TarFile, name: str, target: str) -> None:
    """Add a symlink member."""
    member = tarfile.TarInfo(name)
    member.type = tarfile.SYMTYPE
    member.linkname = target
    tar.addfile(member)


def test_preflight_names_a_missing_sdk_then_a_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preflight names a missing sdk then a missing key."""
    backend = e2b_mod.E2BBackend()
    monkeypatch.delenv(e2b_mod.API_KEY_ENV, raising=False)

    without_key = backend.preflight()
    monkeypatch.setenv(e2b_mod.API_KEY_ENV, "e2b_test")
    ready = backend.preflight()
    monkeypatch.setattr(e2b_mod, "e2b", None)
    without_sdk = backend.preflight()

    assert without_key == [e2b_mod.API_KEY_MISSING]
    assert ready == []
    assert without_sdk == [e2b_mod.SDK_MISSING]


def test_create_sandbox_limits_egress_to_the_provider_host_and_bounds_the_lifetime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Create sandbox limits egress to the provider host and bounds the lifetime."""
    project = _FakeProject()
    _install(monkeypatch, project)

    _boot(tmp_path)

    assert project.calls == [
        (
            "create",
            {
                "template": "benchspec-e2b-snap",
                "timeout": 900 + e2b_mod.LIFETIME_MARGIN_SECONDS,
                "metadata": {
                    "created-by": "benchspec",
                    "benchspec-name": "benchspec-eval-hello-trial-main",
                },
                "envs": {"ANTHROPIC_API_KEY": "test-token"},
                "network": {
                    "allow_out": ["api.anthropic.com"],
                    "deny_out": ["0.0.0.0/0"],
                    "dns_servers": [],
                },
            },
        )
    ]


def test_create_sandbox_uploads_both_trees_and_pins_the_provider_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Create sandbox uploads both trees and pins the provider host."""
    project = _FakeProject()
    _install(monkeypatch, project)
    room = tmp_path / "room"
    (room / "sub").mkdir(parents=True)
    (room / "sub" / "seed.txt").write_text("seed", encoding="utf-8")
    stage = tmp_path / "run" / "project"
    stage.mkdir(parents=True)
    (stage / "README.md").write_text("hi", encoding="utf-8")

    _boot(room, stage)

    written = project.native.written
    with tarfile.open(fileobj=io.BytesIO(written["/tmp/benchspec-workspace.tar.gz"])) as tar:
        assert "sub/seed.txt" in tar.getnames()
    assert written["/tmp/benchspec-project.tar.gz"] == stage_archive(stage).read_bytes()
    unpack = str(project.native.runs[-1]["cmd"])
    assert "tar -xzf /tmp/benchspec-workspace.tar.gz --no-same-owner -C /workspace" in unpack
    assert "tar -xzf /tmp/benchspec-project.tar.gz --no-same-owner -C /project" in unpack
    assert "203.0.113.7 api.anthropic.com" in unpack


def test_create_sandbox_refuses_a_credential_with_no_provider_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Create sandbox refuses a credential with no provider host."""
    project = _FakeProject()
    _install(monkeypatch, project)
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")

    with pytest.raises(SandboxError, match="declares no provider host"):
        _boot(tmp_path, agent=CodexAgent(auth_json_path=str(auth)))

    assert project.calls == []


def test_create_sandbox_kills_the_sandbox_when_the_upload_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Create sandbox kills the sandbox when the upload fails."""
    project = _FakeProject(native=_FakeNative(write_error=e2b.SandboxException("disk full")))
    _install(monkeypatch, project)

    with pytest.raises(SandboxError, match="disk full"):
        _boot(tmp_path)

    assert project.native.killed is True


def test_sync_out_replaces_the_host_workdir_and_drops_unsafe_members(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sync out replaces the host workdir and drops unsafe members."""
    room = tmp_path / "room"
    room.mkdir()
    (room / "deleted-by-agent.txt").write_text("stale", encoding="utf-8")

    def workspace(tar: tarfile.TarFile) -> None:
        _add_file(tar, "./out.txt", b"agent wrote", mode=0o4755)
        _add_link(tar, "./relative", "out.txt")
        _add_link(tar, "./absolute", "/etc/passwd")
        _add_link(tar, "./escaping", "../../outside")

    native = _FakeNative(readable={"/tmp/benchspec-workspace.tar.gz": _tar_gz(workspace)})

    guest = _guest(monkeypatch, tmp_path, native)

    asyncio.run(e2b_mod.E2BBackend().sync_out(guest, room))

    assert sorted(path.name for path in room.iterdir()) == ["out.txt", "relative"]
    assert (room / "relative").read_text(encoding="utf-8") == "agent wrote"
    assert (room / "out.txt").stat().st_mode & 0o7000 == 0
    assert native.runs[0]["cmd"] == "tar -czf /tmp/benchspec-workspace.tar.gz -C /workspace ."


@pytest.mark.parametrize(("tar_exit", "synced"), [(0, True), (1, True), (2, False)])
def test_sync_out_accepts_a_tar_that_saw_files_change_but_not_a_failed_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tar_exit: int, synced: bool
) -> None:
    """Sync out accepts a tar that saw files change but not a failed one."""
    room = tmp_path / "room"
    room.mkdir()
    archive = _tar_gz(lambda tar: _add_file(tar, "out.txt", b"x"))
    native = _FakeNative(readable={"/tmp/benchspec-workspace.tar.gz": archive})
    guest = _guest(monkeypatch, tmp_path, native, [_exit(tar_exit, stderr="tar: boom")])
    sync = e2b_mod.E2BBackend().sync_out(guest, room)

    if synced:
        asyncio.run(sync)
    else:
        with pytest.raises(SandboxError, match="tar: boom"):
            asyncio.run(sync)

    assert (room / "out.txt").exists() is synced


def test_sync_out_refuses_another_backends_guest(tmp_path: Path) -> None:
    """Sync out refuses another backends guest."""
    with pytest.raises(SandboxError, match="E2B sandbox"):
        asyncio.run(e2b_mod.E2BBackend().sync_out(FakeSandbox(), tmp_path))


def test_exec_lifts_the_sdk_timeout_runs_as_root_and_returns_a_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exec lifts the sdk timeout runs as root and returns a nonzero exit."""
    native = _FakeNative()
    guest = _guest(monkeypatch, tmp_path, native, [_exit(7, stdout="out", stderr="err")])

    result = asyncio.run(
        guest.exec("echo", ["a b"], cwd="/workspace", env={"KEY": "value"}, stdin=b"input")
    )

    assert (result.exit_code, result.stdout_text, result.stderr_text) == (7, "out", "err")
    assert native.runs == [
        {
            "cmd": "echo 'a b'",
            "background": True,
            "envs": {"KEY": "value"},
            "user": "root",
            "cwd": "/workspace",
            "on_stdout": None,
            "on_stderr": None,
            "stdin": True,
            "timeout": 0,
        }
    ]
    assert native.started[0].stdin == [b"input", "closed"]


def test_exec_kills_a_command_that_outlives_its_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exec kills a command that outlives its timeout."""
    native = _FakeNative()
    guest = _guest(monkeypatch, tmp_path, native, [None])

    with pytest.raises(TimeoutError, match="sleep timed out after 0.01s"):
        asyncio.run(guest.exec("sleep", ["60"], timeout=0.01))

    assert native.started[0].killed is True


@pytest.mark.parametrize(
    ("running", "raised"),
    [(True, TimeoutError), (False, SandboxError)],
)
def test_an_sdk_timeout_is_a_slow_command_only_while_the_sandbox_is_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, running: bool, raised: type[Exception]
) -> None:
    """An sdk timeout is a slow command only while the sandbox is running."""
    native = _FakeNative()
    guest = _guest(monkeypatch, tmp_path, native, [e2b.SandboxNotRunningException("gone")])
    native.running = running

    with pytest.raises(raised, match="gone"):
        asyncio.run(guest.shell("true"))


@pytest.mark.parametrize(
    ("outcome", "kill", "terminal"),
    [
        (_exit(3), False, ("exited", 3)),
        (_exit(-1), True, ("failed", -1)),
        (e2b.SandboxException("sandbox killed"), False, ("failed", None)),
    ],
)
def test_exec_stream_ends_exited_or_failed_when_killed_or_lost(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    outcome: e2b.CommandResult | Exception,
    kill: bool,
    terminal: tuple[str, int | None],
) -> None:
    """Exec stream ends exited or failed when killed or lost."""
    native = _FakeNative()
    guest = _guest(monkeypatch, tmp_path, native, [outcome])

    async def drain() -> list[tuple[str, bytes | None, int | None]]:
        """Collect every event of one streamed command."""
        stream = await guest.exec_stream("agent", stdin=b"")
        if kill:
            await stream.kill()

        return [(event.event_type, event.data, event.code) async for event in stream]

    events = asyncio.run(drain())

    assert events == [("stdout", b"out", None), (terminal[0], None, terminal[1])]
    assert native.started[0].killed is kill
    assert native.started[0].stdin == ["closed"]


def test_build_snapshot_sizes_a_base_template_provisions_and_seals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build snapshot sizes a base template provisions and seals."""
    project = _FakeProject()
    _install(monkeypatch, project)
    name = "benchspec-e2b-claude-code-1.2.3-abcd1234"

    e2b_mod.E2BBackend().build_snapshot(_claude(), name, EnvConfig(script=b"apt-get install jq"))

    assert project.calls[0] == ("build", f"{name}-base", {"cpu_count": 2, "memory_mb": 2048})
    assert project.calls[1] == (
        "create",
        {
            "template": f"{name}-base",
            "timeout": e2b_mod.BUILD_LIFETIME_SECONDS,
            "metadata": {
                "created-by": "benchspec",
                "benchspec-name": "benchspec-build-claude-code",
            },
        },
    )
    assert "apt-get install jq" in str(project.native.runs[-1]["cmd"])
    assert project.native.snapshots == [(name, "filesystem")]
    assert project.native.killed is True
    assert project.calls[-1] == ("delete_template", f"{name}-base")


def test_snapshot_exists_only_for_a_listed_benchspec_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Snapshot exists only for a listed benchspec snapshot."""
    project = _FakeProject(snapshots=[_Named(names=["team/benchspec-e2b-snap"], snapshot_id="s1")])
    _install(monkeypatch, project)
    backend = e2b_mod.E2BBackend()

    assert backend.snapshot_exists("benchspec-e2b-snap") is True
    assert backend.snapshot_exists("benchspec-e2b-other") is False


def test_prune_removes_only_what_benchspec_tagged_or_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prune removes only what benchspec tagged or named."""
    project = _FakeProject(
        sandboxes=[_Named(sandbox_id="tagged")],
        snapshots=[
            _Named(names=["team/benchspec-e2b-snap"], snapshot_id="ours"),
            _Named(names=["team/someone-elses"], snapshot_id="theirs"),
        ],
        templates=[
            _Named(names=["team/benchspec-e2b-snap-base"], template_id="our-base"),
            _Named(names=["team/benchspec"], template_id="their-lookalike"),
            _Named(names=["team/grading"], template_id="their-template"),
        ],
    )
    _install(monkeypatch, project)

    e2b_mod.E2BBackend().prune()

    assert project.calls == [
        ("list", {"created-by": "benchspec"}),
        ("kill", "tagged"),
        ("delete_snapshot", "ours"),
        ("delete_template", "our-base"),
    ]


def test_prune_without_a_key_touches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prune without a key touches nothing."""
    project = _FakeProject(sandboxes=[_Named(sandbox_id="tagged")])
    _install(monkeypatch, project)
    monkeypatch.delenv(e2b_mod.API_KEY_ENV)

    e2b_mod.E2BBackend().prune()

    assert project.calls == []


def test_discard_quietly_kills_only_the_sandbox_tagged_with_that_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discard quietly kills only the sandbox tagged with that name."""
    project = _FakeProject(sandboxes=[_Named(sandbox_id="half-booted")])
    _install(monkeypatch, project)

    asyncio.run(e2b_mod.E2BBackend().discard_quietly("benchspec-eval-hello-trial-main"))

    assert project.calls == [
        ("list", {"created-by": "benchspec", "benchspec-name": "benchspec-eval-hello-trial-main"}),
        ("kill", "half-booted"),
    ]
