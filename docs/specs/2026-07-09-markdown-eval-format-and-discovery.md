**TL;DR** — Replace skill-root eval discovery with a recursive `**/evals/` crawl for `eval.md` / `*.eval.md`, rename the frontmatter `seed:[{role,text}]` block to `history:[{role,content}]`, move per-eval `fixtures/` → `workspace/` and suite-level `setup.sh` → per-eval `setup.sh`, and key cases on a folder-derived id no longer tied to a skill directory — Phase 3 of the #1 roadmap.

## Problem

- **Symptom:** `discovery._skill_dirs` only finds evals under configured `eval_roots` (`skills`, `.claude/skills`), and `discover_eval_cases` requires the `evals/<slug>/prompt.md` dir shape (`discovery.py:202-258`). The roadmap's target layout is any `**/evals/` folder holding `eval.md` or sibling `*.eval.md` files — a layout the current crawl cannot see.
- **Symptom:** The eval-file frontmatter key is `seed:` carrying `{role, text}` turns (`mdformat.py:39,190-195`; `schema._validate_seed`, `schema.py:156-168`), but the README vision writes `history:` carrying `{role, content}` turns.
- **Symptom:** Per-eval seed files live in `evals/<slug>/fixtures/` (`discovery.py:66-69`) and `setup.sh` is a single suite-level script located by skill name and run from `skills/<skill>/evals/setup.sh` (`sandbox.run_setup_sh`, `sandbox.py:251-279`) — the vision places both a `workspace/` dir and a `setup.sh` inside each eval folder.
- **Symptom:** Case identity is the skill-directory name: `EvalCase.skill = skill_dir.name` feeds artifact paths (`workspace.arm_dir(repo_root, skill, eval_id, …)`), test ids (`param_id`), and grading records (`execution.py:374-382`). The new layout has no skill directory, so a replacement identity is forced.
- **Exposed by:** Roadmap #1 Phase 3 Done-When: discover `**/*/evals/eval.md` and `*.eval.md`; `history[{role, content}]`, per-eval `workspace/`, and per-eval `setup.sh` work; old `prompt.md`/`seed` behavior is not required.
- **Scope:** Eval discovery, the Markdown eval format (filenames + `history` frontmatter), the per-eval `workspace/` and `setup.sh` mechanisms, and the case-identity change those force. Trigger discovery, skill-dispatch detection, report grouping, and artifact metadata are touched only as far as the identity rename forces — their redesigns belong to later phases.
- **Constraint:** No compatibility shims (roadmap rule). The old `prompt.md` dir shape, `seed:` key, and `fixtures/` name are removed outright and in-repo consumers move to the new surface; there is no dual-read.
- **Constraint:** `trigger-evals.md` discovery is not retired here — Phase 4 owns that. Phase 3 must leave trigger routing working while it rewrites eval discovery.

## Solution

```text
**/evals/                     # any evals/ dir, found by recursive crawl (tmp/, .git,
  summarize-transcript/       # __pycache__, hidden dirs pruned)
    workspace/transcript.md   # copied into the sandbox workdir (was fixtures/)
    setup.sh                  # per-eval sandbox setup (was one suite-level script)
    eval.md                   # id = folder name  →  group `summarize-transcript`
  to-spec-activation/
    write-spec.eval.md        # id = file stem    →  group `to-spec-activation`, eval `write-spec`
    prd-migration.eval.md
```

```yaml
---
history:                      # renamed from `seed:`
  - role: user
    content: Here is the transcript.   # `content` renamed from `text`
---
## Prompt
…
## Assertions
- [ ] A file exists at `./brief.md`
```

Discovery crawls for `eval.md` / `*.eval.md`, keys each case on a `(group, eval_id)` pair derived from the folder and filename, and fails loudly on a duplicate pair; the format, workspace, and setup renames follow mechanically.

## User Stories

