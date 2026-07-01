# Schema reference

Output evals and trigger (routing) evals are both **Markdown**. Per skill:

```
<skill-dir>/evals/<slug>/prompt.md     # one self-contained output eval per slug dir
<skill-dir>/evals/<slug>/fixtures/      # optional starting files for that eval
<skill-dir>/evals/trigger-evals.md     # evalspec-trigger/v1 (routing queries)
<skill-dir>/evals/setup.sh             # per-cell skill installer (see configuration.md / agents.md)
```

`<skill-dir>` is any directory under the configured eval roots (default `skills/` and `.claude/skills/`; override via `--evalspec-eval-roots` or `[tool.evalspec] eval_roots`). The suite identity is the directory's basename — there is no `skill_name` frontmatter and no suite header.

Each output eval is one `evals/<slug>/prompt.md` file; its parent dir name is the `slug`. `mdformat.py` owns the Markdown structure, `schema.py` owns validation. Both are strict and loud: unknown headings, plain `-` bullets, indented non-checkbox lines, prose outside known sections, grandchild or ragged assertion nesting, and unknown frontmatter keys all fail at pytest collection with the offending path quoted.

---

## Output evals — the `evals/<slug>/prompt.md` format

One file per eval. The parent directory name is the `slug` (kebab-case, unique within the suite); it appears in test ids (the full id is `<skill>-<slug>-<arm>`) and artifact paths (`eval-<slug>/`). Optional YAML frontmatter carries `seed:` only, then a required `## Prompt` and a required `## Assertions` checklist.

```markdown
---
seed:
- role: user
  text: I dropped a clipping in ./Inbox earlier.
- role: assistant
  text: Got it — say the word and I'll file it.
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
| `seed` (frontmatter) | no | A list of `{role, text}` turns rendered as a transcript prefix before the graded prompt — prior context. See [Seed](#seed). The **only** frontmatter key. |
| `## Prompt` | yes | The agent instruction. `{TODAY}` is substituted; paths are `./`-relative to the working directory. |
| `## Assertions` | yes | A `- [ ]` checklist; each line's text (after the checkbox) is plain **prose**. Non-empty. A top-level item with indented `- [ ]` children is a display-only header (never graded) whose children flatten to standalone assertions in document order — one nesting level only. H3 subheadings are display-only groups, also flattened in document order; no semantics ride on the H3 title. |

