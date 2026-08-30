# Concepts

This page is the vocabulary: one short paragraph per term, in roughly the order
you meet them, each with a one-line example and a link to the document that goes
deepest. Read it once before the [quickstart](quickstart.md), or come back when a
term in another page is unfamiliar.

## Eval

One task and what success looks like: a prompt the agent receives, an optional
`history:` of prior turns, and a checklist of plain-prose **assertions**. An eval
is one Markdown file, `eval.md` or `<stem>.eval.md`, and its id is the folder
name or the file stem. Example: `evals/hello/greets-by-name.eval.md` asks the
agent to "Greet Alice by name" and asserts that `./Greetings/Alice.md` contains
`Hello, Alice!`. Format reference: [writing-evals.md](writing-evals.md).

## Assertion

One `- [ ]` line in an eval: a single claim about the final workspace, the
agent's final message, or what the agent did. Assertions are prose, not checker
syntax; how each one is graded is decided at run time (see **binder**).
Example: `- [ ] ./Greetings/Alice.md contains the exact line 'Hello, Alice!'`.

## Group

The folder an eval file sits in. Its name is the first half of an eval's
identity, `(group, eval_id)`, and the artifact tree is keyed on it. Sibling
`<stem>.eval.md` files in one folder share that folder's `workspace/` and
`setup.sh`. Example: `evals/hello/greets-by-name.eval.md` is eval
`greets-by-name` in group `hello`, and pytest names its cells
`test_eval[hello-greets-by-name-<arm>]`.

## Harness

The coding-agent CLI under test: `claude-code`, `codex`, or `opencode`. Each is
one adapter that knows how to install the CLI into a microVM, which credential it
needs, how to run it headless, and how to read its output stream. Chosen per arm.
Details and how to add one: [harnesses.md](harnesses.md).

## Arm

One column of the benchmark: a named configuration of harness, model, effort,
environment variables, and pass-through CLI arguments. Arms differ from each
other only in that configuration and in what their `setup.sh` branch installs.
Example: `{ name = "trial", model = "opus", effort = "high" }`. Reference:
[configuration.md](configuration.md).

## Eval set

A named benchmark declared in `pyproject.toml`: its arms, the defaults they
inherit, and an optional baseline. One run resolves exactly one set. Example:
`[tool.harnessbench.sets.default]` with arms `baseline` and `trial`. Reference:
[configuration.md](configuration.md#eval-sets).

## Baseline

The arm every other arm's delta is measured against. With a baseline, the report
headline is a percentage-point delta (`+14pp`); without one, arms report absolute
pass rates. Example: `baseline = "baseline"`, an arm whose `setup.sh` installs
nothing. Reading deltas: [results.md](results.md).

## Cell

One `(eval × arm)` pair. Each cell becomes one parametrized pytest test that
boots its own microVM, runs the agent, and grades the result. A two-eval,
three-arm set has six cells. Example: `test_eval[hello-greets-by-name-trial]`.

## Sample

One execution of a cell. A plain run takes one sample per cell (`sample-0/`);
`--count N` takes N, so the report can show flakiness and a noise band on each
delta. Example: `harnessbench run -- --count 5`. See
[results.md](results.md#noise-samples-and-flakiness).

## Iteration

One run's artifact tree, `tmp/evals/iteration_NN/`, numbered once per run and
shared by every cell in it. It holds `benchmark.md`, `benchmark.json`,
`meta.json`, `index.jsonl`, and every sample's per-cell files. Reference:
[results.md](results.md).

## Sandbox and snapshot

Every cell runs inside a microVM, a small hardware-isolated virtual machine
booted from a **snapshot**: a sealed image with the base OS, the harness CLI,
and any suite-wide tools already installed. Snapshots build once per
configuration and are cached; cells boot from them in seconds. Reference:
[sandbox.md](sandbox.md).

## Clean room and `/workspace`

The clean room is a fresh temporary directory on the host, seeded from the
eval's `workspace/` folder (or empty), that is mounted read-write into the
microVM at `/workspace`. It is the agent's working directory, and the host
grades the same directory afterward. Every path in an eval is written
`./`-relative to it. Example: an eval with `workspace/request.md` starts the
agent in a directory containing exactly `request.md`.

## `/project`

A read-only mount of a staged copy of your repository (what a `git clone` would
contain, minus `.env` files, `.git`, and `tmp/`). It exists so the eval's own
`setup.sh` can copy the skill under test into the guest. The agent can read it
too, so keep prompts pointed at `./`. Details:
[sandbox.md](sandbox.md#what-project-contains).

## `setup.sh`

An optional script beside the eval file that runs inside the microVM before the
prompt, with `HARNESSBENCH_ARM` set to the arm's name. It is the one place arms
diverge: the canonical script installs a skill on `trial` and exits early on
`baseline`. Reference:
[writing-evals.md](writing-evals.md#setupsh-what-differs-per-arm).

## Skill

An instruction file (`SKILL.md`) an agent can load and dispatch; in the common
"capability lift" benchmark, the skill is the thing under test. Skills install to
the fixed guest path `/home/harnessbench/skills`, which every harness's native
skill directory links to. Example assertion: `` - [ ] Skill `hello` invoked ``.

## Binder

A fixed, deliberately conservative classifier (one call to
`gemini-3.5-flash-lite` per assertion, which is why `GEMINI_API_KEY` is always
required) that decides, at grade time, whether an assertion can be checked
mechanically. It either **binds** the line to one checker or **punts** it to the
judge. It never grades anything itself. Reference:
[writing-evals.md](writing-evals.md#how-assertions-are-graded).

## Checker

One deterministic check run on the host against the final workspace or the
run's process facts: `file_exists`, `not_file_exists`, `glob_count`, `regex`,
`frontmatter_has`, `sha256_match`, `skill_invoked`, `not_skill_invoked`. Zero
variance, zero judge cost. Example: "./out/report.md exists" binds to
`file_exists`.

## Judge

The LLM that grades every punted assertion, from evidence only: the workspace
tree, file contents and SHA-256s, the agent's final message, and the tools and
skills it invoked. The judge runs on the host through one of the same harness
adapters, is configured once per run, and is independent of the arms. Example:
`[tool.harnessbench.judge] harness = "codex"`. Reference:
[configuration.md](configuration.md#the-judge).

## Errored versus failed

A **failed** assertion is a measurement: the agent ran and the claim did not
hold. An **errored** sample is infrastructure: the CLI crashed or timed out,
`setup.sh` exited non-zero, or the judge failed at the transport level. Errored
samples are excluded from pass rates but counted in the report, so a
half-crashed run cannot read like a clean one. See
[results.md](results.md#noise-samples-and-flakiness).
