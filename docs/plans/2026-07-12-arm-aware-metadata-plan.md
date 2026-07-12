# Implementation Plan — Phase 8: Arm-aware harness/judge/binder/sandbox metadata

Resolves #38. Design spec: [`docs/specs/2026-07-12-arm-aware-metadata.md`](../specs/2026-07-12-arm-aware-metadata.md).

This plan is an ordered implementation decomposition. The spec is authoritative on
*what* and *why*; this plan owns *sequence*, exact file/symbol targets, and the mapping
from the spec's Testing Plan to concrete test files. Ground truth for current symbol
locations was captured from the worktree at plan time (`c3d79ac`); verify before editing.

## Ground rules (carry into every task)

Hard cutover, no compatibility shims. These are the spec's explicit "do NOT shortcut"
items — a task is not done until its unit tests pin them:

1. **Guest probe, not host.** The task-harness `actual_version` is probed *inside the
   selected snapshot* via the backend's guest command seam, **never** `for_host().version()`
   (which is the pinned install selector `latest` and the judge binding). Spec 136–139.
2. **One fingerprint helper.** Fingerprint value + its structured inputs come from a single
   helper shared with `snapshot_name()`. No suffix-parsing of the snapshot name; no
   re-implemented hash in `plugin.py`. Spec 144–146.
3. **Full removal, no aliases.** v1 run-level `agent`, `agent_version`, `token_split` are
   deleted from `meta.json`; a schema test asserts their absence. Spec 93–95.
4. **Never synthesize observed.** `observed_arms` is aggregated only from persisted runtime
   records; absent/deselected/skipped arms get no observed entry. Conflicting records for
   one arm raise a loud aggregation error. Spec 91, 133–135.
5. **Explained unavailable.** unavailable → value is `null` **and** a non-empty `*_error`
   is present. A successful backend lookup must never become an unexplained null. Spec 96–98.
6. **No secrets.** `GEMINI_API_KEY` is never recorded. Binder identity is exported from
   `binder.py` constants/helper, not parsed from the private `_GEMINI_URL`. Spec 101–102, 170.
7. **Exact versions.** `meta.json` `format_version` is exactly `2`; `benchmark.json` is
   exactly `3`.

Baseline at plan time: `make test` = 838 passed; `make lint` green. Because this is a
no-shim cutover, tests break mid-chain and only return to green once the whole chain
lands — update tests alongside each unit, not in a lump.

Microsandbox note: the Python `microsandbox` client is **not** installed in this
environment (only host binaries exist), so the spec's "real microsandbox fixture run"
verification cannot execute here. Unit tests mock the backend and fully cover the logic.
The real-run item is best-effort; if it cannot run, the PR stays draft with a known-gap
note (per resolve-issue Step 8).

---

## Task 1 — Typed runtime-provenance & image-identity records

**Goal:** introduce the shared typed records before any producer or consumer needs them.

**Where:** new module `src/evalspec/provenance.py` (keep `schema.py` as the eval-file
validator; artifact shapes live with the run pipeline). Alternatively colocate in
`report.py` if that reads more naturally to the implementer — decide at implementation,
but one home only.

**Add:**
- `ImageIdentity` (typed): `image_digest: str | None`, `image_digest_status:
  Literal["available","unavailable"]`, `image_digest_error: str | None`. Constructors
  `available(digest)` / `unavailable(error)` that make illegal states unrepresentable
  (available ⇒ non-null digest, null error; unavailable ⇒ null digest, non-empty error).
- `SandboxProvenance` (typed): `backend, snapshot, fingerprint, base_image_ref,
  install_fingerprint, env_script_sha256` + the flattened `ImageIdentity` fields.
- `RuntimeProvenance` (typed): `arm: str`, `actual_version: str | None`,
  `actual_version_status`, `actual_version_error: str | None`, `sandbox: SandboxProvenance`.
  Same available/unavailable invariant for the version fields.
- `to_dict()` producing exactly the `observed_arms[arm]` shape from spec 57–71, and a
  `from_dict()` for reload from `provenance.json`.
- An aggregation helper `aggregate_observed(records: Iterable[RuntimeProvenance]) ->
  dict[str, dict]`: dedupes identical records per arm; raises a clear error on conflicting
  snapshot/digest/actual_version for one arm.

**Tests:** new `tests/test_provenance.py` — invariant enforcement (Ground rule 5),
dedup of identical records, loud conflict error (spec 186–187), `to_dict` shape matches
spec 57–71, round-trip `from_dict(to_dict(x)) == x`.

