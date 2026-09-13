# benchspec versus the alternatives

Research, 2026-09-13. A survey of the tools a benchspec-curious reader would
plausibly reach for instead, and the basis for the README's "benchspec vs. the
alternatives" table. Every claim here was checked against a primary source
(vendor documentation, package registry metadata, release pages) on that date
and re-verified by a second pass. Version numbers are a snapshot, not a
commitment; the README deliberately carries none. No code changes were made.

The companion note
[`2026-09-12-claude-plugin-eval-comparison.md`](2026-09-12-claude-plugin-eval-comparison.md)
goes deeper on `claude plugin eval` alone, including what benchspec should take
from it.

## Answer up front

Five tools are worth naming, and they are five different shapes:

1. **`claude plugin eval`** — the built-in. Free, zero install, and it does
   benchspec's own headline example. Everyone's first question is "isn't this
   already in the tool I have?", so it goes first.
2. **Inspect AI** — the most general of the frameworks. Runs CLI agents in
   Docker sandboxes through its agent bridge, seeds files per sample, grades
   the filesystem afterwards, judges with a model bound to a separate role, and
   reduces repeats with `pass@k`. You write Python.
3. **Harbor / Terminal-Bench** — the benchmark harness. Twenty-plus agents
   pre-integrated, eighty-plus ported benchmarks, many remote container
   providers. Answers "how does this agent rank", not "did my change help".
4. **Coder Eval** — the nearest like-for-like. Same shape as benchspec: five
   harnesses, Docker sandbox, seeded workspace, declarative YAML criteria with
   fractional credit, and repeats with bootstrap confidence intervals.
5. **promptfoo** — the biggest name, and the one whose mechanism differs most.
   Its own coding-agents guide states that the output under test is the agent's
   final text response, not the file contents.

None of the five takes a Markdown file of prose claims as its eval format. That
is the gap the README names — not capability, of which several of these have
more.

## Side by side

| Axis | `claude plugin eval` | Inspect AI | Harbor | Coder Eval | promptfoo | benchspec |
|---|---|---|---|---|---|---|
| Shipped by | Anthropic, inside Claude Code | UK AI Security Institute, with Meridian Labs | Harbor framework (Laude Institute lineage) | UiPath | promptfoo (OpenAI announced an agreement to acquire, 2026-03-09) | this repo |
| Unit of evaluation | One headless `claude -p` run | A `Sample` through a solver, scored | A task trial: container plus verifier script | A real agent run, graded on files and command output | A prompt and its reply | A `(eval × arm)` cell, graded on the workspace |
| Harnesses | Claude Code | Any CLI, via the sandbox agent bridge (Claude Code, Codex CLI, Gemini CLI documented) | 22 agents as of the Harbor Adapters paper, May 2026 | 5 (claude-code, codex, antigravity, opencode, pi) | Any, via a custom provider script | `claude-code`, `codex`, `opencode` |
| Arms | Two by default, with and without; `--ablation none` runs the with-arm alone. No third arm | Several models per run; no delta report documented | Separate runs, compared on a leaderboard | A/B experiments across agent, model, tools, prompt | Providers compared on identical cases | Any list; optional baseline, delta in percentage points |
| Isolation | Host, with a throwaway home, cwd and config. Granted `Bash` runs under Claude Code's own OS-level sandbox, which needs a backend (`bubblewrap` plus `socat` on Linux) or the run is refused | Docker and `local` built in, plus six external providers (k8s, Daytona, Modal, EC2, Proxmox, Vagrant) | Containers, with many remote providers | tempdir, Docker, or in-host | None of its own | Docker container or microVM per cell, from a cached snapshot |
| Seeded workspace | `scaffold_script`, run as you and outside the agent's sandbox, only under `--scaffold` | `Sample.files` plus an optional per-sample `setup` script | Files in `environment/` uploaded at start, when the task ships no Dockerfile | Yes | Not a filesystem model | `workspace/` copied into a clean room, every file hashed first |
| "Left unchanged" | No pre-run hash. `file_exists` sees created files only, so it can only be approximated by grading a file's contents after the run | A few lines of scorer Python | Hash it yourself in `test.sh` | No hash- or unchanged-based criterion among the 15 documented types | Not applicable | `sha256_match` against the pre-run hash |
| Assertion authoring | Six typed grader files | Python `Scorer` functions | `tests/test.sh`, usually pytest | Declarative YAML criteria with weights | A typed assertion DSL, plus JS/Python custom assertions | Prose `- [ ]` lines; the binder binds or punts |
| Judge vendor | Whatever `--judge-model` names, through the same credential and model provider as the arms | Any model bound to the `grader` role | Any LiteLLM model, overridable at invocation | Per-criterion `model:` on `llm_judge`, or a nested `agent.model:` on `agent_judge` | Per-assertion provider override | Any harness and model, independent of the arms |
| Repeats | 3 by default; 2-of-3 judge vote | `--epochs N` with `mean`/`median`/`pass@k` reducers | 3 trials in the Harbor Adapters paper's own evaluation | `repeats:` in an experiment YAML, or `--repeats N` | `repeat` in config, or `--repeat` on the CLI | `--count N`; single-sample deltas flagged in the report |
| Variance reported | No | Reducers, no band | Mean ± SEM in that paper; a 95% CI on the live Terminal-Bench leaderboard | count / mean / median / std / min / max, bootstrap CIs, and a paired mean-difference test on 2-variant experiments | No | Noise band on every delta at N≥2 |
| Errored vs failed | Error recorded; a non-null error is not score 0 | `--fail-on-error` threshold, `--retry-on-error`, `--score-on-error` | Not documented | `FAILED` and `ERROR` are distinct final statuses | Not modelled | Excluded from rates, counted and surfaced separately |
| Scoring | Mean score, 0–1 | Scorer-defined | Reward per trial | Continuous 0.0–1.0, fractional credit | Per-assertion pass/fail, weighted | Assertion pass rate per cell |
| Friction | None: built in, one credential | `pip install`, Docker, credentials | `pip install`, a container provider, credentials | `pip install`, optional Docker | `npx`, provider keys, no Docker | `pip install`, Docker, up to three vendors |

