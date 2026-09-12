# `claude plugin eval` versus benchspec

Research, 2026-09-12. Anthropic shipped `claude plugin eval` in Claude Code
v2.1.269 on 2026-09-11. This note pins down what it is, where it overlaps with
benchspec, where it does not, and what benchspec should take from it. No code
changes were made.

Sources: the feature docs at
[code.claude.com/docs/en/plugin-evals](https://code.claude.com/docs/en/plugin-evals),
the CLI reference at
[plugins-reference#plugin-eval](https://code.claude.com/docs/en/plugins-reference#plugin-eval),
the Claude Code CHANGELOG entry for 2.1.269, and a clone of
`anthropics/claude-plugins-official` for the older `skill-creator` plugin.

## Answer up front

`claude plugin eval` is a built-in Claude Code subcommand, not a marketplace
plugin. It runs each case in a throwaway `claude -p` session with the plugin
loaded and again with nothing loaded, scores each run with authored graders,
and reports `WITH`, `W/OUT`, and `Δ` per case plus a suite mean, a JSON file,
and a self-contained HTML report. It is Claude Code only, two fixed arms only,
and it lives on the host rather than in a container. It is marked experimental
with a server-side kill switch.

The overlap with benchspec is the capability-lift benchmark on Claude Code:
"does my skill help, measured with and without it, over N runs, gated in CI."
That is benchspec's quickstart example, and Anthropic now ships it for free
with no Docker, no binder key, and no second vendor. Everything else benchspec
does is untouched: arms across three harnesses and any model, a real sandbox
with a seeded workspace, prose assertions with no grader DSL, pre-run hashes so
"nothing else changed" is decidable, provenance of what actually ran, a
cross-vendor judge, and a noise band on the delta.

Six things are worth taking. In priority order:

1. Stop counting `Skill X invoked` in the baseline rate. It inflates every
   delta. `plugin eval` excludes such graders from scoring in both arms and
   reports them as pass/fail indicators only.
2. Make the agent-turn timeout configurable. It is hardcoded at 600 s in each
   adapter and never threaded from config; `plugin eval` has `timeout_seconds`
   and `max_turns` per case.
3. Default the quickstart and the report to multiple samples, and warn on a
   single-sample delta. `plugin eval` runs three by default and says why.
4. Ship a benchspec skill for Claude Code so Claude can author and run
   benchspec evals from inside a session. `plugin eval init` does the
   interview-and-write step for its own format; benchspec has lint and
   analyze but nothing that helps write the file.
5. Add a `benchspec compare` between two iterations. `index.jsonl` and
   `benchmark.json` already exist for exactly this; `plugin eval`'s
   `baseline` grader and the docs' "catch regressions when a new model ships"
   framing are the same need.
6. Reposition the README: say plainly when `claude plugin eval` is enough and
   when benchspec is the step up.

Deliberately not taken: MCP mocks, a rubric-per-grader DSL, host-side
isolation, an HTML report, USD cost estimates. Reasons below.

## What `claude plugin eval` is

**Two things share the space, and only one is new.**

| | `claude plugin eval` | `skill-creator` plugin |
|---|---|---|
| Shipped | Claude Code v2.1.269, 2026-09-11 | Feb 2026, marketplace plugin |
| Form | CLI subcommand inside the closed-source binary | SKILL.md plus prompts and Python scripts run by subagents |
| Eval format | `evals/<case>/prompt.md` plus `graders/*.md`, optional `case.yaml` | `evals/evals.json` |
| Isolation | Throwaway home, cwd, and config; `claude -p` child on the host | Subagent context only |
| Arms | with-plugin and without-plugin | with_skill and without_skill or old_skill |
| Improve loop | None; edit and rerun | Yes, including a description optimizer |

The docs state the two formats are unrelated. The rest of this note is about
`claude plugin eval`.

**Layout.** One directory per case under `evals/`. `prompt.md` carries YAML
frontmatter (`runs`, `model`, `max_turns`, `timeout_seconds`, `allowed_tools`,
`append_system_prompt`, `env`, `tags`) and the prompt as its body. Each grader
is one Markdown file under `graders/` whose frontmatter names a `type` and
whose body is the rubric or pattern. `case.yaml` adds `context.scaffold_script`
(a Bash script run in the empty workspace before the agent, only with
`--scaffold`), `context.history_file` (a `.jsonl` transcript to resume), and
`context.add_dirs`. Runs write `results/<timestamp>/aggregate-result.json` and
`report.html`.

**Graders.** Six types, four deterministic and two judge-backed. There are no
custom-code graders.

| Type | Passes when |
|---|---|
| `regex` | A JavaScript regex matches the target; `not_contains` and `count:N` variants. |
| `tool_used` | Calls to a tool whose JSON input matches a regex fall between `min` and `max`. |
| `tool_order` | The first matching `before` call precedes the first matching `after` call. |
| `file_exists` | A file the agent *created* matches a glob. Files a scaffold made or the agent only edited never count. |
| `llm` | A judge votes PASS on the rubric in at least two of three votes. |
| `baseline` | A judge finds the run meets the criteria at least as well as a saved reference transcript. |

A grader looks at one of: the last message (default), the trace (a regex sees
every message; the judge sees the first 12 and last 12), the list of created
paths, the contents of one named file (images are shown to the judge, other
binaries refused), or the mocked MCP calls.

**Scoring.** A run's score is the weighted fraction of graders passed. A case's
score is the mean across its runs (default 3, up to 50). A case passes at
`--threshold`, default 1.0. In two-arm mode every `tool_used: Skill` grader and
every grader marked `arm: with-only` is excluded from the score in both arms
and reported with `scored: false`, because "counting it would push the
without-arm toward zero and inflate Δ". `arm: both` forces scoring. No
standard deviation, no confidence band, no significance test.

**Isolation.** Each run gets a fresh home, cwd, and configuration; user
settings, hooks, CLAUDE.md, MCP servers, other plugins, memory, and skills are
absent. The eval directory is hidden from the agent. Tool grants default to
read-only; `Bash`, `Write`, `Edit`, and web tools need `--allow-tools`, and a
granted `Bash` runs under Claude Code's OS sandbox. Hooks and real MCP servers
run unsandboxed as the user. The docs say outright that the isolation "isn't a
boundary against the plugin's own code."

**Cost and CI.** `--max-cost-usd` is checked before each run starts; hitting it
exits 2 with `partial: true` and a reason. `COST` is a list-price estimate.
`--concurrency` caps at 8. Exit codes are 0 pass, 1 fail or load error, 2
partial, 130 interrupted, 143 terminated. The suggested CI job pins both
`--model` and `--judge-model` "so a model rollout isn't mistaken for a plugin
regression."

**Authoring.** `claude plugin eval init` interviews the user, reads the plugin,
proposes prompts that should and should not trigger it, designs graders, pilots
each once, and writes the case directories.

## Side by side

| Axis | `claude plugin eval` | benchspec |
|---|---|---|
| Harnesses | Claude Code | `claude-code`, `codex`, `opencode` |
| Arms | with and without the plugin | Any list: harness, model, effort, env, provider per arm; optional baseline |
| Where the agent runs | Host, throwaway home and cwd | Docker container or microVM per cell, booted from a cached snapshot |
| Starting workspace | Empty; `scaffold_script` opt-in | `workspace/` seed copied into a clean room, every file hashed first |
| Per-arm setup | None; the plugin is loaded or not | `setup.sh` inside the guest with `BENCHSPEC_ARM` |
| Assertion authoring | Typed grader files with options | Prose `- [ ]` lines; the binder picks a checker or punts to the judge |
| Deterministic checks | regex, tool_used, tool_order, file_exists (created files only) | file_exists, not_file_exists, glob_count, regex, frontmatter_has, sha256_match, skill_invoked, not_skill_invoked |
| "Unchanged" claims | Not expressible | `sha256_match` against the pre-run hash |
| Judge evidence | Last message, or a window of the trace, or one file | Tree, contents, SHA-256s, final message, per-turn tool and skill activity, in one call per cell |
| Judge variance control | 2-of-3 vote per grader | One call; unparseable output retried once |
| Judge vendor | Any Claude model | Any harness and model, independent of the arms; cross-vendor recommended |
| Repeats | `runs`, default 3 | `--count N`, default 1 |
| Delta | Mean score difference per case and suite | Percentage points, pooled, with a noise band at N≥2 |
| Errored runs | `error` recorded; "a non-null error doesn't imply score 0" | Excluded from rates, counted and surfaced separately |
| Skill-trigger assertions in the baseline | Excluded from scoring by default | Counted, so the baseline can only fail them |
| Provenance | `claudeVersion` | Guest-probed agent version, snapshot, image digest, planned versus observed arms |
| Cost | USD estimate, `--max-cost-usd`, partial results | Token counts per sample; no ceiling |
| CI gate | `--threshold` on absolute score, exit codes | `--fail-under` on the raw delta per group |
| Reports | Terminal table, JSON, self-contained HTML, optional artifact publish | Terminal table, `benchmark.md`, `benchmark.json`, `meta.json`, `index.jsonl`, per-sample files |
| Authoring help | `eval init` interview | `lint` and `analyze` |
| Per-case config | runs, model, turns, timeout, tools, env | None; frontmatter allows only `history` |
| Mocks | MCP server mocks with fixed or agent-played answers and replay | None |
| Status | Experimental, server kill switch | Pre-1.0 |

## What this means for benchspec

**The simplest pitch is now a built-in.** A Claude Code user who wants to know
whether their plugin helps will reach for `claude plugin eval` first: no Docker
daemon, no Gemini key, one credential, an interview that writes the suite. The
README's headline example (baseline installs nothing, trial installs the
skill, read the delta) is that use case. benchspec's docs should say so and
stop competing on it.

**The step up is the matrix.** `plugin eval` has one comparison axis and one
harness. The moment the question becomes "sonnet versus opus", "Claude Code
versus Codex on the same skill", "high effort versus medium", or "this env
profile versus that one", `plugin eval` is two separate runs and a hand diff.
benchspec's arms are the product. Its `--models` sweep and mixed-harness sets
are exactly what `plugin eval` cannot do.

**Workspace fidelity is a real gap on their side.** `file_exists` counts only
files created during the run, and there is no way to assert that a seeded file
was left alone. benchspec seeds a workspace, hashes it, and grades against it.
Keep leaning on that; it is the reason the tagline is "what your agent does."

**Prose assertions cut both ways.** `plugin eval` makes the author pick a
grader type and options. benchspec makes the binder pick. Ours is nicer to
write and harder to reason about; theirs is more typing and fully predictable.
Not a change to make, but the docs should own the trade rather than only
selling the upside.

## Recommendations

### 1. Exclude skill-trigger assertions from the delta

`plugin eval` refuses to score `tool_used: Skill` in either arm by default
because the without-arm can never pass it. benchspec has the same problem and
does count it: in `src/benchspec/reporting/report.py`, `_arm_stats` sums every
assertion's `passed` into the pooled rate, and `grading.json` records
`skill_invoked` results as ordinary deterministic assertions. On the in-repo
`hello` eval, one of three assertions is `Skill \`hello\` invoked`, so the
baseline arm is capped at 67% before the agent does anything and the delta
carries a guaranteed +33pp from that line alone.

Options:

- **(a) Report trigger assertions separately.** When a baseline exists,
  assertions bound to `skill_invoked` or `not_skill_invoked` are excluded from
  every arm's pooled rate and shown in a per-arm "trigger rate" line and a
  `scored: false` field in `grading.json`. Matches `plugin eval` and keeps the
  headline delta honest. Recommended.
- (b) Exclude them only from the baseline arm. Simpler but asymmetric; the
  trial rate would still include a line the baseline cannot have.
- (c) Leave the rate alone and add a footnote. Cheapest, but the number in CI
  stays wrong.

Size S. One `checker` field is already present on each bound assertion in the
grading pipeline; the report needs to read it.

### 2. Plumb the agent-turn timeout

Each adapter's `invoke` defaults `timeout: int = 600`, and `_run` in
`src/benchspec/sandbox/sandbox.py` calls it without a timeout, so no
configuration reaches it. The judge timeout is configurable; the agent's is
not. `plugin eval` exposes both a wall-clock cap and a turn cap per case.

Options:

- **(a) Set-level and arm-level `timeout` in `[tool.benchspec.sets.*]`, with
  `--benchspec-agent-timeout` as the CLI override.** Follows the existing
  precedence chain. Recommended.
- (b) Per-eval `timeout` in frontmatter. Useful later, but opens the
  per-eval config surface that the format has kept closed on purpose. Defer.

Size S.

### 3. Make repeats the default story

`plugin eval` runs every case three times by default and says "one run of a
non-deterministic agent tells you little." benchspec's default is one sample,
the noise band only appears at two or more, and `--fail-under` gates on the
raw delta. A single-sample run with a gate is the most likely first CI setup
and the least trustworthy one.

Options:

- **(a) Quickstart and CI docs default to `--count 3`; the report and
  terminal summary print `single sample, no noise band` on any delta with
  n=1.** No behaviour change to the numbers. Recommended.
- (b) Change the default sample count to 3. Triples cost for everyone who did
  not ask; too aggressive while runs boot a 2 GB sandbox each.

Size XS.

### 4. Ship a benchspec skill for Claude Code

`plugin eval init` is the on-ramp: Claude reads the plugin, proposes prompts
and graders, pilots them, writes the files. benchspec's equivalent is a person
reading `writing-evals.md`. The eval format is Markdown with strict rules and
a documented decomposition discipline, which is exactly the kind of thing a
skill teaches well.

Options:

- **(a) Add `skills/benchspec/SKILL.md` to this repo, installable as a plugin,
  that teaches how to write an eval, decompose assertions so they bind, seed
  a workspace, write `setup.sh`, run `lint` and `analyze`, and read
  `benchmark.md`.** Distribution through the same channel `plugin eval` users
  already have. Recommended.
- (b) A `benchspec init` CLI interview that shells out to a harness. Heavier,
  duplicates what the skill does, and ties the CLI to one vendor's agent.

Size M, mostly writing. Bonus: the skill itself becomes a benchspec eval
target and a good dogfood suite.

### 5. Add `benchspec compare`

`plugin eval` aims at "catch regressions when you change the plugin or a new
model ships" and has a `baseline` grader that judges a run against a saved
transcript. benchspec has no run-to-run diff even though `index.jsonl` and
`benchmark.json` were built so external tooling could do one.

Options:

- **(a) `benchspec compare tmp/evals/iteration_03 tmp/evals/iteration_07`:
  same matrix shape, cell-by-cell delta, `config_hash` mismatch warning, the
  observed agent version from each `meta.json` on the header line.**
  Recommended.
- (b) A `baseline`-style judge grader against a reference transcript. Adds a
  judge call per assertion and a new artifact to curate; the matrix already
  holds the comparison.

Size M.

### 6. Reposition the README

One paragraph near the top: if you want to know whether one plugin helps
Claude Code, `claude plugin eval` is built in and needs one credential; use
benchspec when the question is which harness, which model, or which
configuration, when the task starts from real files, or when the check is what
the workspace looks like afterward. Honesty here costs nothing and the
comparison table above is the material.

Size XS.

## Not taking

- **MCP mocks.** Real capability, but benchspec's isolation story is the
  sandbox, and a mock layer is a second product. Revisit if an eval needs a
  tool the sandbox cannot reach.
- **Typed graders.** The binder-and-punt design is the thesis. Recommendation
  1 fixes the one place where a typed grader semantics was needed.
- **Host-side isolation.** `plugin eval` runs on the host because it can
  assume Claude Code's own sandbox. benchspec cannot assume a harness sandbox
  and should not.
- **HTML report and artifact publishing.** `benchmark.md` renders on GitHub.
  A viewer is a later polish item, not a lesson.
- **USD estimates and `--max-cost-usd`.** Three vendors, three price lists,
  and OpenCode reports no token split. Token counts per sample are honest;
  a USD number would not be. A ceiling on samples is not worth a knob yet.
- **2-of-3 judge voting.** Worth an experiment on the binder corpus style
  before adding a 3x judge bill; note it as a follow-up, not a change.

## Open questions

- Does `plugin eval`'s exclusion rule also apply to `not_skill_invoked`
  lines? Their docs only name `tool_used: Skill`. For benchspec the
  symmetric case (a `not invoked` line that the baseline trivially passes)
  inflates the baseline instead, so recommendation 1 should exclude both.
- `plugin eval` hides the eval directory from the agent. benchspec mounts the
  staged repo at `/project`, which includes the eval files. Whether the agent
  can read its own assertions is a fairness question worth a note in
  `sandbox.md` and possibly an exclusion in `sandbox/project.py`.