---

## Task 2 — Backend image-identity method + shared fingerprint helper

**Goal:** the two producers the backend owns.

**Where:** `src/evalspec/backend.py`.

**Add to `SandboxBackend` Protocol (backend.py:40–80):**
- `image_identity(self, snapshot: str) -> ImageIdentity` — returns typed
  available/unavailable, never raises to the caller.
- A structured fingerprint accessor. Refactor the inlined hash in
  `MicrosandboxBackend.cache_fingerprint` (backend.py:120–138) so the raw inputs
  (`backend_id`, `base_image_ref`, `install_fingerprint`, `env_script_sha256`) and the
  8-char digest come from **one** helper — e.g. `fingerprint_inputs(agent, env) ->
  FingerprintInputs` where `FingerprintInputs.digest` is the value `cache_fingerprint`
  returns. `cache_fingerprint` becomes a thin wrapper over it. `snapshot_name()`
  (sandbox.py:33–42) and provenance capture both read from this helper (Ground rule 2).

**Implement in `MicrosandboxBackend` (backend.py:83–269):**
- `image_identity`: read the selected snapshot's native manifest digest; wrap any backend
  exception into `ImageIdentity.unavailable(str(e))` (spec 140–143). Do not abort callers.

**Tests:** `tests/test_backend.py` — `fingerprint_inputs.digest` equals `cache_fingerprint`
for the same args (spec 188–189); `image_identity` returns available on success and
unavailable-with-error when the backend raises (Ground rule 5, spec 190–191). Use fakes;
no real microsandbox.

---

## Task 3 — Guest task-harness version probe

**Goal:** measure the binary *inside* the snapshot.

**Where:** `src/evalspec/agents/base.py` (+ concrete adapters as needed) and the guest seam
`SandboxBackend.guest_shell` (backend.py:70–72).

**Add:** a helper that runs `<agent binary> --version` in the guest via `guest_shell` and
parses the version, returning `(version|None, error|None)`. Explicitly **not**
`for_host().version()` and **not** `binary_version()` (host judge path). Keep the parse
resilient: failure ⇒ `(None, "<why>")` (Ground rule 1/5).

**Tests:** `tests/agents/test_base.py` — guest probe uses the guest seam (assert
`guest_shell` invoked, `for_host` not), success parses a version, failure yields
null+error (spec 185, 190–191).

---

## Task 4 — Capture provenance in `run_eval_arm`

**Goal:** build a `RuntimeProvenance` for the arm's resolved snapshot/runtime and persist it
beside the sample — captured before the task harness produces its result.

**Where:** `src/evalspec/execution.py:296–365` (`run_eval_arm` + `_run_arm_turns`).