1. As an eval author, I want **to drop `eval.md` under any `evals/` folder**, so I place evals by product concern without learning the skill-root layout or a `[tool.evalspec] eval_roots` list.
2. As an eval author, I want **sibling `*.eval.md` files in one folder**, so a compact suite of related probes (e.g. activation cases) shares one `workspace/` and `setup.sh` without a dir-per-case.
3. As an eval author, I want **`history:` with `role`/`content` turns**, so prior chat context reads the way every other agent format writes it.
4. As an eval author, I want **a per-eval `setup.sh`**, so one eval installs its own preconditions instead of every eval in a folder sharing one suite script.

## Implementation Decisions

```text
repo_root ──► discovery.discover_eval_cases
                └─ crawl **/evals/  (prune tmp,.git,__pycache__,hidden)
                     ├─ evals/<folder>/eval.md          ─┐
                     └─ evals/<folder>/*.eval.md         ─┤
                                                          ▼
                           mdformat.parse_eval_md ──► schema._validate_evals_v1
                                                          ▼
                              EvalCase(group, eval_id, eval_dir, workspace_dir)
                                        │
              ┌─────────────────────────┼──────────────────────────────┐
              ▼ room.seed_room          ▼ sandbox.run_setup_sh          ▼ workspace.arm_dir
        copy workspace/ → workdir   bash PROJECT_MOUNT/<reldir>/setup.sh   …/<group>/eval-<eval_id>/<arm>/
```

- **Eval discovery becomes a recursive `**/evals/` crawl, decoupled from `eval_roots`.** `discover_eval_cases` walks the tree for directories named `evals`, and within each collects `eval.md` and `*.eval.md` files.
  - **Prune non-source subtrees** while walking: `tmp/` (the artifact root, `workspace.workspace_parent`), `.git`, `__pycache__`, and dot-prefixed dirs — the exact scratch/mirror crawl the old `_skill_dirs` docstring called out as the reason it used explicit roots. The deny-list is fixed, not configurable.
  - `[tool.evalspec] eval_roots` and `--evalspec-eval-roots` stop governing eval discovery. They remain read only by trigger discovery (below) until Phase 4 removes triggers.
- **Case identity is a `(group, eval_id)` pair, folder-derived, preserving the two-axis artifact path.** The existing path is `…/<skill>/eval-<eval_id>/<arm>/sample-K`; `group` takes the `<skill>` slot and `eval_id` the eval slot, so `workspace.arm_dir`, `execution` grading records, and report keys need no shape change this phase.
  - `eval.md` → `group` = eval folder name, `eval_id` = same folder name.
  - `<stem>.eval.md` → `group` = eval folder name, `eval_id` = `<stem>`.
  - `param_id` = `f"{group}-{eval_id}"`; both segments must be kebab-case (schema already enforces this on the id).
