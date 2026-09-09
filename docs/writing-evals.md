# Writing evals

This is the authoring reference: where eval files live, what goes in them, how
the workspace and `setup.sh` shape the sandbox, and how each assertion actually
gets graded. It is for anyone writing or reviewing an eval. If you have not run a
benchmark yet, start with the [quickstart](quickstart.md); the terms used here
(group, arm, clean room, binder, checker, judge) are defined in
[concepts.md](concepts.md).

## Anatomy of an eval folder

Every eval lives in a folder, its **group**, that can carry up to four kinds of
files:

```
<search-path>/…/<group>/
  eval.md              # one eval; id = the folder name
  <stem>.eval.md       # or several siblings; id = the file stem
  workspace/           # optional starting files, copied into the sandbox
  setup.sh             # optional per-arm sandbox setup
```

Use `eval.md` for the common one-eval folder. Use sibling `<stem>.eval.md` files
when a folder holds a compact suite of related cases; the siblings share the
folder's `workspace/` and `setup.sh`. Case identity is the pair
`(group, eval_id)`, both kebab-case, and the pytest test id is
`test_eval[<group>-<eval_id>-<arm>]`, which is what `-k` filters against.
Artifacts land under `<group>/eval-<eval_id>/` in the run tree.

### Discovery

benchspec finds evals by walking a configured list of **search paths** under
the repo root: `skills`, `tests`, `evals`, and `benchmarks` by default. Any
`eval.md` or `*.eval.md` file anywhere beneath a search path is an eval; the
filename is the marker, so no particular ancestor directory is required.
Override the list with `eval_paths` in `[tool.benchspec]` or the
`--eval-paths` flag; that is the only discovery knob:

```toml
[tool.benchspec]
eval_paths = ["evals"]   # only walk evals/; add ".claude/skills" or similar as needed
```

> **Edge case:** the walk skips symlinks, stops at embedded Git repos, and prunes
> `tmp/`, `.git`, `__pycache__`, and every dot-prefixed directory *inside* a
> search path (a dot-prefixed path you configure explicitly, like
> `.claude/skills`, is still walked). A `workspace/` sitting beside an eval file
> is that eval's sandbox seed and is never descended into. A duplicate
> `(group, eval_id)` pair fails collection loudly, naming both files.

## The eval file

The format is deliberately rigid so a malformed eval fails at pytest collection
with the offending path quoted, rather than silently grading wrong:

```markdown
---
history:
- role: user
  content: I saved some meeting notes in ./notes earlier today.
- role: assistant
  content: Got it — say the word and I'll write them up.
---

## Prompt

You are working in a project rooted at your current working directory.
Read ./notes/standup.md and write a summary to ./reports/{TODAY}/standup.md.

## Assertions

- [ ] ./reports/{TODAY}/standup.md exists
- [ ] ./notes/standup.md is byte-identical to its pre-run content
- [ ] Skill `summarize` invoked

### Summary quality (a display-only group)

- [ ] the summary names every action item from ./notes/standup.md
```

| Part | Required | Rules |
|---|---|---|
| Frontmatter | yes | The `---`/`---` delimiters are required even when empty. `history` is the **only** allowed key. |
| `history` | no | A list of `{role, content}` turns, each non-empty. Rendered as a transcript prefix before the graded prompt, so prior context works on any harness because it rides in the prompt rather than in session state. Only the final prompt is graded. |
| `## Prompt` | yes | The agent's instruction. Non-empty prose. |
| `## Assertions` | yes | A `- [ ]` checklist, at least one item. |

Anything else is a hard error: unknown headings, prose before the first `##`,
plain `-` bullets, unknown frontmatter keys. `### ` subheadings are legal only
inside `## Assertions`, where they are display-only groups: no semantics ride on
the title, and the items flatten in document order.

### Decomposing compound assertions

One assertion should state one fact. When a claim naturally bundles several, nest
children under a display-only parent. The parent is never graded, and each child
becomes a standalone assertion:

```markdown
## Assertions

- [ ] the project was scaffolded:
  - [ ] the ./src/ directory exists
  - [ ] the ./tests/ directory exists
  - [ ] ./README.md opens with '# My Project'
```

One nesting level only. A grandchild, a ragged child indent, an indented
non-checkbox line, or an orphan child with no parent are all collection errors.
The payoff for splitting is deterministic grading: a bare existence claim binds to
a checker, while "exists *and* contains X" always goes to the judge (more on this
below), and a failure pinpoints the exact clause that missed.

### Placeholders

`{TODAY}` substitutes to the host's UTC date (`YYYY-MM-DD`) in the prompt,
assertions, history content, and in workspace file contents *and file names*, so
date-stamped fixtures and date-sensitive assertions stay honest on any day the
suite runs. Any other `{UPPERCASE}` token in a prompt, history turn, or assertion
is rejected before the agent runs rather than leaked to it: eval prompts must be
self-contained, so put values in the workspace, not in placeholders.

## The workspace and the clean room

If the eval folder has a `workspace/` directory, its contents are copied into a
fresh temporary directory, the **clean room**, that is bind-mounted into the
sandbox at `/workspace`, which is also the agent's working directory. No
`workspace/` means the agent starts in an empty directory.

The clean room lives outside your project on purpose. It contains exactly what
`workspace/` seeded plus whatever the agent writes. A staged copy of the repo
(what a clone would contain, never `.env`, `.git`, or earlier runs' artifacts) is
separately mounted read-only at `/project` for `setup.sh`, and the agent can read
it, so do not point prompts or harness arguments there unless repo access is part
of the experiment. Before the agent runs, benchspec records the SHA-256 of
every seeded file, so an assertion like "byte-identical to its pre-run content"
is decidable mechanically afterward.

Write every path in the eval `./`-relative (`./notes/standup.md`, not an absolute
path or a repo-relative one), because that is how the agent, the checkers, and
the judge all see the workspace.

## `setup.sh`: what differs per arm

The eval file is identical across arms; `setup.sh` is where arms diverge. If the
eval folder contains one, it runs inside the sandbox before the prompt, from
the eval folder itself under the read-only `/project` mount, with these
variables set:

| Variable | Meaning |
|---|---|
| `BENCHSPEC_ARM` | The arm's name, the usual branching key. |
| `BENCHSPEC_MODEL` | The arm's task model. Informational; it does not route the model. |
| `BENCHSPEC_HARNESS` | The harness running this cell (`claude-code`, `codex`, `opencode`). |
| `BENCHSPEC_SET` | The explicitly selected set name; empty when the run used `default-set`. |

The arm's own `env` table is layered on top, so a set that declares
`env = { CONTEXT_PROFILE = "full" }` can be read here too. A non-zero exit aborts
the cell as an infra error; it never grades as a failed eval.

The canonical use is the install split:

```bash
#!/usr/bin/env bash
set -e
if [ "$BENCHSPEC_ARM" = "baseline" ]; then
  exit 0
fi
mkdir -p /home/benchspec/skills/my-skill
cp ../../SKILL.md /home/benchspec/skills/my-skill/SKILL.md
```

`/home/benchspec/skills` is the harness-neutral skills home; every agent's
native skill directory is linked to it when the snapshot is built, so one script
serves every harness in a mixed set.

## How assertions are graded

This is the part worth internalizing, because it shapes how you word assertions.
Every assertion takes one of two paths, and the split is decided per line, at
grade time, by the **binder**: a conservative classifier (a fixed
`gemini-3.5-flash-lite` call, which is why its credential, `GEMINI_API_KEY` by
default or `OPENROUTER_API_KEY` under `[tool.benchspec.binder] provider =
"openrouter"`, is always required).

- **Bind**: the binder maps the prose to one deterministic **checker**, run on
  the host against the final workspace (or the run's process facts). Zero
  variance, zero judge cost.
- **Punt**: everything else goes to the **judge**, an LLM grading from
  collected evidence: the workspace tree, file contents, SHA-256s, the agent's
  final message, and process facts (the tools and skills it invoked). The judge
  never grades from recall. All of a cell's punted assertions go to the judge in
  one call.

The checkers the binder can emit:

| Checker | Checks |
|---|---|
| `file_exists` / `not_file_exists` | A path exists / no longer exists (files and directories alike). |
| `glob_count` | Exactly `count` (or at least `min`) files match a glob. |
| `regex` | A file's content matches a pattern. |
| `frontmatter_has` | A file's YAML frontmatter has a key (optionally a value). |
| `sha256_match` | A file is byte-identical to a named pre-run file or a literal SHA-256. |
| `skill_invoked` / `not_skill_invoked` | The arm did / did not dispatch a skill (a process fact, not a file). |

> **Key concept:** the binder is tuned so that a *false positive*, a surface
> check passing on wrong output, is the one unacceptable error. Punting is free
> (the judge was going to grade the line anyway), so anything doubtful punts. In
> particular, persistence and negation claims ("still present", "not
> duplicated", "no new entry was written") always punt: a surface check can see
> the substring but is blind to the invisible "and was not replaced" clause. The
> one carve-out is an explicit byte-identity claim, which binds to
> `sha256_match`.

Practical wording consequences:

- **One fact per line.** "X exists and is accurate" always punts; "X exists"
  binds, and the accuracy claim can stand alone as a judged line.
- **Full paths, `./`-anchored.** The binder copies paths exactly as written, and
  the pre-run SHA map is keyed by full path; a basename will not resolve.
- **Activation lines are exact by convention.** `` Skill `X` invoked `` and
  `` Skill `X` not invoked `` bind to the activation checkers; a longer sentence
  that also claims a file was written is compound and punts. A namespaced dispatch
  (`plugin:summarize`) satisfies an assertion written against `summarize`.

Activation deserves one more note: it is an ordinary assertion, graded
symmetrically across arms. There is no separate trigger-eval format and no
invocation gate. On a trial arm the positive form typically passes; on a baseline
arm (no skill installed) it fails, and that asymmetry is part of the delta you are
measuring. A trial arm that fails its own activation assertion is a routing
finding: the skill was there and the agent did not use it.

## The authoring loop: `lint`, then `analyze`

Two commands close the loop before a paid run.

`benchspec lint` is static and free. It scans every discovered assertion for
wording the judge cannot fairly grade, and exits `1` on any finding:

| Rule | Fires on |
|---|---|
| `vague-adverb` | `explicitly`, `appropriately`, `properly`, `gracefully`, `suitably`, `reasonably`, `adequately`: adverbs naming no observable criterion. |
| `unseen-file` | A path-like string (`dir/file.ext`) with no `./` anchor anywhere in the line; the judge only sees workspace facts. |
| `relative-claim` | `better`, `worse`, `cleaner`, `clearer`, `stronger`, `improved` with no "than" comparand. |

`benchspec analyze` asks the binder itself. It binds every assertion exactly
as a live run would and prints one label per line, `deterministic` or
`judge-backed`, so you can see where each assertion lands and tighten wording
until the facts you care most about grade deterministically. It needs the
binder's credential exported (bare existence lines take a free local fast path;
every other line pays one binder call) and always exits `0` on a classified
suite: it is a report, not a gate.

## Reference: validation rules

- `group` and `eval_id` must match `^[a-z0-9]+(-[a-z0-9]+)*$` (kebab-case).
- `## Prompt` and `## Assertions` are required and non-empty; assertions are
  non-empty strings.
- `history` must be a list of `{role, content}` turns, both fields non-empty.
- `history` is the only frontmatter key; unknown keys are rejected.
- Eval files must be named `eval.md` or `<stem>.eval.md`; anything else errors.

The authoritative validators are `benchspec.specs.mdformat` (Markdown
structure), `benchspec.specs.schema` (the parsed shape), and
`benchspec.specs.lint` (the lint rules); the binder's prompt and checker list
live in `benchspec.grading.binder` and `benchspec.grading.checkers`. If this
page and those modules ever disagree, the modules are right.
