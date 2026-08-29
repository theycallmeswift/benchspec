**TL;DR** — Make run artifacts distinguish **what was configured** from **what
actually ran**. Bump `meta.json` to v2 and `benchmark.json` to v3; record the
planned arm roster separately from observed per-arm runtime provenance; denormalize
`harness`/`model`/`effort` onto `index.jsonl`; record judge and binder identity; thread
the resolved sandbox through preflight and execution; and make
`sandbox:build --set` build once per distinct harness. Runtime provenance is captured
when an arm resolves and uses its snapshot, not reconstructed from config at run-finish.

## Problem

Phase 8 collects identity debts left by Phases 1–7:

- `meta.json` has a configured arm roster but no per-arm runtime version or sandbox
  identity. Its run-level `agent`, `agent_version`, and `token_split` describe only the
  default harness and become false or ambiguous for multi-harness sets.
- The snapshot cache key includes backend, declared base-image reference, agent install
  fingerprint, and environment script bytes, but artifacts expose none of those inputs.
- A floating image tag can move. The backend knows the manifest digest it pulled, but
  evalspec does not record it.
- Recomputing provenance from config at `pytest_sessionfinish` cannot prove what ran. It
  also assigns runtime facts to configured arms that were deselected, skipped, or never
  produced a sample.
- An adapter's `version()` is an install selector such as `latest`, not necessarily the
  version of the binary inside a reused snapshot. A host-side probe measures the judge
  binary, not the task harness in the guest.
- Binder identity is absent, `index.jsonl` rows require a join to learn their core arm
  axes, and the resolved `Set.sandbox` is still dropped before preflight/execution.
- `sandbox:build --set` builds only the default agent snapshot.

Phase 8 records and threads identity only. It changes no grading or matrix math and adds
no second sandbox backend.

## Artifact Contract

`meta.json` v2 separates planned configuration from observed execution:

```jsonc
{
  "format_version": 2,
  "run_id": "…",
  "commit": "…",
  "config_hash": "…",
  "set": "default",
  "runner": "pytest",
  "arms": [
    {
      "name": "opus",
      "harness": "claude-code",
      "model": "opus-4.8",
      "effort": "medium",
      "env": {},
      "harness_args": [],
      "requested_version": "latest",
      "capabilities": {"token_split": true}
    }
  ],
  "observed_arms": {
    "opus": {
      "actual_version": "1.2.3",
      "actual_version_status": "available",
      "sandbox": {
        "backend": "microsandbox",
        "snapshot": "evalspec-microsandbox-claude-code-latest-ab12cd34",
        "fingerprint": "ab12cd34",
        "base_image_ref": "ubuntu:latest",
        "install_fingerprint": "…",
        "env_script_sha256": "…",
        "image_digest": "sha256:…",
        "image_digest_status": "available"
      }
    }
  },
  "judge": {
    "harness": "claude-code",
    "model": "…",
    "effort": "medium",
    "timeout": 60,
    "env": {},
    "harness_args": [],
    "actual_version": "1.2.3"
  },
  "binder": {
    "provider": "gemini",
    "model": "gemini-3.1-flash-lite",
    "api_path": "generativelanguage.googleapis.com/v1beta"
  }
}
```

- `arms` is the complete configured roster, including arms that never ran.
- `observed_arms` contains only arms for which execution selected a snapshot. It is
  aggregated from runtime records, never synthesized for absent arms.
- Remove the v1 run-level `agent`, `agent_version`, and `token_split` fields. There are
  no compatibility aliases. The install selector and concrete capabilities belong on
  each planned arm; observed binary identity belongs under `observed_arms`.
- `actual_version_status` and `image_digest_status` are `available` or `unavailable`.
  When unavailable, the value is `null` and an adjacent `*_error` string explains why.
  A successful backend lookup must never silently become an unexplained null.
- The judge is host-side, so its `actual_version` comes from the existing
  `probe_judge_version()` / `binary_version()` path.
- Binder metadata describes the configured assertion binder, not the grader. Export its
  identity from `binder.py`; do not derive public schema fields by parsing a private URL.

Each `index.jsonl` sample row gains the three core configured axes:

```jsonc
{"skill":"foo","kind":"eval","eval_id":"x","arm":"opus",
 "harness":"claude-code","model":"opus-4.8","effort":"medium",
 "sample":0,"errored":false,"passed":3,"total":3}
```

`benchmark.json` v3 carries the same planned arm metadata, observed runtime provenance,
`runner`, and `binder`. Configured-but-unobserved arms remain valid empty matrix columns,
but their runtime provenance is absent rather than fabricated. `benchmark.md` gets a
compact Provenance section that labels configured-only arms as `not observed`.

## Runtime Capture

```text
resolved Set
    ├── resolved backend ──► session preflight
    ├── each arm ──────────► run_eval_arm
    │                           ├── resolve agent/backend/snapshot
    │                           ├── read backend image identity
    │                           ├── probe guest binary version
    │                           └── persist RuntimeProvenance with sample
    └── sessionfinish ─────► aggregate planned config + observed records
```

- Add a small typed runtime-provenance record shared by execution and reporting. Capture
  it immediately after `ensure_snapshot()` returns and before the task harness runs.
- Persist provenance beside each sample (for example `provenance.json`) so xdist workers,
  interrupted aggregation, and later offline analysis retain the facts. Identical records
  for one arm deduplicate during aggregation. Conflicting records for one arm fail loudly;
  they mean one report would otherwise combine unlike environments.
