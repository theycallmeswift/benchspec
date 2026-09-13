# benchspec versus the alternatives

Research, 2026-09-13. A survey of the tools a benchspec-curious reader would
plausibly reach for instead, and the basis for the README's "benchspec vs. the
alternatives" table. Every claim here was checked against a primary source
(vendor documentation, package registry metadata, release pages) on that date.
Version numbers are a snapshot, not a commitment; the README deliberately
carries none.

The companion note
[`2026-09-12-claude-plugin-eval-comparison.md`](2026-09-12-claude-plugin-eval-comparison.md)
goes deeper on `claude plugin eval` alone, including what benchspec should take
from it.

## Answer up front

Five tools are worth naming, and they are five different shapes:

1. **`claude plugin eval`** — the built-in. Free, zero install, and it does
   benchspec's own headline example. Everyone's first question is "isn't this
   already in the tool I have?", so it goes first.
2. **Inspect AI** — the serious general framework. The only other thing that
   runs Claude Code, Codex CLI and Gemini CLI in Docker sandboxes, seeds files
   per sample, grades the filesystem afterwards, and judges with a
   different-vendor model. You write Python.
3. **Harbor / Terminal-Bench** — the benchmark harness. Twenty-plus agent CLIs
   pre-integrated, eighty-plus ported benchmarks, remote container providers,
   and published mean ± SEM over three trials. Answers "how does this agent
   rank", not "did my change help".
4. **Coder Eval** — the nearest like-for-like. Same shape as benchspec, five
   harnesses, Docker sandbox, seeded workspace, declarative YAML criteria with
   fractional credit. Did not exist when benchspec started.
5. **promptfoo** — the biggest name, and the one whose mechanism differs most.
   Its own coding-agents guide states it grades the agent's final text
   response, not file contents.

None of the five reports an arms-versus-baseline delta matrix from prose
assertions. That is the gap the README names.

## Side by side

| Axis | `claude plugin eval` | Inspect AI | Harbor | Coder Eval | promptfoo | benchspec |
|---|---|---|---|---|---|---|
| Shipped by | Anthropic, inside Claude Code | UK AI Security Institute | Harbor framework (Laude Institute lineage) | UiPath | promptfoo (an OpenAI company since 2026-03) | this repo |
| Unit of evaluation | One headless `claude -p` run | A `Sample` through a solver, scored | A task trial: container plus verifier script | A real agent run, graded on files and command output | A prompt and its reply | A `(eval × arm)` cell, graded on the workspace |
| Harnesses | Claude Code | Any CLI, via the sandbox agent bridge (Claude Code, Codex, Gemini CLI documented) | 20+ pre-integrated | 5 (claude-code, codex, antigravity, opencode, pi) | Any, via a custom provider script | `claude-code`, `codex`, `opencode` |
| Arms | Two, fixed: with and without | Several models per run; no baseline-delta report | Separate runs, compared on a leaderboard | A/B experiments across agent, model, tools, prompt | Providers compared on identical cases | Any list; optional baseline, delta in percentage points |
| Isolation | Host, with a throwaway home and cwd | Docker, `local`, plus Kubernetes / Modal / EC2 / Daytona extensions | Containers, with many remote providers | tempdir, Docker, or in-host | None of its own | Docker container or microVM per cell, from a cached snapshot |
| Seeded workspace | `scaffold_script` opt-in, run outside the agent sandbox | `Sample.files` plus an optional per-sample `setup` script | Files in `environment/` uploaded at start | Yes | Not a filesystem model | `workspace/` copied into a clean room, every file hashed first |
| "Left unchanged" | Not expressible; `file_exists` sees created files only | A few lines of scorer Python | Hash it yourself in `test.sh` | Not documented | Not applicable | `sha256_match` against the pre-run hash |
| Assertion authoring | Six typed grader files | Python `Scorer` functions | `tests/test.sh`, usually pytest | Declarative YAML criteria with weights | A typed assertion DSL, plus JS/Python custom assertions | Prose `- [ ]` lines; the binder binds or punts |
| Judge vendor | A Claude model — always the same vendor as the arms | Any model bound to the `grader` role | Your choice; the docs discourage judges generally | Not documented | Per-assertion provider override | Any harness and model, independent of the arms |
| Repeats | 3 by default; 2-of-3 judge vote | `--epochs N` with `mean`/`median`/`pass@k` reducers | 3 trials, mean ± SEM in the published results | Not documented | `repeat` at three levels | `--count N`; single-sample deltas flagged in the report |
| Variance reported | No | Reducers, no band | Yes, SEM | Not documented | No | Noise band on every delta at N≥2 |
| Errored vs failed | Error recorded; a non-null error is not score 0 | `--fail-on-error` threshold, `--retry-on-error`, `--score-on-error` | Infrastructure failures separated from model failures | Not documented | Not modelled | Excluded from rates, counted and surfaced separately |
| Scoring | Mean score, 0–1 | Scorer-defined | Reward per trial | Continuous 0.0–1.0, fractional credit | Per-assertion pass/fail, weighted | Assertion pass rate per cell |
| Friction | None: built in, one credential | `pip install`, Docker, credentials | `pip install`, a container provider, credentials | `pip install`, optional Docker | `npx`, provider keys, no Docker | `pip install`, Docker, up to three vendors |