- **Duplicate `(group, eval_id)` pairs fail loudly at collection.** A recursive crawl can surface `a/evals/happy/eval.md` and `b/evals/happy/eval.md` — the old `_skill_dirs` raised on duplicate skill names and this preserves that guard as a `SchemaError` naming both source paths. A single loud fail replaces path-qualified ids; a richer collision scheme is deferred to Phase 6/8 if folder-name clashes prove common in practice.
- **The frontmatter block renames `seed` → `history` and `text` → `content`; the flatten stays.** `mdformat._EVAL_FM = {"history"}`, `parse_eval_md` reads `fm["history"]`, `schema._validate_seed` becomes `_validate_history` (keys `{role, content}`), and `_validate_evals_v1`'s `allowed_eval` swaps `seed`→`history`. `room.render_seed` → `render_history`, still flattening turns into the prompt prefix — no real multi-turn replay is introduced here (single-turn task execution is unchanged).
- **`fixtures/` → `workspace/`, relocated into the eval folder.** `EvalCase.fixtures_dir` becomes `workspace_dir = eval_dir / "workspace"`; `room.seed_room` copies it into the workdir exactly as before. For `*.eval.md` siblings, all cases in the folder share the one `workspace/` (the vision's compact-suite intent).
- **`setup.sh` moves from one suite-level script to per-eval, located by the eval's relative dir.** `run_setup_sh` no longer `cd`s into `skills/<skill>/` by name; it runs `bash PROJECT_MOUNT/<eval_dir_relpath>/setup.sh` when that file exists, passing the eval dir relpath (from repo root) through `SandboxSession`. `EVALSPEC_*` cell-env vars (arm, model, harness, set) still reach the script. For `*.eval.md` siblings, all cases in the folder share the one `setup.sh`.
  - The project still mounts read-only at `PROJECT_MOUNT`, so the guest can read the eval folder's `setup.sh` and `workspace/`. The `_plugin_dir_for` / project-marker mount logic is unchanged.
- **Trigger discovery is left on the old skill-root path, untouched.** `discover_trigger_cases`, `_skill_dirs`, `resolve_eval_roots`, and `parse_trigger` keep working against `skills/<skill>/evals/trigger-evals.md`. Phase 4 ("process-fact assertions; `trigger-evals.md` no longer required") retires them. This keeps the two discovery paths independent so Phase 3 doesn't strand routing.
- **`lint.py` and `cases.py` follow the new file path.** `lint.py:73` reads `case.skill_dir / "evals" / case.slug / "prompt.md"`; it moves to the case's `eval_dir / <filename>`. The `cases.py` fixtures wire `workspace_dir` in place of `fixtures_dir`.

## Testing Plan

### Logic
- **Discovery finds both filename shapes under any `evals/` depth** — `eval.md` and `*.eval.md` files anywhere in the tree become cases, while `tmp/`, `.git`, `__pycache__`, and hidden dirs are never crawled.
- **Identity derivation is correct and collisions fail loudly** — folder name and file stem map to `(group, eval_id)` per the rules above, and two evals resolving to the same pair raise a `SchemaError` naming both paths rather than silently colliding.
- **The `history` format parses to the same internal shape the old `seed` did** — `history:[{role, content}]` validates, renders into the prompt prefix, and rejects the old `seed`/`text` keys and malformed turns.
- **Format strictness is preserved** — the `## Prompt` / `## Assertions` checklist rules, H3 grouping, and loud-failure behavior carry over unchanged from `prompt.md` to `eval.md`.

### Behavior
- **A `workspace/`-bearing eval runs end to end** — files under the eval's `workspace/` land in the sandbox workdir before the agent runs, and the arm produces the expected artifacts and grading records under `…/<group>/eval-<eval_id>/`.
- **A per-eval `setup.sh` executes in the sandbox** — the eval-folder script runs from its mounted relative path with the `EVALSPEC_*` cell env available, and a nonzero exit fails the arm loudly.
- **Trigger routing still runs** — `trigger-evals.md` suites discover and execute unchanged alongside the rewritten eval discovery.

### Interface
- **Old layouts are gone, not dual-read** — a `prompt.md` dir, a `seed:` key, a `text` turn field, or a `fixtures/` dir is no longer recognized; no compatibility path accepts them.

## Open Questions

- Should `group` be the bare folder name or the eval-relative path from its `evals/` ancestor? Bare name is lower-churn and matches the vision's examples; a path-qualified group only becomes necessary if real suites collide on folder name. Resolve by checking whether any in-repo contract suite (Phase 9) needs same-named folders under different trees.

## Documentation Plan

- **`docs/schema.md`**: document `eval.md` / `*.eval.md` discovery, the `history:[{role, content}]` frontmatter, the per-eval `workspace/` and `setup.sh`, and the `(group, eval_id)` identity + duplicate rule.
- **`src/evalspec/discovery.py`** + **`src/evalspec/mdformat.py`** module docstrings: rewrite the skill-root / `prompt.md` / `seed` descriptions to the crawl-based layout.
- **`src/evalspec/room.py`** + **`src/evalspec/sandbox.py`** docstrings: `fixtures/`→`workspace/` and suite-level→per-eval `setup.sh`.
- **`README.md` / `docs/quickstart.md`**: update any eval-authoring example that shows the `skills/<skill>/evals/<slug>/prompt.md` shape.

## Out of Scope

- Retiring `trigger-evals.md` and folding skill-dispatch into `## Assertions` process facts — Phase 4. Interim gap: `execution.detect_skill = eval_case.skill` still keys skill detection off the case's `group`; Phase 4 replaces it with `Skill X invoked` assertions.
- Report/matrix grouping changes and the `All evals` row — Phase 6. Phase 3 keeps the existing report keys by preserving the two-axis path.
- Recording eval-format/workspace facts in `meta.json` and index rows — Phase 8 (arm-aware metadata).
- Real multi-turn `history` replay — `history` flattens into the prompt prefix as `seed` did; genuine turn-by-turn execution is not introduced.
- Authoring new README-shaped contract suites in this layout — Phase 9; this phase makes the layout discoverable, not populated.
- `evalspec lint` / `evalspec run` CLI surfaces — Phase 5, though `lint.py`'s internal path is updated here to keep it working.
- Migrating evals that live under `.claude/skills/**`. The dot-dir prune drops that subtree from the crawl, so downstream consumers with real output evals there lose eval discovery with no migration path (trigger discovery under `.claude/` is unaffected — it stays on the old skill-root path).

## References

- #1 — roadmap Phase 3 Done-When and the discovery/format decisions this spec instantiates; its Phase-1 and Phase-2 comments record the inter-phase gaps this phase inherits.
- `docs/research/evalspec-readme-vision.md:74-131` — the target eval layout, `eval.md`/`*.eval.md` rule, `workspace/`+`setup.sh` semantics, and the `history:[{role, content}]` format example.
- `src/evalspec/discovery.py:202-281` — `_skill_dirs`, `discover_eval_cases`, `discover_trigger_cases`, and `EvalCase` (skill/slug/fixtures identity) — the discovery this spec rewrites while leaving triggers intact.
- `src/evalspec/mdformat.py:39,185-223` — `_EVAL_FM`, `parse_eval_md`, and the `seed`→`history` / `prompt.md`→`eval.md` rename surface.
- `src/evalspec/schema.py:156-229` — `_validate_seed` and `_validate_evals_v1`'s `allowed_eval` (`seed`→`history`, `text`→`content`).
- `src/evalspec/room.py:33-82` — `seed_room` (copies `fixtures/`) and `render_seed` (flattens turns) — renamed to `workspace`/`render_history`.
- `src/evalspec/sandbox.py:251-379` — `run_setup_sh` and `SandboxSession`, the suite-level→per-eval setup mechanism and the eval-dir relpath it must now carry.
- `src/evalspec/workspace.py:84-86` — `arm_dir(repo_root, skill, eval_id, …)`, the two-axis artifact path the identity change preserves.
- `src/evalspec/execution.py:300-382` and `src/evalspec/lint.py:73` — the `eval_case.skill` / `prompt.md` consumers the identity and file-path renames touch.

## Verification

- `make lint` — the package and docs pass lint after the discovery rewrite and renames.
- `make test` — offline suites green: discovery of both filename shapes with pruning, `(group, eval_id)` derivation and the duplicate-pair `SchemaError`, `history` parse/validate/render, and the `prompt.md`/`seed`/`fixtures` removals.
- `grep -rEn "prompt\.md|\bseed\b|fixtures_dir|_skill_dirs" src/evalspec` — returns only the trigger-discovery / historical-doc references that legitimately survive; no eval-discovery path still reads the old shapes.
- `make evals SET=<a-workspace-and-setup-eval>` — a live arm run proves a crawled `eval.md` stages its `workspace/`, runs its per-eval `setup.sh`, and writes artifacts under `…/<group>/eval-<eval_id>/`.
