**TL;DR** — Make run artifacts self-describe *what actually ran*: extend `meta.json` (→ `format_version: 2`) so its arm roster carries each arm's probed harness version and resolved sandbox identity (backend, runner, snapshot name, fingerprint + its inputs, and the backend-native **actual** pulled image digest) plus a run-level `binder` object; denormalize the three core arm axes (`harness`/`model`/`effort`) onto `index.jsonl` rows; surface the same provenance in `benchmark.json` (→ `format_version: 3`). Thread the resolved `Set.sandbox` from config down into `run_eval_arm`/preflight (execution is still hardcoded to microsandbox), add a `resolved_image()` method to the `SandboxBackend` interface so the backend reports the digest *it* pulled, and make `evalspec sandbox:build --set` build one snapshot **per distinct harness** in the set. Phase 8 of the #1 roadmap — it records identity; it changes no grading or matrix math.

## Problem

Phases 1–7 each shipped working machinery and deliberately deferred recording its identity into artifacts. Phase 8 collects those debts. Concretely, today:

- **Sandbox identity appears in no artifact.** Phase 7 folded backend id + declared base-image reference + agent install fingerprint + env-script bytes into the snapshot **cache key** (`backend.py:120-138`; `snapshot_name` at `sandbox.py:33-42`), but none of it is written anywhere. A report reader cannot see which snapshot a run used, or *why* a snapshot was or wasn't reused (the fingerprint inputs are invisible). This is the same interim gap the binder identity has, called out in the Phase 7 hand-off (#37).
- **A floating base-image tag is not reproducible and leaves no trail.** The cache fingerprint hashes the **declared** reference string (e.g. `"ubuntu:latest"`), not content — a decision locked in PR #37 (evalspec is not an OCI registry client). So an upstream move of `latest` does not invalidate the snapshot, *and* nothing records which actual image the run ran on. microsandbox already knows — it exposes `Snapshot.image_manifest_digest` / `ImageHandle.manifest_digest` — but evalspec never reads it.
- **`meta.json` is not arm-aware for versions.** `agent_version` names only the **default** harness even for a multi-harness set (`plugin.py:464-466,470-474`); the per-arm roster (`plugin.py:485-492`) records `harness`/`model`/`effort`/`env`/`harness_args` but **no per-arm probed version**. `Arm` has no version field (`arms.py:23-32`).
- **The binder's identity is recorded nowhere.** Production always binds through Gemini `gemini-3.1-flash-lite` (`binder.py:18`) over `generativelanguage…/v1beta` (`binder.py:20`), but `meta.json`/`index.jsonl` carry no binder model or API-path field — only a `binder_degraded` count leaks into per-sample `grading.json`. Artifact consumers cannot distinguish binder models across runs (flagged in the Phase 2 hand-off).
- **`index.jsonl` rows are opaque.** One row per (skill, eval, arm, sample) carrying only the `arm` **name** string (`report.py:190-205`) — a consumer must join against `meta.json` to learn even the harness/model the row ran under.
- **The resolved `Set.sandbox` never reaches execution.** `run_eval_arm` takes `sandbox_name: str = DEFAULT_SANDBOX` (`execution.py:307`) and the sole caller (`cases.py:122-134`) does not pass it — it re-derives the microsandbox default per arm (`execution.py:332-334`). Safe *today* only because every non-microsandbox set fails at `parse_sets`; the resolved backend on `Set` is validated and then dropped.
- **`sandbox:build --set` is single-agent.** Phase 7 threaded `--set`/`--config` into `cli_build` (`sandbox.py:506-539`) but still builds one snapshot for the repo's **default** agent (`make_agent()`, `sandbox.py:531`); a multi-harness set's non-default harnesses get no prebuilt snapshot.