## Notes per tool

### `claude plugin eval`

Shipped in Claude Code v2.1.269 on 2026-09-11 and unchanged as of v2.1.270;
the 2026-09-12 note remains accurate. Two corrections to that note: the public
docs page no longer carries "experimental" framing — the word survives only as
a manifest key — and the server-side kill switch it mentions is not documented
today. Neither claim should appear in user-facing docs.

The scoring detail worth borrowing is still the one tracked in #136: graders
only the with-arm can pass are excluded from scoring in both arms, so a
skill-trigger assertion does not inflate every delta.

### Inspect AI

The strongest technical alternative, and the one to send readers to rather than
compete with. It has everything benchspec has mechanically, plus scale
backends, `pass@k`, a log viewer, and 200+ off-the-shelf evals — if the eval is
Python you are willing to write. The agent bridge routes a CLI agent's API
calls through a local proxy, which is how it drives Claude Code and Codex.

No arms-versus-baseline delta report was found. That is an absence of evidence
rather than evidence of absence; the README's claim is phrased as what
benchspec gives you without assembling it, which is safe either way.

### Harbor / Terminal-Bench

Terminal-Bench is now a dataset run on Harbor. The old `terminal-bench` package
is stale; `harbor` is the live one. The published results are the most
statistically honest in the survey: three trials per configuration with mean ±
SEM, and infrastructure noise separated from model failure. The agent-count and
benchmark-count figures are self-reported by the Harbor authors in
arXiv:2609.04298.

### Coder Eval

First released 2026-07-09 and iterating fast. The closest thing to a direct
competitor found anywhere, and the reason the README names it rather than
leaving it out. Its `/docs/criteria` and `/docs/experiments` pages returned 404
during this survey, so only what its README and PyPI summary state is cited
here: an unchanged-file criterion, judge vendor configurability, and
repeated-run handling could not be confirmed either way. Anonymous telemetry is
on by default.

### promptfoo

Alive and widely used; OpenAI's deprecation guidance for its own retiring Evals
platform points users here, so it is the default answer to "what eval tool
should I use" for a large population. The disqualifier for a mechanism
comparison is stated in promptfoo's own *Evaluate Coding Agents* guide: the
agent's output under test is its final text response describing what it did,
not the file contents. The README therefore contrasts on that axis only, in one
line, rather than running it down a table row by row.

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
  [tbench.ai Terminal-Bench 4.0](https://www.tbench.ai/news/terminal-bench-4-0) ·
  [arXiv:2609.04298](https://arxiv.org/abs/2609.04298)
- [coder-eval.com](https://coder-eval.com/) ·
  [github.com/UiPath/coder_eval](https://github.com/UiPath/coder_eval)
- [promptfoo.dev/docs/intro](https://www.promptfoo.dev/docs/intro/) ·
  [Evaluate Coding Agents](https://www.promptfoo.dev/docs/guides/evaluate-coding-agents/) ·
  [OpenAI acquisition](https://openai.com/index/openai-to-acquire-promptfoo/)
- [OpenAI deprecations](https://developers.openai.com/api/docs/deprecations) ·
  [princeton-pli/hal-harness](https://github.com/princeton-pli/hal-harness)
