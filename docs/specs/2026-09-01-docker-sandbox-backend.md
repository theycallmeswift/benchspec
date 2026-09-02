**TL;DR** — Implement `DockerBackend` as the second concrete `SandboxBackend`, turning `sandbox = "docker"` from a fail-fast stub into a working container runtime — plus the two seam repairs it forces (backend-neutral agent credentials, a backend-neutral infra error) — so evals run on hosts without Apple Silicon or KVM.

## Problem

- **Symptom:** `sandbox = "docker"` is recognized but unimplemented. `_NOT_IMPLEMENTED = {"docker": "Docker is not implemented"}` in `src/harnessbench/sandbox/backend.py` makes `resolve_sandbox("docker")` raise `SchemaError`; a Docker set exits `2` before any arm (`tests/config/test_arms.py:551`, `tests/test_main.py:174`).
- **Symptom:** microsandbox's host gate excludes common machines. `MicrosandboxBackend.preflight()` requires Apple Silicon or Linux with `/dev/kvm` — x86 macOS, Windows/WSL, and the many CI runners without nested virtualization cannot run evals at all. Docker is the runtime those hosts already have.
- **Symptom (seam leak):** agent adapters construct microsandbox `Secret.env(..., allow_hosts=[...])` objects inside `secrets()` (`src/harnessbench/agents/claude.py:167-177`), and `create_sandbox` passes them straight through. A second backend cannot consume them.
- **Symptom (seam leak):** agents catch `MicrosandboxError` at the invoke boundary to classify a sandbox failure as a recorded infra error (`src/harnessbench/agents/claude.py:302-330`). A Docker runtime failure would propagate as a crash instead of an excluded arm.
- **Constraint:** the lazy-import invariant holds for the new backend — importing `harnessbench.sandbox.*` must not import any Docker client package, so `lint`/`analyze` keep working on a host with nothing but Python (`backend.py` module docstring).
- **Constraint:** the guest-runtime contract is implicit, not typed. Whatever `create_sandbox` returns must provide `.shell(script, env=, cwd=)` → result with `exit_code`/`stdout_text`/`stderr_text`, `.exec_stream(cmd, args, cwd=, env=, stdin=)` → an async iterable of `stdout`/`exited`/`failed` events with `.kill()`, and `.stop()` (`sandbox/sandbox.py:252-330,385-470`), plus a `Volume`-like class with `.bind(path, readonly=)` handed to `_agent_extra_volumes` (`sandbox/sandbox.py:159-172`).
- **Constraint:** isolation semantics differ and must be stated, not hidden. A container shares the host kernel; the agent runs as root with `bypassPermissions` on the promise that the sandbox is the containment boundary (`agents/claude.py:159-165`). Selecting `sandbox = "docker"` is the user's explicit opt-in to the weaker boundary; the docs must say so plainly.
- **Scope:** one new backend behind the existing seam, plus the two seam repairs it forces. No change to microsandbox behavior, the cache-key semantics, or the eval/config surface beyond the newly-valid value.

## Solution

```toml
[tool.harnessbench.sets.ci]
sandbox = "docker"          # was fail-fast; now selects DockerBackend
arms    = [ "claude-code:claude-sonnet-5" ]
```

```text
$ harnessbench sandbox:build --set ci    # docker-commits image harnessbench-docker-claude-code-<v>-<fp>
$ harnessbench run --set ci              # each cell runs as a container from that image
```

The same set config, snapshot cache model, and exit-code contract — with cells in Docker containers instead of microVMs.

## User Stories

1. As an eval author on **an x86 CI runner or x86 Mac**, I want my suite to run with the Docker I already have, so the benchmark isn't gated on Apple Silicon or KVM.
2. As a benchmark maintainer, I want **backend choice to be one set-level key**, so the same evals and arms run under either backend with no other config change.
3. As a results consumer, I want **the backend id and image digest in the run artifacts**, so a matrix produced under container isolation is distinguishable from one produced under microVMs.