## Notes per tool

### `claude plugin eval`

Shipped in Claude Code v2.1.269 on 2026-09-11 and unchanged as of v2.1.270, the
current release; the 2026-09-12 note remains accurate. Two corrections to that
note: the public docs page no longer carries "experimental" framing — the word
survives only as the `experimental.evals` manifest key, itself documented as a
schema that may change between releases — and the server-side kill switch it
mentions is not documented today. Neither claim should appear in user-facing
docs.

Two details the first pass got wrong and the README must not repeat. It is not
unsandboxed: granted `Bash` runs under Claude Code's OS-level sandbox, and on a
machine with no backend the run is refused rather than run unconfined. And its
judge is not documented as a Claude model; what is documented is that it goes
through the same authentication and model provider as the session, which is the
real contrast with benchspec's cross-vendor judge.

The scoring detail worth borrowing is still the one tracked in #136: graders
only the with-arm can pass are excluded from scoring in both arms, because
"counting it would push the without-arm toward zero and inflate Δ".

### Inspect AI

The most general of the alternatives, with scale backends, `pass@k`, a log
viewer, and 200+ off-the-shelf evals — if the eval is Python you are willing to
write. The agent bridge routes a CLI agent's API calls through a proxy inside
the sandbox, which is how it drives Claude Code, Codex CLI and Gemini CLI.

It is not uniquely capable: Harbor also runs those three agents in containers,
seeds files, grades the filesystem, and takes an arbitrary judge model. What
separates Inspect is generality and the quality of its error handling, not a
capability the others lack.

No arms-versus-baseline delta report was found. `eval-set` runs several models
in one invocation and collects their logs, but documents no comparison or delta
output. That is an absence of evidence rather than evidence of absence, and the
README's claim is phrased as what benchspec gives you without assembling it,
which is safe either way.

### Harbor / Terminal-Bench

Terminal-Bench is now a dataset run on Harbor. The old `terminal-bench` package
is stale at 0.2.18 (2025-09-26); `harbor` is the live one.

Care with the statistics claim. Three trials with mean ± SEM is real, but it is
the methodology of the Harbor Adapters paper's own evaluation
(arXiv:2609.04298), not a Harbor default, and the live Terminal-Bench
leaderboard reports a 95% confidence interval instead. The agent and benchmark
counts are self-reported in that paper and dated "as of May 2026"; the docs'
own answer to "how many agents" is `harbor agent list`.

Two first-pass claims did not survive checking: Harbor's docs do not discourage
LLM judges — they present LLM and agent judges neutrally, noting only that
agent judges are slower and more expensive — and no separation of
infrastructure failures from model failures is documented. The paper's
timeout / near-miss / far-off taxonomy is a taxonomy of agent failure modes,
not of infrastructure.

### Coder Eval

First released 2026-07-09 and iterating fast: 30 PyPI releases by 2026-09-12.
The closest thing to a direct competitor found anywhere.

