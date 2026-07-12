# Schema reference

Evals are authored in **Markdown**.

```
<search-path>/…/<group>/eval.md          # id = folder name
<search-path>/…/<group>/<stem>.eval.md   # id = file stem (siblings share the folder)
<search-path>/…/<group>/workspace/       # optional starting files, copied into /workspace
<search-path>/…/<group>/setup.sh         # optional per-eval sandbox setup
```

Discovery walks a configured list of **search paths** (`eval_paths`, default `skills`, `tests`, `evals`, `benchmarks`) under the repo root. Any `eval.md` / `*.eval.md` file anywhere beneath a search path is an eval — the filename is the marker, so no `evals/` ancestor is required, and `group` is the eval file's parent folder. Set your own with `[tool.evalspec] eval_paths` or `--evalspec-eval-paths` — this is the sole discovery knob. The walk does not follow symlinks (a symlinked group or eval file is skipped, not an error), treats an embedded Git repo as a boundary, and prunes `tmp/`, `.git`, `__pycache__`, and every dot-prefixed directory *within* a search path (a dot-prefixed path you configure as a search path, e.g. `.claude/skills`, is still walked). A `workspace/` beside an eval file is that eval's sandbox seed and is never descended into.

Nothing under a path outside `eval_paths` is discovered — to place evals under `.claude/skills/` (or any other non-default location), add that path to `eval_paths`.

Case identity is the pair `(group, eval_id)`. For `eval.md`, `group` and `eval_id` are both the parent folder's name. For `<stem>.eval.md`, `group` is the parent folder's name and `eval_id` is the file stem — so several `<stem>.eval.md` files can share one folder. The full test id is `<group>-<eval_id>-<arm>`; artifact paths read `<group>/eval-<eval_id>/`. A duplicate `(group, eval_id)` pair fails loudly at collection, naming both source files.

There is no `skill_name` frontmatter and no suite header — group identity comes entirely from the folder layout above.

`mdformat.py` owns the Markdown structure, `schema.py` owns validation. Both are strict and loud: unknown headings, plain `-` bullets, indented non-checkbox lines, prose outside known sections, grandchild or ragged assertion nesting, and unknown frontmatter keys all fail at pytest collection with the offending path quoted.

---

## Output evals — the `eval.md` / `*.eval.md` format

One file per eval, named `eval.md` or `<stem>.eval.md`. The eval id is the parent folder name for `eval.md`, or the file stem for `<stem>.eval.md` (both kebab-case); it appears in test ids (the full id is `<group>-<eval_id>-<arm>`) and artifact paths (`<group>/eval-<eval_id>/`). Optional YAML frontmatter carries `history:` only, then a required `## Prompt` and a required `## Assertions` checklist.

```markdown
---
history:
- role: user
  content: I dropped a clipping in ./Inbox earlier.
- role: assistant
  content: Got it — say the word and I'll file it.
---

## Prompt

You are working in a vault rooted at your current working directory. Read ./Inbox/clipping.md and archive it.

## Assertions

- [ ] the archived file lives at ./9. Archive/Sources/{TODAY}/clipping.md
- [ ] the source no longer exists at ./Inbox/clipping.md
- [ ] Skill `archive` invoked

### Wiki page (display-only group)

- [ ] a wiki page was written under 1. Wiki/ that cites the archived source
```

A compound assertion can be decomposed into per-clause atoms — the parent is a display-only header, each child is graded on its own:

```markdown
## Assertions

- [ ] the vault was scaffolded:
  - [ ] the 0. Inbox/ directory exists
  - [ ] the 1. Profile/ directory exists
  - [ ] ./.meta/index.md opens with '# Index'
```

Children stay plain prose; the binder derives each child's checker exactly as for a top-level line.