## Implementation Decisions

```text
sandbox = "docker" ──► resolve_sandbox() ──► DockerBackend        (new: sandbox/docker.py)
        │                                        │ registered in backend._REGISTRY;
        │                                        │ "docker" leaves backend._NOT_IMPLEMENTED
        ▼                                        ▼
  preflight()          docker CLI on PATH + `docker info` reachable
  build_snapshot()     docker run <base> → agent.provision → bridge_skills_home
        │              → environment_script → docker commit <snapshot_name>
  snapshot_exists()    docker image inspect <snapshot_name>
  image_identity()     docker image inspect → Id/RepoDigest → ImageIdentity
  create_sandbox()     docker run -v workspace:/workspace -v stage:/project:ro
        │              --cpus VM_CPUS --memory VM_MEMORY_MIB
        ▼
  DockerSandbox        .shell / .exec_stream / .stop over `docker exec` subprocesses
```

- **Drive Docker through the CLI, not a client library.** `asyncio.create_subprocess_exec` around `docker` subcommands: zero new dependencies, the lazy-import invariant is satisfied trivially, and the CLI is the one interface every Docker-equipped host has. Revisit only if streaming exec through the CLI proves unreliable.
- **A snapshot is a committed local image tagged with `snapshot_name()`.** The build mirrors `MicrosandboxBackend._build_snapshot_async`: run the base image, `agent.provision()`, skills-home bridge, environment script under `set -e`, then `docker commit`. `fingerprint_inputs()` reuses the same four ingredients with `backend_id="docker"`, so a Docker and a microsandbox snapshot of one agent+env never collide (`sandbox/sandbox.py:41-51`).
- **Generalize `agent.secrets()` to a backend-neutral declaration.** Agents return plain credential specs (env name, value, allowed hosts); `MicrosandboxBackend` maps them to `Secret.env`, `DockerBackend` maps them to container env vars. Docker cannot enforce `allow_hosts` scoping — the credential is guest-readable there, and the docs state that as part of the opt-in.
- **Introduce a backend-neutral infra error.** The backend wraps its runtime failures (daemon gone, container OOM-killed, exec torn down) in one exception type the agents catch alongside `TimeoutError`/`OSError`, replacing the direct `MicrosandboxError` imports in `agents/*.py` — so a Docker failure records an `is_error` arm instead of crashing the run.
- **`DockerSandbox` + `DockerVolume` satisfy the implicit guest contract.** `.shell` and `.exec_stream` (with `stdout`/`exited`/`failed` events and `.kill()`) run through `docker exec`; `.stop` is `docker rm -f`; `DockerVolume.bind(path, readonly=)` renders `-v` flags. `SandboxSession`, trigger routing, and the agents stay untouched above the seam.
- **Resource and lifecycle parity.** `--cpus`/`--memory` from the existing `VM_CPUS`/`VM_MEMORY_MIB`; `replace=True` semantics via remove-then-run; per-cell teardown removes the container.

## Testing Plan

### Logic
- **Registry and fail-fast** — `resolve_sandbox("docker")` returns a `DockerBackend`; unknown names still raise listing both supported values.
- **Cache identity is backend-distinct** — the Docker fingerprint folds `backend_id="docker"` with the same base-image/install/env ingredients; a Docker and microsandbox snapshot of one agent+env get different names, and each rebuilds on any ingredient change.
- **Preflight reports the Docker-shaped remedy** — missing CLI and unreachable daemon each produce a specific, actionable error string.
- **Credential mapping per backend** — one neutral secret spec renders as a scoped microsandbox `Secret` and as a Docker env var, byte-identical values.

### Behavior
- **A full cell runs end to end under Docker** — build (or reuse) the committed image, boot a container, run `setup.sh`, invoke the agent, gather facts, tear down; gated on a reachable daemon and skipped with an explicit reason otherwise.
- **A runtime failure records, not crashes** — a container killed mid-turn yields an `is_error` arm result excluded from grading, same as the microsandbox path.
- **The microsandbox path is unchanged** — an existing microsandbox set produces the same snapshot names and artifacts after the seam repairs.