Its `/docs/criteria` and `/docs/experiments` pages 404, which led the first pass
to record four rows as "not documented". They are documented, at
`/docs/user-guide` and `/docs/task-definition-guide`: repeats via `repeats:` or
`--repeats N`, summary statistics with bootstrap confidence intervals and a
paired mean-difference test on two-variant experiments, a per-criterion judge
model, and `FAILED` versus `ERROR` as distinct final statuses. Its statistics
are stronger than benchspec's noise band, and the README must not claim
otherwise.

What it does not have is a hash- or unchanged-based criterion: fifteen criterion
types are documented and none of them compares against a pre-run state. Every
behavioural claim about this tool is version-fragile — four rows went stale in
the single day between the first pass and the second. Anonymous telemetry is on
by default.

### promptfoo

Alive and widely used; OpenAI's deprecation guidance for its own retiring Evals
platform points users here, so it is the default answer to "what eval tool
should I use" for a large population. OpenAI announced an agreement to acquire
promptfoo on 2026-03-09, subject to customary closing conditions; no primary
source confirms the deal has closed, and neither promptfoo's site nor its docs
describe it as an OpenAI company today. Both parties state it remains open
source.

The reason it is not compared mechanism-for-mechanism is stated in promptfoo's
own *Evaluate Coding Agents* guide: "The agent's output is its final text
response describing what it did, not the file contents." The same paragraph
offers routes around that — read the files after the eval, or enable tracing —
so the honest statement is that filesystem grading is not the default and not
part of the assertion layer, rather than that it is impossible. The README
contrasts on that axis in one line rather than running it down a table row by
row.

## Considered and left out

- **Braintrust**, **LangSmith**, **Langfuse** — experiment tracking and trace
  observability. No agent execution, no sandbox, no filesystem grading.
  Including one forces including the others.
- **OpenAI Evals** — dead twice over: the `openai/evals` repo was archived in
  2024, and the hosted platform goes read-only 2026-10-31 and shuts down
  2026-11-30.
- **HAL (Holistic Agent Leaderboard)** — `princeton-pli/hal-harness` was
  archived 2026-07-01.
- **SWE-bench** — a dataset with a runner. The unit is a patch validated
  against a fixed repo's tests, not a framework for your own tasks. Its
  `PASS_TO_PASS` / `FAIL_TO_PASS` split is good prior art for #136.
- **Vals.ai** — a vendor relationship, not something you install.
- **SkillsBench** (arXiv:2602.12670), **SkillTester** (arXiv:2603.28815), and
  similar academic suites — fixed task sets measuring skills in general, not
  tools for measuring yours. Worth noting that they independently converged on
  a paired with/without-skill baseline design.
- **DeepEval**, **Ragas**, **evalite** — prompt and response level.

## Sources

- [code.claude.com/docs/en/plugin-evals](https://code.claude.com/docs/en/plugin-evals)
  · [Claude Code CHANGELOG](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md)
- [inspect.aisi.org.uk](https://inspect.aisi.org.uk/) ·
  [sandboxing](https://inspect.aisi.org.uk/sandboxing.html) ·
  [agent bridge](https://inspect.aisi.org.uk/agent-bridge.html) ·
  [options](https://inspect.aisi.org.uk/options.html)
- [harborframework.com/docs/tasks](https://www.harborframework.com/docs/tasks) ·
  [agents](https://www.harborframework.com/docs/agents) ·
  [judge criteria](https://www.harborframework.com/docs/rewardkit/judge-criteria) ·
  [arXiv:2609.04298](https://arxiv.org/abs/2609.04298) — the three-trial mean ±
  SEM methodology · [tbench.ai](https://www.tbench.ai/) — the leaderboard's 95% CI
- [coder-eval.com/docs/user-guide](https://coder-eval.com/docs/user-guide) ·
  [task definition guide](https://coder-eval.com/docs/task-definition-guide) ·
  [github.com/UiPath/coder_eval](https://github.com/UiPath/coder_eval)
- [promptfoo.dev/docs/intro](https://www.promptfoo.dev/docs/intro/) ·
  [Evaluate Coding Agents](https://www.promptfoo.dev/docs/guides/evaluate-coding-agents/) ·
  [promptfoo on joining OpenAI](https://www.promptfoo.dev/blog/promptfoo-joining-openai/)
- [OpenAI deprecations](https://developers.openai.com/api/docs/deprecations) ·
  [princeton-pli/hal-harness](https://github.com/princeton-pli/hal-harness)