- **Exposed by:** Roadmap #1 Done-When #8 — *"`meta.json` and index rows are arm-aware and include resolved harness, model, effort, env, judge config, binder model/API path, sandbox backend, and relevant versions/digests"* — plus the four concrete carry-overs in the Phase 7 hand-off comment.
- **Scope:** Recording/threading only. Extend the three run artifacts with the missing provenance; add one backend method for the actual digest; thread the resolved backend into execution/preflight; make `sandbox:build --set` build per distinct harness. **Not** new grading, new matrix math, or a second sandbox backend.
- **Constraint:** No compatibility shims (roadmap rule). `meta.json` bumps to `format_version: 2` and `benchmark.json` to `3`; consumers move to the new shape. The Phase 4 **3.11 floor** and the Phase 5/7 **lazy-`microsandbox`-import** invariant hold — `lint`/`analyze` must still run with the package absent, so any digest/backend read happens only on a path that already imported the backend for a real run.

## Solution

`meta.json` (`format_version: 2`) — the arm roster gains `harness_version` and a `sandbox` block; a run-level `binder` object and `runner` field appear (judge unchanged from Phase 1):

```jsonc
{
  "format_version": 2,
  "run_id": "…", "commit": "…", "config_hash": "…",
  "agent": "claude-code", "agent_version": "1.2.3",   // still the default harness
  "set": "default",
  "runner": "pytest",                                 // NEW — resolved Set.runner
  "arms": [
    {
      "name": "opus", "harness": "claude-code", "model": "opus-4.8",
      "effort": "medium", "env": {…}, "harness_args": [],
      "harness_version": "1.2.3",                     // NEW — per-arm probe
      "sandbox": {                                    // NEW — resolved snapshot identity
        "backend": "microsandbox",
        "snapshot": "evalspec-microsandbox-claude-code-1.2.3-ab12cd34",
        "fingerprint": "ab12cd34",
        "base_image_ref": "ubuntu:latest",            // declared (what was fingerprinted)
        "install_fingerprint": "…",
        "env_script_sha": "…",
        "image_digest": "sha256:…"                    // NEW — actual pulled digest (backend-native)
      }
    }
  ],
  "judge": { "harness": "…", "model": "…", "effort": "…", "timeout": …, "env": {…}, "harness_args": [] },
  "binder": {                                         // NEW — fixed Gemini identity
    "provider": "gemini",
    "model": "gemini-3.1-flash-lite",
    "api_path": "generativelanguage.googleapis.com/v1beta"
  }
}
```

`index.jsonl` — the three core arm axes denormalized onto each per-sample row (heavier provenance stays in `meta.json`, joined by `arm`):

```jsonc
{"skill":"foo","kind":"eval","eval_id":"x","arm":"opus",
 "harness":"claude-code","model":"opus-4.8","effort":"medium",
 "sample":0,"errored":false,"passed":3,"total":3, …timings…}
```

`sandbox:build --set` builds one snapshot **per distinct harness** in the resolved set (model/effort/harness_args are runtime-only and not in the fingerprint, so arms sharing a harness share a snapshot):

```text
$ evalspec sandbox:build --set default      # arms: opus(claude), sonnet(claude), gpt(codex)
  built evalspec-microsandbox-claude-code-1.2.3-<fp>   (image sha256:… )
  built evalspec-microsandbox-codex-0.9.1-<fp>         (image sha256:… )
```

## User Stories

1. As a benchmark maintainer, I want **each arm's exact harness version, sandbox snapshot, and pulled image digest in `meta.json`**, so a result set records the environment it was produced in and I can reproduce or audit it later.
2. As an artifact consumer, I want **`index.jsonl` rows to name the harness/model/effort they ran under**, so I can group per-sample results without joining every row back to `meta.json`.
3. As a maintainer debugging a stale snapshot, I want **the fingerprint and its inputs recorded**, so I can see *why* a snapshot was or wasn't reused (backend, declared base image, install fingerprint, env-script hash).
4. As someone comparing runs over time, I want **the actual pulled image digest recorded even when the set declares a floating tag**, so `ubuntu:latest` moving upstream is visible in the artifacts rather than silent.
5. As a run operator, I want **`evalspec sandbox:build --set` to prebuild every harness in the set**, so a multi-harness run doesn't cold-build a non-default harness's snapshot mid-run.
6. As a report reader, I want **the binder model/API path recorded**, so I can tell which binder graded a run when comparing across time.

