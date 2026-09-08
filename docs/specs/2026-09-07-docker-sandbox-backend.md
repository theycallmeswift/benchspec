# Docker sandbox backend: implement `sandbox = "docker"`

Source: https://github.com/theycallmeswift/benchspec/issues/75 (captured 2026-09-07).

Follow-through on the seam #36 (Phase 7) built. Supersedes #66, which was filed under the old `harnessbench` name and closed as not planned. Builds on the `sandbox:clean` wiring, the `benchspec-*` namespace, and the CLI e2e suite merged in PR #76 (#72). Spec at `docs/specs/2026-09-07-docker-sandbox-backend.md`.

**TL;DR** — Implement `DockerBackend` as the second concrete `SandboxBackend`, make `docker` the default `sandbox` value with microsandbox the opt-in, teach `sandbox:clean` to prune Docker containers and images under the same `benchspec-` prefix, and make the two seam repairs this forces (backend-neutral agent credentials, a backend-neutral sandbox error), so evals run on any host with a Docker daemon.

## Problem

- **Symptom:** `sandbox = "docker"` is recognized but unimplemented. `_NOT_IMPLEMENTED = {"docker": "Docker is not implemented"}` at `src/benchspec/sandbox/backend.py:370` makes `resolve_sandbox("docker")` raise `SchemaError`, so a Docker set exits `2` before any arm (`tests/config/test_arms.py:551`, `tests/test_main.py:174`, `tests/sandbox/test_backend.py:27`, `tests/cli/test_sandbox_build_e2e.py:18`).
- **Symptom:** the default backend excludes common machines. `DEFAULT_SANDBOX = "microsandbox"` (`backend.py:50`) flows into every unpinned set (`config/arms.py:52,64,165`), the bare `sandbox:build` (`sandbox/sandbox.py:572`), trigger routing (`sandbox/sandbox.py:488`), and preflight (`sandbox/sandbox.py:61`). `MicrosandboxBackend.preflight()` (`backend.py:153`) requires Apple Silicon or Linux with `/dev/kvm`, so x86 macOS, Windows/WSL, and CI runners without nested virtualization cannot run evals at all. Docker is the runtime those hosts already have.
- **Symptom:** `sandbox:clean` is microsandbox-only. `cli_clean` (`sandbox/sandbox.py:604`) walks `~/.microsandbox` for `NAME_PREFIX` entries and shells out to `msb`; once Docker cells exist, its containers and committed images would accumulate with no command to reclaim them.
- **Symptom (seam leak):** every agent adapter builds microsandbox `Secret.env(..., allow_hosts=[...])` objects inside `secrets()` (`agents/claude.py:167`, `agents/codex.py:185`, `agents/opencode.py:318`) and `create_sandbox` passes them straight to `Sandbox.create`. A second backend cannot consume them.
- **Symptom (seam leak):** agents catch `MicrosandboxError` at the invoke boundary to classify a sandbox failure as a recorded infra error (`agents/claude.py:323`, `agents/codex.py:351`, `agents/opencode.py:423`). A Docker runtime failure would propagate as a crash instead of an `is_error` arm.
- **Why it stayed hidden:** Phase 7 (#36) deliberately built only the seam and the fail-fast stub. The prior implementation issue (#66) was filed under the old `harnessbench` name and closed as not planned before the rename; this spec re-grounds it against the current tree.
- **Exposed by:** PR #76 (for #72, merged) namespaced every sandbox and snapshot under `NAME_PREFIX = "benchspec-"` (`backend.py:53`) and added real-subprocess CLI end-to-end tests under `tests/cli/`. One asserts that a Docker set is a usage error, and the `sandbox:clean` tests substitute only `msb`. Both need Docker counterparts.
- **Constraint:** the lazy-import invariant holds for the new backend. Importing `benchspec.sandbox.*` must not import any Docker client package, so `lint`/`analyze` keep working on a host with nothing but Python (`backend.py` module docstring, `tests/sandbox/test_backend.py:253`).
- **Constraint:** the guest-runtime contract is implicit, not typed. Whatever `create_sandbox` returns must provide `.shell(script, env=, cwd=)` returning `exit_code`/`stdout_text`/`stderr_text` (`sandbox/sandbox.py:198`), `.exec(cmd, args, cwd=, env=, timeout=, stdin=)` (`orchestration/environments.py:97`), `.exec_stream(cmd, args, cwd=, env=, stdin=)` yielding `stdout`/`exited`/`failed` events with `.kill()` (`sandbox/sandbox.py:418`), and `.stop()`; plus a `Volume`-like class with `.bind(path, readonly=)` handed to `_agent_extra_volumes` (`sandbox/sandbox.py:160`).
- **Constraint:** isolation semantics differ and must be stated, not hidden. A container shares the host kernel; the agent runs as root with `bypassPermissions` on the promise that the sandbox is the containment boundary (`agents/claude.py:160`). With Docker as the default, the docs must lead with that tradeoff and name microsandbox as the stronger opt-in.
- **Scope:** one new backend behind the existing seam, the default flip, Docker pruning in `sandbox:clean`, Docker versions of the CLI e2e tests, and the two seam repairs. No change to microsandbox behavior or the cache-key ingredients.

## Solution

```toml
[tool.benchspec.sets.ci]            # no `sandbox` key: runs under Docker, the new default
model    = "sonnet"
baseline = "baseline"
arms     = [{ name = "baseline", harness = "claude-code" }, { name = "trial" }]

[tool.benchspec.sets.hardened]
sandbox  = "microsandbox"           # explicit opt-in to microVM isolation
```

```text
$ benchspec sandbox:build --set ci    # commits image benchspec-snapshot:benchspec-docker-claude-code-<v>-<fp>
$ benchspec run --set ci              # each cell runs as container benchspec-eval-<id>-<arm>-<worker>
$ benchspec sandbox:clean             # prunes benchspec-* containers and images too
```

Same set config, snapshot cache model, and exit-code contract, with cells in Docker containers by default and microVMs on request.

## User Stories

1. As a first-time user on **any machine with Docker**, I want `benchspec run` to work out of the box, so the quickstart is not gated on Apple Silicon or KVM.
2. As a benchmark maintainer, I want **backend choice to stay one set-level key**, so the same evals and arms run under either backend with no other config change.
3. As an eval operator, I want **`sandbox:clean` to reclaim Docker containers and images**, so a bloated Docker host is fixed by the same command as a bloated `~/.microsandbox`.
4. As a results consumer, I want **the backend id and image digest in `observed_arms`**, so a matrix produced under container isolation is distinguishable from one produced under microVMs.
5. As a security-conscious operator, I want **the docs to say plainly what Docker gives up**, so choosing the default is an informed choice and the microsandbox opt-in is easy to find.

## Implementation Decisions

```text
[tool.benchspec.sets.<name>] (sandbox omitted) ──► arms.parse_sets ──► DEFAULT_SANDBOX = "docker"
sandbox = "docker" ──► backend.resolve_sandbox() ──► DockerBackend   (new: sandbox/docker.py)
                                              │ registered in backend._REGISTRY; _NOT_IMPLEMENTED deleted
                                              ▼
  preflight()            docker_binary() resolves + `docker info` reachable
  fingerprint_inputs()   same four ingredients, backend_id="docker"
  snapshot_exists()      `docker image inspect benchspec-snapshot:<snapshot_name>`
  build_snapshot()       docker run --name benchspec-build-<agent> <base_image> → agent.provision
                         → bridge_skills_home → environment_script (set -e) → `docker commit`
  image_identity()       `docker image inspect --format {{.Id}}` → ImageIdentity
  create_sandbox()       `docker run -d --name benchspec-eval-… -v <workdir>:/workspace -v <stage>:/project:ro
                          --cpus VM_CPUS --memory VM_MEMORY_MIB -e <credential>`
  create_trigger_sandbox()  `--name benchspec-trigger-<worker>`, /project only, then agent.stage_project_assets
  prune()                `docker rm -f` NAME_PREFIX* containers + `docker rmi -f` benchspec-snapshot:NAME_PREFIX*
                                              │
                                              ▼
  DockerSandbox           .shell / .exec / .exec_stream / .stop over `docker exec` / `docker rm -f`
  DockerVolume.bind()     renders the -v flag for _agent_extra_volumes

benchspec sandbox:clean ──► sandbox.cli_clean ──► for each backend in _REGISTRY: backend.prune() ──► unlink lock files
```

- **Flip the default to Docker.** `DEFAULT_SANDBOX` (`backend.py:50`) becomes `"docker"`; `MicrosandboxBackend.id` (`backend.py:146`) and its `_REGISTRY` key (`backend.py:367`) become the literal `"microsandbox"` so the constant no longer doubles as that backend's name. Every consumer of `DEFAULT_SANDBOX` (`config/arms.py`, `sandbox/sandbox.py:61,488,572`, `orchestration/execution.py:387`) inherits the flip unchanged. The repo's own `e2e` set in `pyproject.toml` stays unpinned so `make e2e` exercises the default. Tests that encode the old default (`tests/runners/test_pytest.py:280`, `tests/orchestration/test_execution.py:816`) update to the new one, and `tests/fixtures/sandbox/two-backends.toml` becomes an accept fixture with one set per backend.
- **Drive Docker through the CLI, not a client library.** `asyncio.create_subprocess_exec` around `docker` subcommands: zero new dependencies, the lazy-import invariant is satisfied trivially, and the CLI is the one interface every Docker-equipped host has. `docker_binary()` mirrors `msb_binary()` (`backend.py:59`): a `BENCHSPEC_DOCKER_PATH` override wins, else `shutil.which("docker")`, so tests can substitute a logging shim the way `tests/cli/test_sandbox_clean_e2e.py:47` substitutes `msb` via `MSB_PATH`.
- **A snapshot is a committed local image in one repository.** Images are tagged `benchspec-snapshot:<snapshot_name>`; the tag charset admits everything `snapshot_name()` (`sandbox/sandbox.py:42`) produces, which already starts with `NAME_PREFIX`, and a single repository keeps listing and pruning to one `docker images` call. The build mirrors `MicrosandboxBackend._build_snapshot_async` (`backend.py:263`): run the base image as `benchspec-build-<agent>` (`backend.py:268`), `agent.provision()`, skills-home bridge, environment script under `set -e`, then `docker commit`; a failed build removes the build container. `fingerprint_inputs()` reuses the same four ingredients with `backend_id="docker"`, so names never collide across backends and provenance capture (`orchestration/execution.py:315`) works unchanged.
- **Containers carry the existing `benchspec-*` run names.** `_sandbox_run_name` (`benchspec-eval-<id>-<config>-<worker>`, `sandbox/sandbox.py:140`), the trigger name (`benchspec-trigger-<worker>`, `sandbox/sandbox.py:406`), and the build name are passed as `--name`, so `NAME_PREFIX` selects every benchspec-owned resource on either backend and the prune rule is identical across them. `replace=True` semantics via remove-then-run on the same name; per-cell teardown removes the container, so Docker does not inherit the leak #72 describes for microsandbox.
- **`sandbox:clean` prunes every registered backend.** `prune()` joins the `SandboxBackend` Protocol (`backend.py:93`). `MicrosandboxBackend.prune()` takes the current `msb` walk out of `cli_clean` (`sandbox/sandbox.py:604`); `DockerBackend.prune()` removes containers whose name starts with `NAME_PREFIX` and images under `benchspec-snapshot` whose tag starts with `NAME_PREFIX`. `cli_clean` calls `prune()` on each registry entry, then unlinks the lock files as today. A missing runtime binary means nothing to prune, exactly as `msb_binary() is None` does now, so the command still exits `0` on a host with neither runtime and `_run_sandbox_clean` (`__main__.py:116`) keeps its no-error-mapping shape.
- **Generalize `agent.secrets()` to a backend-neutral declaration.** Agents return plain credential specs (env name, value, allowed hosts) from a small dataclass in `agents/base.py`. `MicrosandboxBackend.create_sandbox` (`backend.py:307`) maps them to `Secret.env`; `DockerBackend` maps them to `-e` container env vars. The three agent modules drop their `microsandbox.Secret` imports and leave the allowlist in `tests/sandbox/test_backend.py:253`. Docker cannot enforce `allow_hosts`: the credential is guest-readable there, and the docs state that as part of the default's tradeoff.
- **Introduce a backend-neutral sandbox error.** A `SandboxError` in `sandbox/backend.py` that each backend raises (or wraps its runtime's exceptions into) for daemon-gone, container-killed, and exec-torn-down failures. Agents catch it alongside `TimeoutError`/`OSError`, replacing the direct `MicrosandboxError` imports at the invoke boundary, so a Docker failure records an `is_error` arm instead of crashing the run. `_run_sandbox_build` (`__main__.py:72`) maps it to `ExitCode.FINDING` the same way it maps `MicrosandboxError` today.
- **`DockerSandbox` and `DockerVolume` satisfy the implicit guest contract.** `.shell` wraps `docker exec -i <name> bash -c`; `.exec` and `.exec_stream` wrap `docker exec` with `-w` and `-e` flags, the stream yielding `stdout`/`exited`/`failed` events and `.kill()` terminating the exec subprocess; `.stop` is `docker rm -f`. `SandboxSession`, trigger routing, `run_setup_sh`, and the agents stay untouched above the seam. Resource limits come from the existing `VM_CPUS`/`VM_MEMORY_MIB` (`backend.py:55`).
- **Preflight is Docker-shaped.** Missing CLI and unreachable daemon each produce a specific remedy string; platform is not checked, since any host with a working daemon qualifies. The shared credential preflight still runs via `sandbox.preflight()` (`sandbox/sandbox.py:54`).
- **CLI e2e tests get Docker twins.** Following the merged pattern (real `python -m benchspec` subprocess via `run_benchspec` in `tests/support/cli.py:22`, a logging shim substituted through an env override), `tests/cli/test_sandbox_build_e2e.py` flips its Docker usage-error case (`:18`) to a positive build through a `docker` shim and adds a preflight-failure case with `BENCHSPEC_DOCKER_PATH` pointed at a missing path; `tests/cli/test_sandbox_clean_e2e.py` gains a case where the `docker` shim reports leaked `benchspec-*` containers and images and the command log shows each `rm -f` and `rmi -f`, mirroring the `msb` case at `:53`. A daemon-gated case runs the real build and a real cell, skipped with an explicit reason when `docker info` fails.

## Testing Plan

### Logic
- **Registry and default** — `resolve_sandbox("docker")` returns a `DockerBackend`, an omitted `sandbox` key resolves to it, `"microsandbox"` still resolves, and an unknown name raises listing both supported values.
- **Cache identity is backend-distinct** — the Docker fingerprint folds `backend_id="docker"` with the same base-image/install/env ingredients; a Docker and a microsandbox snapshot of one agent+env get different names, and each rebuilds on any ingredient change.
- **Preflight reports the Docker-shaped remedy** — a missing CLI and an unreachable daemon each yield a specific, actionable error string.
- **Credential mapping per backend** — one neutral credential spec renders as a scoped microsandbox `Secret` and as a Docker env flag with byte-identical values.
- **Docker command rendering** — mounts, resource flags, env, working directory, `benchspec-*` container names, and stdin handling render to the expected `docker` argv without a daemon present.
- **Prune selects only benchspec-owned resources** — containers without the `NAME_PREFIX` name and images outside the `benchspec-snapshot` repository or without the `NAME_PREFIX` tag are left alone.

### Behavior
- **A full cell runs end to end under Docker** — build or reuse the committed image, start a container, run `setup.sh`, invoke the agent, gather facts, tear down; gated on a reachable daemon and skipped with an explicit reason otherwise.
- **A runtime failure records, not crashes** — a container killed mid-turn yields an `is_error` arm result excluded from grading, same as the microsandbox path.
- **Trigger routing streams and early-stops** — the routing probe sees line-delimited stdout events through `exec_stream` and `kill()` ends the exec on dispatch.
- **`sandbox:clean` reclaims both runtimes** — after a clean, leaked `benchspec-*` containers and committed images are gone alongside the `~/.microsandbox` entries and lock files, and a host with neither runtime still exits `0`.
- **The microsandbox path is unchanged** — an existing `sandbox = "microsandbox"` set produces the same snapshot names and artifacts after the seam repairs.

### Interface
- **An unpinned set runs under Docker** — `[tool.benchspec.sets.<name>]` without a `sandbox` key drives `sandbox:build --set` and `run --set` through `DockerBackend`, and `sandbox = "docker"` is the explicit equivalent, with the exit-code contract kept (`0` built or reused, `2` config or preflight, `1` build failure).
- **The CLI e2e suite covers Docker through the real entry point** — the `tests/cli/` subprocess tests exercise `sandbox:build` and `sandbox:clean` against a `docker` shim, and a daemon-gated case against the real daemon, mirroring the merged `msb` shim cases.
- **No Docker client package at import time** — importing `benchspec.sandbox.docker` needs nothing beyond the stdlib; `lint`/`analyze` run on a Docker-less host.
- **`observed_arms[arm].sandbox.backend` reads `docker`** with an available image digest for a Docker run.

## Open Questions

- **Rootless Docker and Podman:** does the exec/commit/mount surface used here work under rootless Docker or `podman` aliased as `docker`? Resolve by running the behavior suite on those targets; until then the docs claim Docker Engine/Desktop only.
- **Credential posture for subscription tokens:** a guest-readable OAuth or subscription token is a bigger exposure than a scoped API key, and Docker is now the default. Should Docker runs warn, or refuse, when an arm's credential is subscription-shaped? Default proposal is a loud warning, not a refusal.
- **Windows hosts:** Docker Desktop on Windows would make benchspec runnable there for the first time. Supported target or explicitly untested? Resolve after the behavior suite exists.

## Documentation Plan

- **`docs/quickstart.md`**: replace the Apple Silicon / KVM requirement at line 11 with "a running Docker daemon", move the microsandbox extra and its preflight notes (lines 32, 223-226) under a "stronger isolation" opt-in subsection.
- **`README.md`**: update the install line at 35, the note at 39, and the platform row at 91 so Docker is the default and microsandbox the opt-in; extend the `sandbox:clean` mention at 119 to Docker resources.
- **`docs/sandbox.md`**: replace the "only backend implemented" note at line 21 and the host list at 30 with a backend section that leads with Docker (daemon reachable, any platform, shared-kernel isolation, credential visibility, image cache under `docker images benchspec-snapshot`), then microsandbox as the microVM opt-in; extend the `sandbox:clean` block at 92-95 to containers and images.
- **`docs/configuration.md`**: update the `sandbox` row at line 47 to list both values with `docker` as the default; extend the `sandbox:clean` notes at 133 and 141 to Docker resources.
- **`docs/harnesses.md`**: per-harness credential notes gain a Docker column: env-var injection, no host scoping.

## Out of Scope

- Replicating microsandbox's `allow_hosts` network-scoped secret injection in Docker. There is no equivalent primitive; the tradeoff is documented instead.
- Remote Docker hosts (`DOCKER_HOST`), Podman or rootless guarantees, and Windows support claims. Candidates after the Open Questions resolve.
- Any change to microsandbox backend behavior, VM sizing, the cache-key ingredient set, or the `benchspec-*` naming PR #76 landed.
- Removing the microsandbox extra or dropping microsandbox support. It stays a first-class opt-in.
- A third backend (exe.dev or otherwise). This proves the seam holds by adding exactly one.

## References

- #66 — the prior Docker backend issue, filed under the `harnessbench` name and closed as not planned; this spec supersedes it against the renamed tree.
- #36 and `docs/specs/2026-07-11-pluggable-sandbox-backend.md` — Phase 7, which built the seam and explicitly deferred Docker.
- #72 and PR #76 (merged as `8d55e8e`) — `sandbox:clean` CLI wiring, the `benchspec-*` namespace for every sandbox and snapshot, and the `tests/cli/` e2e suite with `tests/support/cli.py` helpers this spec extends with Docker cases.
- `src/benchspec/sandbox/backend.py:50,53,55,59,93,143,146,153,263,268,307,336,367,370,373` — `DEFAULT_SANDBOX`, `NAME_PREFIX`, `VM_CPUS`, `msb_binary`, the `SandboxBackend` Protocol, `MicrosandboxBackend` and its `id`, preflight, snapshot build and build name, `create_sandbox`, `create_trigger_sandbox`, `_REGISTRY`, the `_NOT_IMPLEMENTED` stub this deletes, and `resolve_sandbox`.
- `src/benchspec/sandbox/sandbox.py:42,54,61,140,160,175,206,386,406,418,488,572,604` — `snapshot_name`, shared preflight and its default, `_sandbox_run_name`, `_agent_extra_volumes`, `run_setup_sh`, `SandboxSession`, trigger routing with its name and default, the bare-build default, and `cli_clean`.
- `src/benchspec/config/arms.py:52,64,165` — where an omitted `sandbox` key takes `DEFAULT_SANDBOX`.
- `src/benchspec/orchestration/environments.py:97` — `GuestSandbox.exec`, the third guest exec surface Docker must satisfy.
- `src/benchspec/orchestration/execution.py:315,387` — `_capture_sandbox_provenance` and the execution-side default.
- `src/benchspec/agents/claude.py:167,323`, `agents/codex.py:185,351`, `agents/opencode.py:318,423` — the two seam leaks: microsandbox `Secret` construction and the `MicrosandboxError` catch.
- `src/benchspec/__main__.py:72,116` — `_run_sandbox_build`, whose exit-code split gains the neutral error, and `_run_sandbox_clean`, whose no-error shape the Docker prune preserves.
- `tests/cli/test_sandbox_build_e2e.py:18,38`, `tests/cli/test_sandbox_clean_e2e.py:47,53,94`, `tests/support/cli.py:22` — the merged e2e cases and helpers the Docker twins mirror.
- `docs/quickstart.md:11,32,223-226`, `README.md:35,39,91,119`, `docs/sandbox.md:21,30,92-95`, `docs/configuration.md:47,133,141` — the doc surfaces that currently present microsandbox as the default, `docker` as fail-fast, and `sandbox:clean` as microsandbox-only.
- `tests/config/test_arms.py:551,611`, `tests/test_main.py:174`, `tests/sandbox/test_backend.py:27,253`, `tests/runners/test_pytest.py:280`, `tests/orchestration/test_execution.py:816`, `tests/fixtures/sandbox/two-backends.toml` — the fail-fast tests, default-encoding tests, and fixture this flips, and the import allowlist to shrink.

## Verification

- `make test` — proves registry, default, fingerprint, preflight, credential-mapping, command-rendering, and prune logic, plus the unchanged microsandbox path, and runs the shim-backed `tests/cli/` Docker cases.
- `make lint` — proves the new module and doc updates pass style and import without Docker installed.
- `uv run benchspec sandbox:build` — with no set or config, exits `0` with a committed `benchspec-snapshot:benchspec-docker-…` image while the daemon runs; exits `2` with a readable preflight error when it does not.
- `uv run benchspec run --set <unpinned-set>` on a sample eval — proves a cell boots, grades, and lands in the matrix with `observed_arms[arm].sandbox.backend == "docker"`.
- `uv run benchspec sandbox:clean && docker ps -a --filter name=benchspec- -q | wc -l` — prints `0` on a host that had leaked eval containers and no run in flight.

## Done When

- `resolve_sandbox("docker")` returns a working `DockerBackend`; `"docker"` is gone from `_NOT_IMPLEMENTED`; the fail-fast tests flip to positive coverage.
- `DEFAULT_SANDBOX` is `"docker"`: an unpinned set, the bare `sandbox:build`, trigger routing, and preflight all run under Docker; `sandbox = "microsandbox"` still selects the microVM backend.
- `benchspec sandbox:build` commits a `benchspec-snapshot:benchspec-docker-<harness>-<version>-<fp>` image (exit `0`), exits `2` on a Docker-less host with a specific remedy, and `1` on a genuine build failure.
- A cell runs end to end in a container named `benchspec-eval-…` (stage, start, `setup.sh`, agent invoke, fact gathering, teardown) and lands in the matrix; a mid-turn runtime failure records an `is_error` arm instead of crashing the run.
- `benchspec sandbox:clean` prunes `benchspec-*` containers and `benchspec-snapshot:benchspec-*` images alongside the microsandbox entries, and exits `0` on a host with neither runtime.
- `tests/cli/` has Docker twins of the `sandbox:build` and `sandbox:clean` e2e cases (shim-backed via `BENCHSPEC_DOCKER_PATH`) plus a daemon-gated real-cell case, following the merged `run_benchspec` pattern.
- `agent.secrets()` is backend-neutral (microsandbox maps to scoped `Secret.env`, Docker to container env) and no agent imports `MicrosandboxError` or `Secret` directly.
- Importing `benchspec.sandbox.docker` needs nothing beyond the stdlib; `lint`/`analyze` still run with neither runtime installed.
- `docs/quickstart.md`, `README.md`, `docs/sandbox.md`, `docs/configuration.md`, and `docs/harnesses.md` present Docker as the default, microsandbox as the isolation opt-in, and state the shared-kernel and credential-visibility tradeoffs.