There is no `checks:` field, no `skill_name`, and no `background.md`. Every assertion is plain prose; the **binder** derives the deterministic check at grade time (see [Assertions](#assertions) and `concepts.md`). A `- [ ]` item with indented `- [ ]` children decomposes into one graded atom per child, in document order; the parent line is a display-only header. One nesting level only — a grandchild (any indent deeper than the first child), a ragged child indent, an indented non-checkbox line, an orphan child with no parent, and a plain `-` bullet without a checkbox are all hard errors. A parent and its children must share one `## Assertions` body or one `###` group.

`fixtures/` (a sibling of `prompt.md` under the slug dir) holds the eval's starting files; its contents are copied into the per-cell clean room mounted at `/workspace` before the prompt runs. No fixtures ⇒ an empty workdir.

### Seed

`seed:` is an optional list of `{role, text}` turns. It renders into a `<transcript>…</transcript>` block prepended to the graded prompt — render-into-prompt, so prior context works on any agent without session injection. Only the final (graded) prompt is graded; the seed is context.

```yaml
seed:
- role: user
  text: Here's the deadline — Friday.
- role: assistant
  text: Noted.
```

Each turn needs a non-empty `role` and `text`. `{TODAY}` is substituted in `text`; any other `{UPPERCASE}` placeholder is rejected, symmetric with the graded prompt, so a stray token fails the eval rather than leaking to the agent. A malformed seed (non-list, missing/empty field) fails at collection.

### Assertions

Every `## Assertions` entry is plain prose. The **binder** classifies each one at grade time: when confident, it maps the prose to one of six deterministic **checkers** (`file_exists`, `glob_count`, `sha256_match`, `frontmatter_has`, `regex`, `skill_invoked`) run on the host against the final workdir or process facts; otherwise it **punts** to the LLM judge. Authors write no checker syntax — there is none to learn. The split is invisible from the suite. See `concepts.md` for the binder's contract.

One assertion is special by convention: **activation**. Write it as

```markdown
- [ ] Skill `archive` invoked
```

The binder maps that line to the `skill_invoked` checker, which grades against the arm's dispatched-skills set: True where the skill fired, False where it didn't. It's an ordinary prose assertion graded symmetrically across arms — there is no separate invocation gate. A trial arm (skill installed) passes it; a baseline arm (no skill) fails it.

### Placeholder substitution

Applied before the agent sees the content.

| Placeholder | Substituted as | Where |
|---|---|---|
| `{TODAY}` | ISO `YYYY-MM-DD` from the host clock (UTC, for guest agreement) | Prompt, assertions, seed text, fixture files, fixture filenames |

Paths are `./`-relative: the agent runs with its current working directory set to the workdir mount (`/workspace`), so `./Inbox/x` resolves there. Hidden-directory assertions preserve the hidden path component, for example `./.meta/templates/entity-person.md exists` binds to that full relative path. Any `{UPPERCASE_PLACEHOLDER}` other than `{TODAY}` in a prompt or seed fails fast — eval prompts must be spec-free to keep baselines honest. Put the value in the fixture.

---

## `evalspec-trigger/v1` — trigger evals

Trigger evals live in `trigger-evals.md`. Each query becomes one parametrized routing test: invoke the agent against `query` (no fixtures, no judge), assert whether the skill was dispatched. `mdformat.parse_trigger` owns parsing; `schema` validates it.

Structure: YAML frontmatter (`skill_name`), an optional `## Description`, then `## Trigger` and `## No Trigger` sections. Each query is `- <slug>: <query>`; polarity comes from section membership (`## Trigger` → `should_trigger=true`; `## No Trigger` → `should_trigger=false`). An optional `  - fails-on [tiers]: reason` indented under a query marks a known miss or over-fire.

```markdown
---
skill_name: ingest
---
## Description
Positives capture INTO the wiki; negatives lean on near-miss siblings.

## Trigger
- ingest-article: ingest this article into my knowledge base
- inbox-process: process the source in my Inbox and add it to the wiki
  - fails-on [sonnet]: routes on opus; sonnet misses. Cross-verified 2026-05-30.

## No Trigger
- archive-near-miss: archive this transcript, I'm done with it
- summarize-readonly: summarize what my vault says about OpenAI
```

| Field | Source | Required | Notes |
|---|---|---|---|
| `skill_name` | frontmatter | yes | Kebab-case. Matches the skill's frontmatter `name` (what the Skill tool reports). Trigger evals keep this key — routing detection matches the dispatched skill's reported name, which is decoupled from the directory basename. |
| `## Trigger` / `## No Trigger` | section | yes | Polarity sections. ≥1 query total. Queries under `## Trigger` should route; queries under `## No Trigger` should not. |
| `slug` | `<slug>:` prefix | yes | Kebab-case, unique within file. Identity for artifacts, report rows, `-k`. Decoupled from the query text. |
| `query` | line remainder | yes | User message the agent receives. Routing-sensitive — never reword. Backticks are safe (not a table cell). |
| `fails-on` | indented sub-bullet | no | `fails-on [tiers]: reason`. `tiers` ⊆ `{opus, sonnet, haiku}`. The runner marks `pytest.mark.xfail(strict=False)` only when `--evalspec-model` matches a listed tier; all others run strict. `reason` is free prose (dated provenance). Section membership remains the design intent — `fails-on` only relaxes the gate per tier. Applies to both polarities: a positive that misses and a negative that over-fires are both tier failures. |

Run the suite per tier:

```
uv run pytest -p evalspec.plugin -k trigger --evalspec-model opus
uv run pytest -p evalspec.plugin -k trigger --evalspec-model sonnet
uv run pytest -p evalspec.plugin -k trigger --evalspec-model haiku
```

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

- A `slug` (the output eval's parent dir) and `skill_name` (trigger frontmatter) must match `^[a-z0-9]+(-[a-z0-9]+)*$` (kebab-case); the query `slug` must be kebab-case and unique within its file.
- Assertion strings must be non-empty after stripping whitespace; `## Assertions` itself must be non-empty.
- A seed turn needs a non-empty `role` and `text`; the seed must be a list.
- The only output-eval frontmatter key is `seed`; the only trigger frontmatter key is `skill_name`. Unknown frontmatter keys, top-level keys, or per-item fields are rejected. Add functionality via a new `$schema` version or a documented field, not by sneaking one in.

Authoritative validator: `evalspec.schema` (plus `evalspec.mdformat` for Markdown structure). When this document drifts, the code wins.

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