- Probe the task harness binary inside the selected snapshot/runtime. Do not use
  `for_host().version()`: `version()` is commonly the requested selector (`latest`) and
  the host binding is the judge installation. The backend may expose a narrow command or
  inspection hook needed to run `<agent binary> --version` against the snapshot.
- Add a backend image-identity method returning a typed available/unavailable result.
  `MicrosandboxBackend` reads the selected snapshot's native manifest digest. Backend
  exceptions become an explicit unavailable result in the artifact; they do not abort
  completed artifact aggregation.
- Expose the fingerprint and its inputs from one helper used by both `snapshot_name()` and
  provenance capture. Do not recover the fingerprint by parsing the snapshot-name suffix
  or reimplement the hash inputs in `plugin.py`.
- Arms sharing a harness share a snapshot today because version selection is harness-level
  environment configuration and model/effort/harness args are runtime-only. Provenance is
  still keyed by arm so a future per-arm install selector does not require a schema break.

## Sandbox Threading and Build

- Resolve the selected set for the session preflight and pass its backend to
  `sandbox.preflight()`. Trigger-only runs with no eval set keep the default backend path.
- Pass the resolved `EvalSet` sandbox value from `cases.py` into `run_eval_arm`; execution
  resolves and uses that backend instead of `DEFAULT_SANDBOX`.
- With `--set` or `--config`, `sandbox:build` resolves the set and builds once per distinct
  harness via `make_agent(harness)`. Preserve the bare `sandbox:build` path, which builds
  only the default agent and does not require a sets table.
- Report every built/reused snapshot and its image-identity status. Preserve the existing
  exit contract: usage/preflight failure is 2 and build failure is 1.

## Implementation Decisions

- Build planned arm metadata once and reuse it for `meta.json`, `index.jsonl`, and
  benchmark joins. Keep judge metadata separate because it has a different execution
  environment and fields.
- `index.jsonl` carries only `harness`, `model`, and `effort`; heavier invariant provenance
  stays in `meta.json` and the per-sample provenance record.
- Binder identity is fixed and run-level. `GEMINI_API_KEY` is never recorded.
- Image digest is observation, not cache invalidation. The Phase 7 decision remains:
  evalspec does not become an OCI registry client, and a moved floating tag does not by
  itself invalidate an existing snapshot.
- `benchmark.md` gains no matrix columns. Provenance appears below the matrix.
- `meta.json` moves from v1 to v2 and `benchmark.json` from v2 to v3. Consumers migrate;
  no compatibility shims remain.

## Testing Plan

### Logic

- Planned and observed arms remain distinct: a configured but absent arm appears in
  `arms` and the matrix, but not in `observed_arms`.
- `requested_version=latest` can coexist with a concrete guest `actual_version`.
- Host judge probing and guest task-harness probing use their respective environments.
- Runtime records from multiple samples deduplicate; conflicting snapshot, digest, or
  actual-version values for one arm raise a clear aggregation error.
- Fingerprint fields come from the same helper as `snapshot_name()` and match the backend
  cache key exactly.
- Digest/version failures produce `status=unavailable`, a null value, and a non-empty
  error; no unexplained null is emitted.
- Per-arm `capabilities.token_split` replaces the removed run-level field.
- `index.jsonl` rows receive the correct core axes and no heavy provenance fields.
- Binder identity is exported from binder constants/helper without key material.
- Format versions are exactly meta v2 and benchmark v3.

### Behavior

- A real run records the selected snapshot, backend-native image digest, and guest binary
  version before running the task, then aggregates those exact records.
- Deselection, a skipped arm, or an arm that never creates a sample cannot acquire
  fabricated observed provenance at session finish.
- The resolved set backend drives both preflight and execution; trigger-only behavior
  retains the default backend.
- A two-harness set build creates/reuses two snapshots; two arms on one harness build once.
- Bare `sandbox:build` remains compatible with projects that have no sets table.
- `lint` and `analyze` still import and run without `microsandbox` installed.

### Interface

- Schema tests pin planned/observed semantics, unavailable-state shape, and removal of the
  misleading v1 run-level fields.
- Markdown labels configured-but-unobserved arms as `not observed`.
- Documentation explains that a recorded digest makes floating-tag movement auditable but
  does not make the old snapshot automatically refresh or a run reproducible by itself.

## Out of Scope

- A second sandbox backend or an evalspec-owned registry client.
- New grading behavior, matrix math, or matrix columns.
- Making the binder configurable or per-arm.
- A runner-plugin abstraction.
- Arm-level build-environment overlays; arm `env` remains a runtime overlay.
- Automatically rebuilding snapshots when a floating image tag moves.

## References

- #1 — roadmap umbrella, Done-When #8.
- #37 — backend seam, fingerprint inputs, and backend-native image identity decision.
- `src/evalspec/plugin.py` — current manifest/report aggregation.
- `src/evalspec/report.py` — index and benchmark artifact construction.
- `src/evalspec/backend.py` — backend protocol and fingerprint ownership.
- `src/evalspec/sandbox.py` — snapshot selection and `sandbox:build`.
- `src/evalspec/execution.py` / `src/evalspec/cases.py` — runtime backend threading.
- `src/evalspec/agents/base.py` — requested version, binary probe, and capabilities.
- `src/evalspec/judges/registry.py` — host-side judge version probing.
- `src/evalspec/binder.py` — fixed binder transport identity.

## Verification

- `make test`
- `make lint`
- A real microsandbox fixture run that verifies recorded digest and guest binary version.
- `evalspec sandbox:build --set <two-harness-fixture>` and a same-harness two-arm fixture.
- `evalspec lint` / `evalspec analyze` with `microsandbox` absent.