## Implementation Decisions

```text
resolved Set (arms[], runner, sandbox)      [arms.py]
        │
        ├─► execution: run_eval_arm(sandbox_name=Set.sandbox)   ← thread, don't hardcode
        │        cases.py passes eval_set.sandbox               [execution.py:307,326-334; cases.py:122-134]
        │
        └─► run-finish provenance collection                    [plugin.py pytest_sessionfinish]
                per distinct harness in run_set.arms:
                  agent   = make_agent(harness)
                  backend = resolve_sandbox(run_set.sandbox)
                  name    = snapshot_name(agent, env, backend=backend)
                  digest  = backend.resolved_image(name)   ← NEW backend method
                → arm.sandbox = {backend, snapshot, fingerprint, inputs…, image_digest}
                → arm.harness_version = agent.for_host().version()   (probe, deduped by harness)
                → meta.arms[], index rows (core axes), benchmark per-arm stats
```

- **Extend the resolved-arm record in ONE place, then fan out.** The `{name, harness, model, effort, env, harness_args}` literal is materialized in three near-identical spots — `meta.json`'s `arms[]` (`plugin.py:485-492`), the `arm_meta` join map (`plugin.py:550-563`), and (as the `_judge_meta` variant) `plugin.py:438-447`. Introduce a single helper (e.g. `_arm_meta(arm, *, version, sandbox)` in `plugin.py`) that builds the enriched per-arm dict once, and have both `meta.json` and the `arm_meta` map consume it, so the roster shape can't drift. `harness_version` and the `sandbox` block are added there.
- **Probe per-arm harness versions, deduped by harness.** Today only the default harness is probed (`plugin.py:472-474`). At run-finish, for each **distinct** `arm.harness` in `run_set.arms`, resolve `make_agent(harness).for_host()` and call `version()` once, memoized by harness name; stamp the result on every arm with that harness. Keep the existing best-effort guard (`plugin.py:475-478`): a probe failure leaves `harness_version: null`, never aborts artifact writing. (Probing is offline/host-side and cheap; `for_host()` is the Phase 1 judge/preflight binding.)
- **Record resolved sandbox identity per arm, computed at run-finish.** For each distinct harness: `backend = resolve_sandbox(run_set.sandbox)`, `name = snapshot_name(agent, env, backend=backend)` (`sandbox.py:33-42`), and read the fingerprint inputs the backend already hashes (`backend.py:120-138`) — backend id, **declared** `base_image_ref`, `agent.install_fingerprint()`, and an `env_script_sha` over `env.script`. Surface `fingerprint` (the 8-char suffix) and `snapshot` (the full name) too. `env` is the run's resolved `EnvConfig` (`resolve_environment_config(repo_root)`), the same one execution builds against — sandbox identity is therefore per-harness, and arms sharing a harness share it.
- **Add `resolved_image(name) -> str | None` to the `SandboxBackend` interface for the actual digest.** New method on the protocol (`backend.py:40-80`) reporting the backend-native manifest digest of a built/reused snapshot. `MicrosandboxBackend` implements it via `Snapshot.get(name).image_manifest_digest` (confirmed in microsandbox 0.5.10 — no upstream work, no extra dependency). Called only at run-finish for a run that executed arms, so the lazy-import invariant holds (`lint`/`analyze` never reach it). Returns `None` (and `meta` records null) if the snapshot handle or digest is unavailable — best-effort, like the version probe. This closes the deferred half of the PR #37 decision: evalspec still does not resolve digests itself; the backend that pulled the image reports what it got.
- **Thread the resolved `Set.sandbox` into execution and preflight.** `run_eval_arm` keeps `sandbox_name` keyword-only but the sole caller `cases.py:122-134` now passes `eval_set.sandbox` (it already holds the resolved `EvalSet`). Preflight (`sandbox.py` preflight path) likewise resolves the set's backend rather than the hardcoded default. Behavior is unchanged while microsandbox is the only backend, but the resolved value now actually drives the run instead of being dropped — prerequisite for any future backend and for recording the *used* backend honestly.
- **Denormalize core axes onto `index.jsonl`; nothing heavier.** `index_rows` (`report.py:169-207`) sees only `arm_dir.name` from disk, so thread the existing `arm_meta` map (`plugin.py:550-563`) in as a parameter and add `harness`/`model`/`effort` to each row (`report.py:190-205`), keyed off the arm name. Sandbox digest, binder identity, and `env` stay in `meta.json` (identical across an arm's samples — copying them per row is pure bloat). `index.jsonl` stays unversioned (added keys are additive), consistent with today.
- **Surface the same provenance in `benchmark.json` (`format_version: 2 → 3`).** The per-arm stats dict already joins `{harness, model, effort, env, harness_args}` by arm name (`report.py:434-441`) — add `harness_version` and the `sandbox` block there from the same enriched `arm_meta`, and add run-level `runner` + `binder` to the top-level return (`report.py:468-475`). Bump `format_version` to `3` (Phase 6 pinned this as the trigger). `benchmark.md` gets a short **Provenance** section (backend, per-harness snapshot + image digest, binder model) rather than new **matrix columns** — the matrix stays `rate (+Npp)` and keeps rendering harness in the header (`report.py:286`); cramming digests into cells would wreck it. All rendering flows through `_rate_cell`/`_format_markdown` (`report.py:326-360`) as before.
- **Record the binder identity as a fixed run-level object.** Add a `binder` block to `meta.json`/`benchmark.json` sourced from the `binder.py` constants — `provider: "gemini"`, `model: GEMINI_BINDER_MODEL` (`binder.py:18`), `api_path` derived from `_GEMINI_URL`'s host+version (`binder.py:20`). It is **run-level, not per-arm** (one fixed binder grades every arm) and **not configurable** — mirroring the Phase 2 decision that production never reads an env-var binder model. `GEMINI_API_KEY` value is never recorded.
- **`sandbox:build --set` builds per distinct harness.** `cli_build` (`sandbox.py:506-539`) currently builds one `make_agent()` default. When a set is resolved, iterate its arms' **distinct harnesses** and build one snapshot each via the existing `ensure_snapshot`/`build_snapshot` path (`make_agent(harness)` per harness), using the resolved backend and the repo/set env. Deduping by harness matches the fingerprint (agent + backend + base-image + env; model/effort are runtime-only), so arms sharing a harness resolve to one snapshot name and build once. Reuse the Phase 5 `_run_sandbox_build` wrapper and its exit-code split (`RuntimeError`→2 / `MicrosandboxError`→1); report each built/reused snapshot name + image digest.

## Testing Plan

### Logic
- **Enriched arm roster is built once and consistent** — the single arm-meta helper produces the same `{…, harness_version, sandbox}` shape consumed by both `meta.json`'s `arms[]` and the `arm_meta` join map; a version-probe failure yields `harness_version: null` without aborting the manifest.
- **Sandbox identity is complete and correct** — for a resolved arm, `sandbox.snapshot` equals `snapshot_name(agent, env, backend=backend)`, `fingerprint` is its 8-char suffix, and `base_image_ref`/`install_fingerprint`/`env_script_sha` match the fingerprint inputs the backend hashed; distinct harnesses get distinct snapshots, arms sharing a harness share one.
- **Per-harness version dedup** — a two-arm set on one harness probes the harness version exactly once and stamps both arms; a two-harness set probes twice.
- **`index.jsonl` core axes** — each row carries `harness`/`model`/`effort` matching its arm's resolved config, keyed by arm name, and no sandbox/binder/env keys.
- **`resolved_image`** — `MicrosandboxBackend.resolved_image(name)` returns the manifest digest for a built snapshot and `None` for an absent/undigestable one; `meta.json` records the value or null.
- **Binder identity is fixed** — the `binder` block reports `gemini` / `gemini-3.1-flash-lite` / the v1beta path from the constants, with no key material and no env-var read.
- **Format versions bump** — `meta.json` is `2`, `benchmark.json` is `3`; the `meta.json`-`1` and `benchmark`-`2` pins are updated, not duplicated.

### Behavior
- **A real run records provenance end to end** — a microsandbox run writes `meta.json` with per-arm `harness_version` + `sandbox.image_digest`, `index.jsonl` rows with core axes, and a `benchmark.md` Provenance section; the digest matches what the backend pulled.
- **Resolved backend drives execution** — `run_eval_arm` receives the set's `sandbox` from `cases.py` (not the hardcoded default); a run under the microsandbox set behaves identically to today, proving the threading is inert for the only backend while no longer dropping the value.
- **`sandbox:build --set` is multi-harness** — a two-harness fixture set builds two snapshots (one per distinct harness) and reports both names + digests; a set with two arms on one harness builds once.
- **Lazy-import invariant holds** — `lint`/`analyze` run with `microsandbox` absent; the digest/backend reads are never reached off the run path.

### Interface
- **Artifact schema is stable and documented** — `meta.json` (v2), `index.jsonl`, and `benchmark.json` (v3) expose the fields the README vision promises; a schema test pins the new keys.
- **`sandbox:build --set` exit contract is unchanged** — preflight/usage failure exits `2`, build failure exits `1`, via the Phase 5 `_run_sandbox_build` split; only the number of snapshots built changes.

## Open Questions

- **`index.jsonl` depth — resolved: core axes only.** Denormalize `harness`/`model`/`effort` onto per-sample rows; keep sandbox digest, binder identity, and `env` in `meta.json` (join by arm name). Satisfies the Done-When wording without copying invariant bytes across thousands of rows.
- **`sandbox:build --set` granularity — resolved: per distinct harness.** The snapshot fingerprint keys on agent + backend + base-image + env; model/effort/harness_args are runtime-only, so per-arm building would produce byte-identical redundant snapshots. Dedupe to distinct harnesses.
- **Actual image digest — resolved: record now.** Add `resolved_image()` to the backend interface and read microsandbox's `Snapshot.image_manifest_digest` at run-finish. Closes the floating-tag gap identified in PR #37; the only remaining piece of that decision.
- **"Capability flags" (roadmap user story 4) — scoped out.** No concrete per-arm capability flag exists in the codebase today (`token_split` is a run-level agent capability already recorded). Add named capability fields only when a concrete capability needs recording; this phase records versions/identity, not speculative flags.
- **Judge harness version — included.** Add `harness_version` to the `judge` object too (the judge runs a host-side harness binary via `for_host()`), since "relevant versions" covers the grader. Cheap and consistent; if it complicates the judge-preflight ordering, drop to a follow-up.

## Documentation Plan

- **`docs/schema.md`** (or the artifact-schema section): document `meta.json` v2 (`runner`, per-arm `harness_version` + `sandbox` block, run-level `binder`), the `index.jsonl` core-axis columns, and `benchmark.json` v3 + the Provenance section.
- **`docs/configuration.md`**: note that the sandbox backend, snapshot identity, and actual image digest are now recorded per arm; reiterate the floating-tag caveat (declared ref is fingerprinted, actual digest is recorded) and that pinning a digest in `base_image` gives strict reproducibility.
- **`docs/agents.md`**: document the new `SandboxBackend.resolved_image()` method on the adapter interface and the per-arm host-side version probe.
- **`docs/concepts.md`** / **`README.md`**: fold the arm-aware artifact fields into the artifact-contract description.

## Out of Scope

- Any second sandbox backend, or resolving image digests via a registry client (skopeo) — the backend reports the digest it pulled; the PR #37 decision stands.
- New grading behavior, new matrix math, or new matrix **columns** — provenance goes to a `benchmark.md` Provenance section and the JSON, not into rate cells.
- Making the binder configurable or per-arm — binder stays the fixed run-level Gemini identity (Phase 2 decision); only its identity is recorded.
- A runner-plugin abstraction — `runner` is recorded config only (one runner, `pytest`).
- Arm-level `env`-varying snapshots — snapshot env is the run-level resolved `EnvConfig`, as today; per-arm env is a runtime overlay, not a build input.
- The Phase 4 3.11-floor doc drift in `README.md`/`docs/quickstart.md` (fold into the Phase 9 docs pass).

## References

- #1 — roadmap umbrella; Done-When #8 and the Phase 7 hand-off comment (the four concrete Phase 8 carry-overs).
- #37 (Phase 7) — `SandboxBackend` seam, the cache fingerprint, and the "evalspec is not an OCI registry client / backend records the actual digest in Phase 8" decision.
- `src/evalspec/plugin.py:411-435,438-447,450-507,550-563,615-617` — `build_manifest`, `_judge_meta`, `_write_manifest` (arm roster, default-only `agent_version`, version probe), the `arm_meta` map, and `index.jsonl` write.
- `src/evalspec/report.py:169-207,326-360,391-475,478-501` — `index_rows`, `_format_markdown`, `build_benchmark` (arm-fact join at `434-441`, return at `468-475`), `write_benchmark`.
- `src/evalspec/backend.py:40-80,83-269,120-138,164-187` — `SandboxBackend` protocol (where `resolved_image` is added), `MicrosandboxBackend`, `cache_fingerprint` inputs, the build path whose snapshot the digest is read from.
- `src/evalspec/sandbox.py:33-42,506-539` — `snapshot_name`, `cli_build` (single-agent build to make per-harness).
- `src/evalspec/execution.py:296-334` — `run_eval_arm` (`sandbox_name` default + the Phase 8 threading comment); `src/evalspec/cases.py:122-134` — the sole caller to pass `eval_set.sandbox`.
- `src/evalspec/arms.py:23-32,44-52` — `Arm` (no version field) and `Set` (`runner`/`sandbox` to record).
- `src/evalspec/binder.py:18,20,200-255` — `GEMINI_BINDER_MODEL`, `_GEMINI_URL`, and the fixed-model contract the `binder` block records.
- microsandbox 0.5.10 — `Snapshot.image_manifest_digest` / `Snapshot.get` / `ImageHandle.manifest_digest` (backend-native digest source).

## Verification

- `make test` — proves the enriched roster helper, per-harness version dedup, sandbox-identity computation, `resolved_image`, `index.jsonl` core axes, binder block, format-version bumps, and the execution threading pass.
- `make lint` — proves package + docs satisfy lint, including the lazy-`microsandbox`-import boundary test.
- `evalspec run --set <microsandbox-fixture>` — writes `meta.json` v2 with per-arm `harness_version` + `sandbox.image_digest`, `index.jsonl` rows carrying `harness`/`model`/`effort`, and a `benchmark.md` Provenance section.
- `evalspec sandbox:build --set <two-harness-fixture>` — builds one snapshot per distinct harness and reports each name + pulled digest; a same-harness two-arm set builds once.
- `evalspec lint <fixture>` / `evalspec analyze <fixture>` with `microsandbox` uninstalled — still exit cleanly, proving no digest/backend read escaped onto the non-run path.