| Part | Required | Notes |
|---|---|---|
| `history` (frontmatter) | no | A list of `{role, content}` turns rendered as a transcript prefix before the graded prompt — prior context. See [History](#history). The **only** frontmatter key. |
| `## Prompt` | yes | The agent instruction. `{TODAY}` is substituted; paths are `./`-relative to the working directory. |
| `## Assertions` | yes | A `- [ ]` checklist; each line's text (after the checkbox) is plain **prose**. Non-empty. A top-level item with indented `- [ ]` children is a display-only header (never graded) whose children flatten to standalone assertions in document order — one nesting level only. H3 subheadings are display-only groups, also flattened in document order; no semantics ride on the H3 title. |

There is no `checks:` field, no `skill_name`, and no `background.md`. Every assertion is plain prose; the **binder** derives the deterministic check at grade time (see [Assertions](#assertions) and `concepts.md`). A `- [ ]` item with indented `- [ ]` children decomposes into one graded atom per child, in document order; the parent line is a display-only header. One nesting level only — a grandchild (any indent deeper than the first child), a ragged child indent, an indented non-checkbox line, an orphan child with no parent, and a plain `-` bullet without a checkbox are all hard errors. A parent and its children must share one `## Assertions` body or one `###` group.

`workspace/` (a sibling of the eval file under the group dir) holds the eval's starting files; its contents are copied into the per-cell clean room mounted at `/workspace` before the prompt runs. No `workspace/` ⇒ an empty workdir.

`setup.sh` (also a sibling under the group dir) is this eval's own sandbox setup script — it installs the skill under test, branching on `$EVALSPEC_ARM`, and runs with `cwd` at the group dir. It is per-eval, replacing the old suite-level `evals/setup.sh`; see `configuration.md` / `agents.md`.

### History

`history:` is an optional list of `{role, content}` turns. It renders into a `<transcript>…</transcript>` block prepended to the graded prompt — render-into-prompt, so prior context works on any agent without session injection. Only the final (graded) prompt is graded; the history is context.

```yaml
history:
- role: user
  content: Here's the deadline — Friday.
- role: assistant
  content: Noted.
```

Each turn needs a non-empty `role` and `content`. `{TODAY}` is substituted in `content`; any other `{UPPERCASE}` placeholder is rejected, symmetric with the graded prompt, so a stray token fails the eval rather than leaking to the agent. A malformed history (non-list, missing/empty field) fails at collection.

### Assertions

Every `## Assertions` entry is plain prose. The **binder** classifies each one at grade time: when confident, it maps the prose to a deterministic **checker** (`file_exists`, `glob_count`, `sha256_match`, `frontmatter_has`, `regex`, `skill_invoked`, plus the negative matchers `not_file_exists` and `not_skill_invoked`) run on the host against the final workdir or process facts; otherwise it **punts** to the LLM judge. Authors write no checker syntax — there is none to learn. The split is invisible from the suite. See `concepts.md` for the binder's contract.

One family of assertions is special by convention: **activation**. Write it in one of two polarities:

```markdown
- [ ] Skill `archive` invoked
- [ ] Skill `codex` not invoked
```

The binder maps each polarity to its own checker, both grading against the arm's dispatched-skills set:

- `` Skill `X` invoked `` → `skill_invoked`. Passes when `X` is in the dispatched set.
- `` Skill `X` not invoked `` → `not_skill_invoked`. A **membership-absence** check — passes when `X` is *not* in the dispatched set.

Negation is always a distinct checker name (`not_skill_invoked`, `not_file_exists`), never a polarity flag — the spec reads unambiguously and the binder picks an explicit primitive. More negative matchers get added as evals need them.

The skill token may be plain or backticked and may be namespaced (`plugin:ingest`); a fired `plugin:ingest` satisfies an assertion written against `ingest` (exact-or-namespaced match). Both are ordinary prose assertions graded symmetrically across arms — there is no separate invocation gate. On a trial arm (skill installed) the positive form passes and the negative form of that same skill fails; on a baseline arm (no skill) the positive form fails and the negative form passes.

> **On the vision's `Capability … activated` wording.** The [readme vision](research/evalspec-readme-vision.md) phrases these as `` Capability `X` activated `` / `` Capability `X` not activated ``. That is documentation-level wording for the *same* shipped checker — the recognizer that binds today matches `` Skill `X` invoked `` / `` not invoked ``. Read "capability activated" and "skill invoked" as the same process-fact assertion.

**A `skill_invoked` / `not_skill_invoked` assertion is an ordinary deterministic checker.** It records `type: deterministic` in `grading.json` — the same family as `file_exists` or `regex` — and `evalspec analyze` labels it `deterministic` too. What sets it apart is only its *evidence*: a process fact (which skills the arm dispatched, via `GradeContext`) rather than the workdir. There is no separate `activation` type or analyze label — it grades and reports exactly like any other deterministic checker; a punt analyzes as `judge-backed`.

### Placeholder substitution

Applied before the agent sees the content.

| Placeholder | Substituted as | Where |
|---|---|---|
| `{TODAY}` | ISO `YYYY-MM-DD` from the host clock (UTC, for guest agreement) | Prompt, assertions, history content, workspace files, workspace filenames |

Paths are `./`-relative: the agent runs with its current working directory set to the workdir mount (`/workspace`), so `./Inbox/x` resolves there. Hidden-directory assertions preserve the hidden path component, for example `./.meta/templates/entity-person.md exists` binds to that full relative path. Any `{UPPERCASE_PLACEHOLDER}` other than `{TODAY}` in a prompt or history turn fails fast — eval prompts must be spec-free to keep baselines honest. Put the value in the workspace.

---

## `evalspec lint`

`evalspec lint` (`make evals:lint`, or `python -m evalspec lint [root]`) statically scans every output suite for assertions the judge can't fairly grade. The judge grades only from supplied evidence — workdir facts, SHAs, the agent's final message, process facts — so an assertion the evidence can't decide gets graded by vibes, and pass rates drift without the skill changing.

Each rule is a heuristic with an honest level:

| Rule | Level | Fires on |
|---|---|---|
| `vague-adverb` | warning | adverbs that name no observable criterion (`explicitly`, `appropriately`, `properly`, `gracefully`, …) |
| `unseen-file` | warning | a file path with no `./` anchor — the judge never sees files outside the workdir facts |
| `relative-claim` | warning | a comparative (`better`, `cleaner`, `improved`, …) with no comparand |

**Any warning-level finding exits 1.** Run it before each eval session, the way you'd run a linter before a commit.

---

## Validation rules

- Both `group` and `eval_id` (the eval's folder name and id) must match `^[a-z0-9]+(-[a-z0-9]+)*$` (kebab-case).
- Assertion strings must be non-empty after stripping whitespace; `## Assertions` itself must be non-empty.
- A history turn needs a non-empty `role` and `content`; `history` must be a list.
- The only frontmatter key is `history`. Unknown frontmatter keys, top-level keys, or per-item fields are rejected. Add functionality via a new `$schema` version or a documented field, not by sneaking one in.
- A duplicate `(group, eval_id)` pair fails loudly at collection, naming both source files. Per-eval `setup.sh` replaces the old suite-level `evals/setup.sh` script.

Authoritative validator: `evalspec.schema` (plus `evalspec.mdformat` for Markdown structure). When this document drifts, the code wins.

---

## `meta.json` — derived artifact

The run manifest, written once per run at the **iteration root** — `tmp/evals/iteration_NN/meta.json`, beside `index.jsonl` and `benchmark.json`. It is a derived artifact assembled from resolved config plus captured runtime provenance, not an input schema and not validated at collection.

`meta.json` v2 separates what was **configured** (`arms`) from what actually **ran** (`observed_arms`) — the v1 shape conflated them into run-level fields that were false or ambiguous for a multi-harness set.

| Field | Notes |
|---|---|
| `format_version` | `2`. |
| `run_id` / `commit` / `config_hash` | Identity for cross-run aggregation. `config_hash` hashes only the planned selectors (`set`, `runner`, `arms`, `judge`, `binder`) — never `observed_arms` or a probed `judge.actual_version` — so two runs of one config hash identically regardless of what ran or where. |
| `iteration` / `started_at` / `evalspec_version` | The iteration name, run start time, and the evalspec release that produced the run. |
| `set` / `runner` | The resolved set name and its runner (`pytest`), or `null` for a trigger-only run with no eval set. |
| `arms` | The **complete configured roster**, including an arm that never produced a sample. See Planned arms below. |
| `observed_arms` | Per-arm **runtime provenance** — only arms that actually resolved and used a snapshot. See Observed arms below. |
| `judge` | The resolved judge: `harness`, `model`, `effort`, `timeout`, `env` (redacted), `harness_args`, `actual_version`. `actual_version` is a best-effort host-side probe of the judge binary (the judge runs on the host, not in a guest) — `null` when the probe fails, with no adjacent `_status`/`_error` pair, unlike the guest-probed fields below. |
| `binder` | The binder's fixed transport identity: `provider`, `model`, `api_path`. See Binder identity below. |

### Planned arms (`arms`)

Each entry in `arms` is the arm as **configured**, independent of whether it ran:

| Field | Notes |
|---|---|
| `name` / `harness` / `model` / `effort` | The arm's declared identity. |
| `env` | The arm's env overlay, redacted (secret-shaped keys are masked). |
| `harness_args` | The arm's pass-through CLI tokens. |
| `requested_version` | The harness's install **selector** (for example `"latest"`) — configured intent, not the concrete binary that ran. The concrete version is `observed_arms[name].actual_version`. |
| `capabilities.token_split` | Whether the harness reports a cache-aware token split. Per-arm, since one set can mix harnesses with different capabilities. |

> **Removed in v2, no aliases.** The v1 run-level `agent`, `agent_version`, and `token_split` fields are gone — they described only the default harness. A consumer migrating off v1 reads `arms[i].harness`/`arms[i].requested_version` in place of `agent`/`agent_version`, and `arms[i].capabilities.token_split` in place of the run-level `token_split`. There is no compatibility shim; a reader keyed on the old fields must be updated, not patched around.

### Observed arms (`observed_arms`)

Keyed by arm name. **Only arms with at least one persisted runtime record appear** — a configured-but-deselected, skipped, or never-sampled arm has no key here, never a fabricated one. Provenance is captured once execution resolves and uses a snapshot (right after `ensure_snapshot()`, before the task harness runs) and persisted beside each sample; aggregation at session-finish dedupes identical records per arm and raises if two records for one arm disagree on `snapshot`, `image_digest`, or `actual_version`.

| Field | Notes |
|---|---|
| `actual_version`, `actual_version_status` (`available`\|`unavailable`), `actual_version_error` | The task harness's binary version, probed **inside the guest snapshot** — never the host-side install selector. When `actual_version_status` is `unavailable`, `actual_version` is `null` and `actual_version_error` explains why; `actual_version_error` is present only on that path — the available path never emits an unexplained key. |
| `sandbox.backend` / `sandbox.snapshot` / `sandbox.fingerprint` | The resolved backend name, the snapshot it built or reused, and the cache-key fingerprint — from the same helper `snapshot_name()` uses, never recomputed independently. |
| `sandbox.base_image_ref` / `sandbox.install_fingerprint` / `sandbox.env_script_sha256` | The fingerprint's own inputs: the declared base image reference, the agent-install fingerprint, and the environment-script hash. |
| `sandbox.image_digest`, `sandbox.image_digest_status` (`available`\|`unavailable`), `sandbox.image_digest_error` | The backend-native image manifest digest, with the same available/unavailable pairing as `actual_version` above. |

**A recorded `image_digest` is an audit trail, not a cache key or a reproducibility guarantee.** It lets you notice, after the fact, that a floating base-image tag (`ubuntu:latest`) has moved since a snapshot was built — compare the digest recorded then against a digest read now. It does **not** by itself refresh the snapshot (evalspec does not become an OCI registry client and never auto-rebuilds on tag movement) and it does not, by itself, make a run reproducible: reproducing a run also depends on the base image's actual contents at pull time, the install step, and the environment script — none of which the digest alone pins down. Treat it as evidence for an investigation, not a guarantee baked into the artifact.

### Binder identity (`binder`)

Fixed and run-level — describes the assertion **binder** (the prose→checker classifier), not the configured judge: `provider` (`"gemini"`), `model` (`gemini-3.1-flash-lite`), `api_path` (`generativelanguage.googleapis.com/v1beta`). `GEMINI_API_KEY` is never recorded here or anywhere else in the artifact tree — this object carries transport identity, never key material.

Authoritative shape: `evalspec.plugin.build_manifest` / `_write_manifest`, `evalspec.report.planned_arms`, `evalspec.provenance`. When this document drifts, the code wins.

---

## `index.jsonl` — derived artifact

Flat per-sample results at the iteration root (`tmp/evals/iteration_NN/index.jsonl`), one JSON line per (eval × arm × sample). The aggregator's entry point — every field here is also present in the per-sample artifacts, so this file is a derived convenience, not a new input, and is rebuilt from disk rather than validated at collection.

| Field | Notes |
|---|---|
| `skill` / `eval_id` / `arm` / `sample` | Case identity: the group (`skill`), the eval id, the arm name, and the sample index. |
| `kind` | Always `"eval"` today. |
| `harness` / `model` / `effort` | The arm's three core configured axes, denormalized from the planned-arm roster so a reader learns them without joining `meta.json`. Omitted entirely (never a faked `null`) for a row whose arm isn't in that roster. |
| `errored` | Whether the sample was an infra failure. |
| `passed` / `total` | Assertions passed vs. total for the sample. |
| `duration_ms` / `judge_ms` / `total_tokens` / `input_tokens` / `output_tokens` | Timing/token figures from the sample's `timing.json`, where present. |

Heavier per-arm provenance — sandbox identity, guest binary version — stays in `meta.json`'s `observed_arms` and the per-sample `provenance.json`; `index.jsonl` carries only the three core axes, never the full runtime record.

Authoritative shape: `evalspec.report.index_rows`. When this document drifts, the code wins.

---

## `benchmark.json` — derived artifact

The run-level benchmark. One `benchmark.json` (plus its rendered `benchmark.md`) is written per run at the **iteration root** — `tmp/evals/iteration_NN/benchmark.json`, beside `meta.json` and `index.jsonl` — aggregating every group in the run into a single report. It is a derived artifact rebuilt from the per-sample `grading.json` files on disk, not an input schema and not validated at collection.

| Field | Notes |
|---|---|
| `format_version` | `3`. Distinct from the `meta.json` run-manifest `format_version` (`2`); the two version independently. |
| `label` | The run name (`iteration_NN`). The report spans the whole run, so there is no per-group suffix. |
| `baseline` | The baseline arm name the Δ columns measure against, or `null` when the set names none — or when the named baseline produced no graded sample. |
| `max_samples` | The highest per-cell sample count observed (from `--count N`). |
| `roster` | The run's eval roster: a list of `{group, eval_id}` objects, one per discovered `<group>/eval-*` directory, sorted by `(group, eval_id)`. It drives the matrix rows, so an all-errored eval (no graded sample in any arm) still appears as an all-`—` row. |
| `arms` | Per-arm **stats**, keyed by declared arm name — `pass_rate`, `pass_rate_stdev`, `n`, `delta_pp` / `delta_noise_pp` (vs baseline), harness/model/env metadata, and a `per_eval` list. An arm configured but absent from disk is still a column (`pass_rate: null`). Each `per_eval` row carries its own `group` and `eval_id` — matched against the roster by that composite key — plus `pass_rate_mean` and `samples`. |
| `planned_arms` | The complete configured arm roster, same shape and builder (`report.planned_arms`) as `meta.json`'s `arms` — see [`meta.json`](#metajson) above. A configured-but-unobserved arm is still a valid, empty `arms` matrix column here; it carries no fabricated provenance. |
| `observed_arms` | Aggregated per-arm runtime provenance, same shape as `meta.json`'s `observed_arms` (see above) — only arms with a persisted record appear. |
| `runner` | The run's runner (for example `pytest`), or `null` for a trigger-only run. |
| `binder` | The binder's fixed transport identity — same shape as `meta.json`'s `binder` (see above). |

Note the naming split: `arms` is per-arm **result stats** (pass rate, deltas, `per_eval`); `planned_arms` and `observed_arms` are the **configuration/provenance** pair mirroring `meta.json` — don't confuse the two `arms`-adjacent keys.

`benchmark.md` renders the composite `group/eval_id` for each matrix row and one column per arm, then a compact `## Provenance` section below the matrix: one line per matrix-column arm giving its observed guest version, snapshot, and image digest where available. A configured arm with **no persisted runtime record is labeled `not observed`**, never shown with fabricated identity, and the section adds no matrix columns.

The JSON keeps `group` and `eval_id` as separate machine fields on every `roster` entry and `per_eval` row. Authoritative shape: `evalspec.report.build_benchmark`. When this document drifts, the code wins.

---

## Trajectory event schema — derived artifact

The structured trajectory `evalspec.trajectory` derives from a `session.jsonl` — not an input schema, not validated at collection. The run consumes it in-process (judge process facts, `transcript.json` counts); regenerate on demand with `trajectory_from_session(session_text)`. Each turn contributes an ordered list of two event kinds:

| Event | Fields |
|---|---|
| `tool_call` | `kind`, `id` (call id), `name` (tool name), `arguments` (object) |
| `tool_result` | `kind`, `call_id` (links to the call's `id`), `is_error` (bool), `content` (string, truncated to 2000 chars) |

`trajectory_from_session` tags each event with its source turn (`turn`); the per-turn `extract_trajectory` does not.

Field names map onto the OpenTelemetry GenAI semantic conventions: `name` → `gen_ai.tool.name`, `id`/`call_id` → `gen_ai.tool.call.id`, `arguments` → `gen_ai.tool.call.arguments`, `content` → `gen_ai.tool.call.result`.

OpenCode models a tool use as a single completed `tool_call` with no separate `tool_result` frame and exposes no call id, so its events carry `id: ""` and emit calls only. A `Skill` dispatch is normalized to `name: "Skill"`, `arguments: {"skill": <name>}` across both agents, so `skills_dispatched` / `render_process_facts` stay agent-agnostic.

Canonical definition: the `evalspec.trajectory` module docstring. When this document drifts, the code wins.
