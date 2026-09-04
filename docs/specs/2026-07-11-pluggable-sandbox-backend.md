**TL;DR** — Make `runner` and `sandbox` first-class `[tool.evalspec.sets.<name>]` fields that fail fast on unsupported values, factor the microsandbox lifecycle behind a `SandboxBackend` adapter (preflight / build / session / cache-fingerprint), and fold backend id + declared base-image reference + harness install inputs into the snapshot cache identity — Phase 7 of the #1 roadmap. microsandbox stays the only concrete backend; Docker is **not** implemented, only the seam that would let it be added is.

## Problem

- **Symptom:** The sandbox backend is hardcoded, not selected. `microsandbox` is imported inline at every lifecycle call site — `Sandbox, Snapshot` in `_build_snapshot_async` (`sandbox.py:169`), `Sandbox, Volume` in `_create_sandbox` (`sandbox.py:291`), `MicrosandboxError` in `_guest_shell`/`_stop_quietly` (`sandbox.py:97,133`), `is_installed()` in `_microsandbox_installed` (`sandbox.py:48-54`). There is no interface a second backend could implement; adding Docker later means editing every one of these call sites.
- **Symptom:** `runner` and `sandbox` are not config at all. `Set` carries only `name`/`arms`/`baseline` (`arms.py:34-41`) and `_SET_DEFAULT_KEYS` is `("harness","model","effort","env","harness_args")` (`arms.py:53`) — no `runner`, no `sandbox`. The vision makes both set-level benchmark fields (`evalspec-readme-vision.md:53-56,163-164`), so an unsupported value today is silently ignored instead of failing fast.
- **Symptom:** The cache identity is incomplete. `snapshot_name` = `evalspec-{agent.id}-{agent.version()}-{env.digest()}` (`sandbox.py:35-40`); `env.digest()` hashes `base_image` string + script bytes (`discovery.py:141-146`). It omits the **backend** (a Docker and a microsandbox snapshot of the same agent+env would collide on one name) and pins nothing: `BASE_IMAGE = "ubuntu:latest"` (`sandbox.py:29`) is a floating tag, so an upstream image move does not invalidate the snapshot, and the harness **install inputs** (CLI installer version beyond `agent.version()`) never participate.
- **Symptom:** Preflight is microsandbox-shaped and unconditional. `preflight()` hardcodes Apple-Silicon / `/dev/kvm` / `microsandbox.is_installed()` checks (`sandbox.py:57-75`) with the microsandbox-specific remedy `make evals:build`. A different backend would need entirely different host checks, but there is no dispatch point.
- **Exposed by:** Roadmap #1 Phase 7 Done-When: `runner` and `sandbox` are parsed config fields; the sandbox backend is selected through a pluggable adapter (microsandbox only for now, shaped so Docker can be added without touching runtime callers); cache identity includes backend, harness version, image digest, package/install inputs, and environment setup bytes.
- **Scope:** Add `runner`/`sandbox` set fields + fail-fast validation; extract a `SandboxBackend` adapter with microsandbox as the sole implementation; enrich the cache fingerprint; and thread `--set`/`--config` into `evalspec sandbox:build` (deferred here by Phase 5). **Not** the *bare* `evalspec sandbox:build` / `evalspec run` commands themselves — Phase 5 shipped those (`#33`) with the shared arg/exit conventions; Phase 7 makes an eval *set* change what gets built. **Not** any second backend.
- **Constraint:** No compatibility shims (roadmap rule). microsandbox call sites move behind the adapter; nothing keeps importing `microsandbox` outside the backend module. Phase 5 established that `lint`/`analyze` must never require the microsandbox package — every `microsandbox` import is lazy (function-body, post-preflight; `sandbox.py:97,133,183`, `__main__.py:93`). The extraction must preserve that: importing the backend *module* must not import `microsandbox` at module load, so `lint`/`analyze` keep working with the package absent.
- **Constraint:** Docker is explicitly out. The adapter interface and the `sandbox = "docker"` fail-fast path are in scope; a Docker implementation is not (Out of Scope, and Out of Scope on #1).
- **Note:** `runner` has one value — `pytest` — and the runner *is* the pytest plugin. Phase 7 parses/validates/records `runner` and fails fast on anything else, but does **not** build a runner-plugin abstraction; only the sandbox axis gets an adapter, because that is where the future-backend modularization lives.

## Solution

```toml
[tool.evalspec.sets.default]
runner  = "pytest"        # parsed, validated, recorded; only "pytest" supported
sandbox = "microsandbox"  # selects the backend adapter; only "microsandbox" supported
arms    = [ ... ]
baseline = "baseline"
```

```text
$ evalspec run --set default            # sandbox = "microsandbox" → MicrosandboxBackend
  ... builds snapshot evalspec-microsandbox-claude-code-1.2.3-<fp> ...

$ evalspec run --set docker-set         # sandbox = "docker"
error: [tool.evalspec.sets.docker-set] unsupported sandbox `docker`
       (supported: microsandbox). Docker is not implemented.
$ echo $?
2
```

An unsupported `sandbox`/`runner` fails before any paid arm runs; the supported value routes through an adapter that owns preflight, build, session, and its slice of the cache key.

## User Stories

1. As a benchmark maintainer, I want **`runner` and `sandbox` in the set config with fail-fast validation**, so a typo or an unavailable backend errors before I pay for a run instead of silently defaulting.
2. As a maintainer adding a backend later, I want **one `SandboxBackend` interface**, so a Docker or exe.dev backend is a new adapter module, not edits scattered across `sandbox.py`.
3. As an eval author, I want **the snapshot cache keyed on the backend and the declared base image**, so a switched backend or an edited base-image reference rebuilds rather than silently reusing a stale VM. (A moved *floating* tag is not detected — evalspec does not resolve digests itself; see the Decision under Open Questions.)

## Implementation Decisions

```text
[tool.evalspec.sets.<name>] runner / sandbox
        │  parse_sets: validate against SUPPORTED_RUNNERS / registered backends
        │  unsupported → SchemaError → nonzero exit (before any arm runs)
        ▼
Set(runner="pytest", sandbox="microsandbox", …)
        │
        ▼
resolve_sandbox("microsandbox") ─► MicrosandboxBackend   (registry; "docker" absent → fail fast)
        │
        ├─ preflight()                → host-readiness errors (Apple Silicon / KVM / installed)
        ├─ cache_fingerprint(agent,env) → backend id · declared base-image ref · install inputs · env bytes
        ├─ build_snapshot(agent,name,env)   (was _build_snapshot_async)
        └─ open_session(...)                (was _create_sandbox / SandboxSession)

snapshot_name = evalspec-{backend.id}-{agent.id}-{agent.version()}-{cache_fingerprint}
```

- **Add `runner`/`sandbox` as set fields, validated in `parse_sets`.** Extend `Set` (`arms.py:34-41`) with `runner: str` and `sandbox: str`, read them in `parse_sets` (`arms.py:83-147`) alongside `baseline`, default `runner="pytest"` / `sandbox="microsandbox"`, and raise `SchemaError` when the value is not in `SUPPORTED_RUNNERS = {"pytest"}` / the backend registry. They are **set-level**, not arm-level — not in `_SET_DEFAULT_KEYS` (`arms.py:53`), which stays the agent-facing axes. Reuse the existing early-raise path so an unsupported value exits before any task arm (mirrors the judge-preflight ordering at `plugin.py:633`). **The exit code is `2` (usage), per the Phase 5 contract** — not a bespoke code. Through `run`, a collection-time `SchemaError`/`pytest.UsageError` surfaces as pytest status 2 → `exit_code_for_pytest_status` → `ExitCode.USAGE` (`src/evalspec/exit_codes.py`). Through `sandbox:build`, the `_run_sandbox_build` wrapper already splits `RuntimeError` → 2 (usage/preflight) from `MicrosandboxError` → 1 (build failure) (`__main__.py:67-101`); an unsupported `sandbox`/`runner` raised as `SchemaError` from config resolution lands on the exit-2 leg. Do not invent a new nonzero code — reuse `exit_codes.py`.
- **Extract a `SandboxBackend` adapter; microsandbox is the only implementation.** New `src/evalspec/sandboxes/` (or `backend.py`) defining the interface — `id`, `preflight() -> list[str]`, `cache_fingerprint(agent, env) -> str`, `build_snapshot(agent, name, env)`, `snapshot_exists(name) -> bool`, `open_session(...) -> SandboxSession`. Move the inline `microsandbox` imports (`sandbox.py:97,133,169,291`), `_microsandbox_installed` (`sandbox.py:48-54`), and the Darwin/KVM checks out of `preflight` (`sandbox.py:57-75`) into `MicrosandboxBackend`. `resolve_sandbox(name)` looks up a registry `{"microsandbox": MicrosandboxBackend}`; an unknown name (including `"docker"`) raises `SchemaError`. Runtime callers (`execution.py:326`, `SandboxSession`) take a resolved backend, never import `microsandbox`.
- **Fold backend + declared base image + install inputs into the cache fingerprint.** `snapshot_name` (`sandbox.py:35-40`) prepends `backend.id`; `cache_fingerprint` hashes, in addition to today's `env.digest()` (base-image string + script bytes, `discovery.py:141-146`): (a) the backend id, (b) the **declared** base-image reference (the tag/ref as configured — evalspec does *not* resolve it to a content digest; see the Decision under Open Questions), and (c) an agent **install fingerprint** — `CodingAgent.install_fingerprint()` hashes the adapter's `provision_script()` (the fully-resolved install commands, with any pinned version baked in), so a changed installer rebuilds even at a floating `version()`. Result: `evalspec-{backend}-{agent.id}-{agent.version()}-{fp}`. A moved floating base-image tag is not detected here; the backend records the actual pulled digest in Phase-8 artifacts.
- **Route preflight through the resolved backend.** `preflight()` (`sandbox.py:57-75`) becomes: resolve the set's backend, call `backend.preflight()` for host errors, then keep the shared `credential_preflight_error()` check (`sandbox.py:71-73`). The microsandbox remedy string (`make evals:build`) moves into `MicrosandboxBackend`.
- **Define the boundaries.** `runner` is parsed/validated/recorded only — no runner-plugin abstraction (one runner exists). Phase 5 (`#33`) shipped the *bare* `evalspec sandbox:build [root]` — its build resolves agent+env from the repo root, so an eval *set* does not change what is built, and it takes no `--set`/`--config` (`__main__.py:122-124` registers only the root positional). **Phase 7 threads `--set`/`--config` into `sandbox:build`:** add the flags to that subparser, thread `--config` into `resolve_environment_config` and `--set` into set resolution so the resolved `sandbox` backend and `env` drive the build, surfaced *through* the existing `_run_sandbox_build` wrapper (`__main__.py:67-101`) and `sandbox.cli_build(repo_root)` (`sandbox.py:605-623`) entry — no parallel build path. Phase 7 also exposes `backend.build_snapshot` / the `ensure_snapshot` entry (`sandbox.py:194-210`) that these wrap. **Deferred to Phase 8, not here:** *arm-aware, multi-harness* `sandbox:build` (one snapshot per harness in the resolved set) — that needs the arm metadata Phase 8 defines; Phase 7 threads set/config selection into a single-agent-per-invocation build. The new fingerprint fields **appear in artifacts** only once Phase 8 (metadata) lands — Phase 7 makes them participate in the cache key, not yet in `meta.json` (same interim gap the binder identity has). A committed two-set fixture (one `sandbox = "microsandbox"`, one `sandbox = "docker"`) gives the fail-fast path something to assert against.

## Testing Plan

### Logic
- **`runner`/`sandbox` validate and fail fast** — a set with `sandbox = "docker"` or `runner = "jest"` raises `SchemaError` naming the field and the supported values; a valid set resolves to `Set(runner="pytest", sandbox="microsandbox")`; both fields default when omitted.
- **`resolve_sandbox` registry** — `"microsandbox"` returns the backend; `"docker"` and any unknown name raise; the error names Docker as not implemented.
- **Cache fingerprint is complete and reproducible** — `snapshot_name` changes when the backend id, declared base-image reference, agent install fingerprint, or environment script bytes change, and is stable when none do; two backends over the same agent+env never collide on one name.
- **Backend id is part of identity** — `snapshot_name(agent, env)` for a microsandbox backend is prefixed `evalspec-microsandbox-`.

### Behavior
- **Supported set runs end to end** — a `sandbox = "microsandbox"` set builds/reuses a snapshot and runs an arm through the backend adapter with no direct `microsandbox` import outside the backend module.
- **Unsupported backend exits nonzero before any arm** — running the `docker` fixture set exits nonzero with a readable diagnostic and spends no task-model tokens.
- **Preflight dispatches to the backend** — the microsandbox host checks (Apple Silicon / KVM / installed) run via `MicrosandboxBackend.preflight()`; the shared credential preflight still runs.

### Interface
- **`sandbox`/`runner` are a public config surface** — `[tool.evalspec.sets.<name>]` accepts the supported values, rejects others early, and the resolved values are available on `Set` for Phase 8 to record.
- **No `microsandbox` import escapes the backend** — a grep-style test asserts `microsandbox` is imported only under the backend module; `execution.py`/`sandbox.py` callers use the resolved adapter.

## Open Questions

- **Module layout: `src/evalspec/sandboxes/` package vs a single `sandbox_backend.py`?** Recommendation: keep `sandbox.py` as the runtime session home and add a small `backend.py` + `MicrosandboxBackend` beside it now; promote to a `sandboxes/` package only if Phase 10 reorg lands. Resolved by a layout call before implementation.
- **Should the base-image digest be resolved (skopeo/registry) at build time?** **Decision (post-review): no — evalspec does not become an OCI registry client in Phase 7.** The cache key hashes the **declared** base-image reference as configured. A separate resolver (e.g. `skopeo inspect`) was prototyped and rejected: it duplicates the registry auth/mirror/platform resolution the sandbox backend already does at pull time (so the fingerprinted digest and the pulled digest could diverge), it is an extra host dependency whose absence silently degrades the guarantee, and a global OCI resolver leaks a registry assumption into a generic backend abstraction. Consequence: a floating tag (`ubuntu:latest`) is not reproducible across time/machines — documented as a known limitation; pin a digest in `base_image` for strict reproducibility. The authoritative identity is what the backend actually pulled: Phase 8 records that backend-native digest (for microsandbox, via `ImageHandle.manifest_digest` / `Image.inspect`) in `meta.json` artifacts.

## Documentation Plan

- **`docs/configuration.md`**: document set-level `runner`/`sandbox`, the supported values, the fail-fast behavior, the pluggable-backend interface (microsandbox only for now), and the cache-key inputs (backend, harness version, declared base-image reference, install inputs, environment setup bytes).
- **`docs/agents.md`**: note the new `CodingAgent.install_fingerprint()` opt-in and the backend/adapter boundary that harness adapters sit beside.
- **`docs/concepts.md`**: reconcile the sandbox section with the adapter model; state Docker/exe.dev as future backends the seam allows, not shipped ones.

## Out of Scope

- Implementing a Docker, exe.dev, or any second sandbox backend — only the adapter boundary and the `sandbox = "docker"` fail-fast path.
- The *bare* `evalspec sandbox:build` / `evalspec run` commands and the shared arg/exit conventions — Phase 5 (`#33`). Phase 7 adds `--set`/`--config` to `sandbox:build` (deferred here by Phase 5) but reuses the existing wrapper and exit-code contract, not a new command.
- *Arm-aware, multi-harness* `sandbox:build` (one snapshot per harness in the resolved set) — Phase 8, which owns the arm metadata it needs.
- Recording backend / image-digest / install-fingerprint fields in `meta.json` / index rows — Phase 8 (metadata).
- A runner-plugin abstraction — one runner (`pytest`) exists; `runner` is validated config only.
- Changing the microsandbox VM shape (CPUs/memory), routing, or artifact capture.

## References

- #1 — roadmap umbrella; Phase 7 Done-When and the "pluggable adapter, microsandbox-only, Docker not implemented" decision (updated 2026-07-11).
- #36 — the Phase 7 implementation issue split from #1.
- #33 (Phase 5) — shipped the bare CLI + exit-code contract and deferred `sandbox:build --set`/`--config` here.
- `docs/research/evalspec-readme-vision.md:53-56,63-64,163-164,244-247` — runner/sandbox as set fields, capability-matrix targets, and the on-demand build model.
- `src/evalspec/sandbox.py:29,35-40,48-75,97,133,169,194-210,291` — `BASE_IMAGE`, `snapshot_name`, microsandbox-specific preflight, inline backend imports, and the build/session lifecycle to extract.
- `src/evalspec/discovery.py:125-176` — `EnvConfig.digest()` (base-image string + script bytes) the fingerprint extends.
- `src/evalspec/arms.py:34-41,53,83-147` — `Set`, `_SET_DEFAULT_KEYS`, `parse_sets` where `runner`/`sandbox` are added and validated.
- `src/evalspec/execution.py:326` — `ensure_snapshot` call site that takes a resolved backend.
- `src/evalspec/agents/base.py` — `CodingAgent` where `install_fingerprint()` is added.
- `src/evalspec/exit_codes.py` — the Phase 5 exit-code contract (`ExitCode.USAGE = 2`, `exit_code_for_pytest_status`) that the fail-fast path reuses.
- `src/evalspec/__main__.py:67-101,122-124` — `_run_sandbox_build` (the `RuntimeError`→2 / `MicrosandboxError`→1 split, lazy microsandbox import) and the `sandbox:build` subparser Phase 7 adds `--set`/`--config` to.
- `src/evalspec/sandbox.py:605-623` — `cli_build(repo_root)`, the build entry `sandbox:build` wraps and that `--set`/`--config` feed.

## Verification

- `make test` — proves `runner`/`sandbox` parsing + fail-fast, `resolve_sandbox`, the enriched fingerprint, and the backend-boundary import test pass.
- `make lint` — proves package and docs satisfy lint after the backend extraction.
- `evalspec run --set <docker-fixture>` exits `2` — proves `sandbox` is a first-class field and an unimplemented backend fails cleanly (usage, before any arm) via the Phase 5 exit-code contract, not a bespoke code.
- `evalspec run --set <microsandbox-fixture>` — proves the supported backend routes through the adapter and builds/reuses a snapshot whose name carries the backend id.
- `evalspec sandbox:build --set <microsandbox-fixture>` and `evalspec sandbox:build --config <fixture>` — prove `--set`/`--config` (deferred here by Phase 5) select which set/config drives the build through the resolved backend; `--set <docker-fixture>` exits `2`.