### Interface
- **`sandbox = "docker"` is a valid config value** — parses in `[tool.harnessbench.sets.<name>]`, drives `sandbox:build --set` and `run --set`, and keeps the exit-code contract (`0` built/reused, `2` config/preflight, `1` build failure).
- **No Docker client package at import time** — importing `harnessbench.sandbox.docker` imports nothing beyond the stdlib; `lint`/`analyze` run on a Docker-less host.

## Open Questions

- **Rootless Docker and Podman:** does the exec/commit/mount surface used here work under rootless Docker or `podman` aliased as `docker`? Resolve by running the behavior suite on those targets; until then docs claim Docker Engine/Desktop only.
- **Credential posture for subscription tokens:** a guest-readable OAuth/subscription token is a bigger exposure than a scoped API key — should Docker runs warn (or refuse) when an arm's credential is subscription-shaped? Needs a product call; default proposal is a loud warning, not a refusal.
- **Windows hosts:** Docker Desktop on Windows would make harnessbench runnable there for the first time — supported target or explicitly untested? Resolve after the behavior suite exists.

## Documentation Plan

- **`docs/sandbox.md`**: add the Docker backend to the backend section — host requirements (daemon reachable, any platform), the shared-kernel isolation tradeoff and credential-visibility consequence, image cache location (`docker images` / how to clean).
- **`docs/configuration.md`**: update the `sandbox` row — two supported values, no more fail-fast note for `docker`.
- **`docs/harnesses.md`**: per-harness credential notes gain the Docker column: env-var injection, no host scoping.

## Out of Scope

- Replicating microsandbox's `allow_hosts` network-scoped secret injection in Docker (no equivalent primitive; documented tradeoff instead).
- Remote Docker hosts (`DOCKER_HOST`), Podman/rootless guarantees, and Windows support claims — candidates after the Open Questions resolve.
- Any change to microsandbox backend behavior, VM sizing, or the cache-key ingredient set.
- A third backend (exe.dev or otherwise) — this proves the seam holds; it adds exactly one.

## References

- `src/harnessbench/sandbox/backend.py` — the `SandboxBackend` Protocol, `MicrosandboxBackend` (the implementation to mirror), `_REGISTRY`, and the `_NOT_IMPLEMENTED` docker stub this spec deletes.
- `src/harnessbench/sandbox/sandbox.py:41-67,205-330,385-470` — `snapshot_name`, shared preflight, `SandboxSession`, and trigger routing: the backend-agnostic callers that must not change.
- `src/harnessbench/agents/claude.py:167-177,300-330` — the two seam leaks: microsandbox `Secret` construction and the `MicrosandboxError` catch.
- `docs/specs/2026-07-11-pluggable-sandbox-backend.md` / #36 — Phase 7, which built the seam and explicitly deferred Docker.
- #1 — the roadmap that scoped Docker out; this is its follow-through.
- `docs/sandbox.md:17-24`, `docs/configuration.md:47` — the doc surfaces that currently document `docker` as fail-fast.
- `tests/config/test_arms.py:551-616`, `tests/test_main.py:174-191`, `tests/sandbox/test_backend.py` — the fail-fast tests this flips and the backend test patterns to extend.

## Verification

- `make test` — proves registry/fingerprint/preflight/credential-mapping logic and the unchanged microsandbox path.
- `make lint` — proves the new module and doc updates pass style, importable without Docker installed.
- `harnessbench sandbox:build --set <docker-set>` — exits `0` with a committed `harnessbench-docker-…` image while the daemon runs; exits `2` with a readable preflight error when it doesn't.
- `harnessbench run --set <docker-set>` on a sample eval — proves a cell boots, grades, and lands in the matrix under container isolation.