> **Placement (validated Codex finding #2).** The guest version probe (Task 3) needs a
> **live** sandbox object, which only exists inside the async `_run_arm_turns` session (the
> `asyncio.run(...)` block at execution.py:344), **not** at the synchronous point right after
> `ensure_snapshot()` (execution.py:334, which yields only a snapshot *name*). Do not probe
> at line 334. Split the capture:

**Change:**
- **Static sandbox identity** — assemble `SandboxProvenance` from the shared fingerprint
  helper (Task 2) around execution.py:332–334 where `agent`/`backend`/`snapshot` are already
  resolved. `backend.image_identity(snapshot)` may be sync or may need the async context
  depending on the backend client; if it needs the live async client, capture it inside the
  session alongside the guest probe. Keep it out of the hot path either way and never let it
  raise (Task 2 guarantees typed unavailable).
- **Guest version probe** — run inside `_run_arm_turns` (execution.py:344–365) against the
  live sandbox via the guest seam (Task 3), before/around the task turns. Return the probe
  result (and any image-identity captured there) up to `run_eval_arm` as part of the
  `arm_run` result, or via a small out-param, so the synchronous body can persist it.
- Assemble `RuntimeProvenance(arm=arm.name, actual_version=…, sandbox=SandboxProvenance(…))`
  in the sync body once the async session returns, then persist as `provenance.json` beside
  the sample's other artifacts (same dir as `grading.json`/`timing.json`) so xdist workers and
  offline analysis retain it (spec 132–135). Persisting after the session returns is fine —
  the *facts* (snapshot, digest, guest version) are all observed before the graded result is
  finalized, satisfying "captured before the task harness runs" in the sense the spec means
  (the environment identity is fixed at session start, not mutated by the run).
- Thread the resolved `sandbox_name` from the caller (Task 5) rather than the hardcoded
  default.

**Tests:** `tests/test_execution.py` — provenance persisted with the sample; the guest probe
runs against the live sandbox (assert the guest seam is used, not `for_host()`); uses the
passed-in backend, not `DEFAULT_SANDBOX`.

---

## Task 5 — Thread the resolved sandbox through preflight & execution

**Goal:** the resolved set's backend drives both preflight and execution; trigger-only
runs keep the default.

**Where:** `src/evalspec/cases.py`, `src/evalspec/sandbox.py:45–52`, `execution.py`.

**Change:**
- Make the resolved `Set` (with `.sandbox`) reach `cases.py`. Currently only the raw CLI
  string `eval_set_name` (cases.py:45–51) is available; resolve the set once and expose its
  `.sandbox` to both the preflight fixture (`_sandbox_preflight`, cases.py:97–100) and
  `test_eval` (cases.py:122–134).
- `_sandbox_preflight` passes the resolved backend to `sandbox.preflight(backend)`
  (sandbox.py:45). No eval set ⇒ default backend path preserved (spec 153–154).
- `test_eval` passes `sandbox_name=<resolved>` into `run_eval_arm` (spec 155–156).

> **Fixture mechanism (validated Codex finding #9).** `_sandbox_preflight` (cases.py:97–100)
> is `scope="session", autouse=True` and takes **no** fixture args today. A session-scoped
> fixture **cannot** depend on the function-scoped `eval_set_name`/`eval_arm` fixtures
> (scope mismatch). Resolve the set from `request.config` directly instead: add a `request`
> param and reuse the exact guard `pytest_generate_tests`/`sessionfinish` already apply
> (`plugin.py:532–538`, mirrored at ~`plugin.py:649`) so a **trigger-only** project with no
> sets table does not force `resolved_run_set()` to parse/raise. Factor that "resolve the run
> set only when eval cases exist" logic into one helper shared by the fixture and
> `sessionfinish` rather than duplicating the guard.

**Tests:** `tests/test_execution.py` / `tests/test_plugin.py` (or `tests/test_cases*`):
resolved backend drives preflight + execution; trigger-only retains default (spec 203).

---

## Task 6 — Build planned-arm metadata once; aggregate observed in `sessionfinish`

**Goal:** single planned-arm builder reused by meta/index/benchmark; observed aggregated
from persisted records, never synthesized.

**Where:** `src/evalspec/plugin.py` (`build_manifest` 411, `_write_manifest` 450–507,
`_judge_meta` 438–447, `pytest_sessionfinish` 510–626).

**Change:**
- Extract one `planned_arms(run_set) -> list[dict]` producing the spec 45–56 arm shape:
  `name, harness, model, effort, env, harness_args, requested_version,
  capabilities.token_split`. `requested_version` = `agent.version()` (the install
  selector); `capabilities.token_split` = `agent.capabilities.token_split` (replaces the
  removed run-level `token_split`, spec 192). Reuse for meta.json arms, the benchmark join
  (Task 7), and index rows (Task 8).
- `build_manifest` / `_write_manifest`: bump `format_version` to `2`; **remove** run-level
  `agent`, `agent_version`, `token_split` (Ground rule 3); add `runner` (from
  `run_set.runner`), `observed_arms`, and `binder` (Task 9). Keep `judge` from `_judge_meta`
  plus the host `actual_version` from `probe_judge_version(judge.harness)` (spec 99–100,
  registry at judges/registry.py:66–79).
- `pytest_sessionfinish`: load every `provenance.json` under the skills root, run
  `aggregate_observed` (Task 1). Absent arms ⇒ no observed entry (Ground rule 4).

> **Ordering (validated Codex finding #5).** `_write_manifest` is called at plugin.py:541,
> **before** the skill-dir walk (plugin.py:582–617) where `provenance.json` records live.
> Aggregate observed provenance **first** and pass the `observed_arms` dict into
> `_write_manifest`/`build_manifest` as a parameter — do not let `meta.json` be written with
> an empty or stale `observed_arms`. Move the aggregation walk (or a dedicated
> `provenance.json`-only walk) ahead of the manifest write.

**Tests:** `tests/test_plugin.py` — meta `format_version == 2`; v1 fields absent
(Ground rule 3); planned includes a configured-but-unrun arm while observed excludes it
(spec 182–183); judge `actual_version` from host path (spec 185); conflicting records
error (spec 186–187); `requested_version=latest` coexists with a concrete observed
`actual_version` (spec 184).

---

## Task 7 — `benchmark.json` v3 + `benchmark.md` Provenance section

**Where:** `src/evalspec/report.py` (`build_benchmark` 391–475, `write_benchmark` 478–501,
`_format_markdown` 318–380).

**Change:**
- Bump `format_version` to `3` (report.py:469). Carry planned arm metadata (from Task 6's
  builder), observed runtime provenance, `runner`, and `binder`. Configured-but-unobserved
  arms stay valid empty matrix columns with runtime provenance absent, not fabricated
  (spec 112–115).
- `_format_markdown`: add a compact **Provenance** section below the matrix (no new matrix
  columns, spec 174). Configured-only arms labeled `not observed` (spec 213).

**Tests:** `tests/test_report.py` — benchmark `format_version == 3`; unobserved arm present
in matrix, absent from observed provenance; markdown labels it `not observed`.

---

## Task 8 — `index.jsonl` core axes

**Where:** `src/evalspec/report.py` `index_rows` (169–207); writer in plugin.py:615–617.

**Change:** each row gains `harness`, `model`, `effort` (from the planned-arm builder,
keyed by arm name) — and nothing heavier (spec 168–169, 193). Heavy provenance stays in
meta.json / `provenance.json`.

**Tests:** `tests/test_report.py` / `tests/test_plugin.py` — rows carry the three axes and
no provenance fields.

---

## Task 9 — Binder identity export

**Where:** `src/evalspec/binder.py`.

**Change:** add an exported `binder_identity() -> dict` (or module constants) returning
`{provider: "gemini", model: GEMINI_BINDER_MODEL, api_path:
"generativelanguage.googleapis.com/v1beta"}`. Derive `api_path` from a **named constant**,
not by parsing `_GEMINI_URL`. Never touch `GEMINI_API_KEY` (Ground rule 6). Consumed by
Tasks 6 and 7.

**Tests:** `tests/test_binder.py` — identity exported without key material; matches spec
82–86 (spec 194).

---

## Task 10 — `sandbox:build --set` builds once per distinct harness

**Where:** `src/evalspec/sandbox.py` `cli_build` (506–539); `src/evalspec/__main__.py`
`_run_sandbox_build` (67–108).

**Change:**
- With `--set`/`--config`: resolve the set and build once per **distinct** harness via
  `make_agent(harness)` (spec 157–159). Bare `sandbox:build` still builds only the default
  agent and needs no sets table (spec 158–159, 206).
- Report every built/reused snapshot and its `image_identity` status (spec 160).
- Preserve exit contract: usage/preflight failure = `2`, build failure = `1`
  (`__main__.py:94–105`, spec 161).

**Tests:** `tests/test_sandbox.py` / `tests/test_main.py` — two-harness set builds two
snapshots; two arms on one harness build once (spec 205); bare path works with no sets
table (spec 206); exit codes preserved.

---

## Task 11 — Docs

**Where:** `README.md` / `docs/` artifact reference.

**Change:** document meta v2 / benchmark v3 shapes (planned vs observed), the
unavailable-state shape, and that a recorded digest makes floating-tag movement auditable
but does **not** auto-refresh the snapshot or make a run reproducible by itself (spec 214–215).

**Tests:** `tests/test_readme_examples.py` if README carries executable/JSON examples.

---

## Verification (resolve-issue Step 8)

1. `make test` — green (unit tests fully cover the logic; backends mocked).
2. `make lint` — green.
3. `evalspec lint` / `evalspec analyze` import & run with `microsandbox` absent
   (spec 207) — runnable here. The existing
   `tests/test_backend.py::test_microsandbox_imported_only_under_allowlist` enforces this
   by grepping `src/evalspec/*.py` for `microsandbox` imports outside an allowlist. Keep new
   types in the fresh `provenance.py` (no microsandbox import); `image_identity` lands in the
   already-allowlisted `backend.py`. Re-run this test after Task 2.
4. `sandbox:build --set <two-harness-fixture>` and a same-harness two-arm fixture — via
   unit tests with fake backends (spec 205).
5. Real microsandbox fixture run recording digest + guest version (spec 199, 243) —
   **best-effort; likely unattainable here** (no `microsandbox` client). If it cannot run,
   note as a known gap and keep the PR draft rather than claim it passed.

## Suggested commit sequence

One commit per task, tests alongside. Order 1→11 minimizes the red window: records →
producers → guest probe → capture → threading → aggregation → benchmark → index → binder →
build → docs.
