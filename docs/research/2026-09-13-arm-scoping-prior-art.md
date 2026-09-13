# Scoping an assertion by arm: prior art

Research, 2026-09-13. How test languages, build systems, config formats, and
LLM eval frameworks say "this check applies only under configuration X",
surveyed to settle the syntax and semantics filed as #136. Companion to
[`2026-09-12-claude-plugin-eval-comparison.md`](2026-09-12-claude-plugin-eval-comparison.md),
which carries the decision; this note carries the evidence. No code changes
were made.

## What the survey settled

- **Granularity.** No BDD dialect tags a step. Cucumber: "It is not possible
  to place tags above Background or steps", and on conditional steps, "you
  probably have an anti-pattern." The only per-assertion scoping among
  general frameworks is JUnit's `assumingThat`. benchspec is a matrix, not
  a scenario, so the BDD refusal does not transfer.
- **Key.** The two precedents with a runtime environment, Karate and Behave,
  both chose key=value predicates over free tags. benchspec's arm names come
  from `pyproject.toml` and `--models` sweeps, so keying on properties keeps
  an eval valid across sets.
- **Placement.** A key of the item (Ansible `when:`, Actions `if:`, promptfoo
  `providers:`) beats a trailing token on the line (TAP `# SKIP`, Obsidian
  `[key:: value]`, todo.txt `+tag`) for this codebase, because the assertion
  text then reaches the binder and judge untouched.
- **Semantics.** Four exclusion semantics are kept distinct everywhere: filter
  (absent from the report), skip (present, marked), unscored (graded, out of
  the aggregate), expected failure. Rust counts `ignored` and `filtered out`
  separately; Robot documents `--exclude` against `--skip`; TAP has SKIP and
  TODO. A scoped benchspec line is skipped where it does not apply and
  unscored where it does, both outside the pooled rate.
- **Inflation.** Two eval frameworks solve it directly. `claude plugin eval`
  excludes `tool_used: Skill` and `arm: with-only` graders from the score in
  both arms. SWE-bench keeps `PASS_TO_PASS` and `FAIL_TO_PASS` as separate
  ratios and never averages them. Inspect AI separates "not applicable"
  (`return None`) from "no verdict" (`Score.unscored()`).

## Placement versus key

|  | Block level (above or around a group) | Item level (on the line, or a key of the item) |
|---|---|---|
| **By name** (tags, arm names, config labels) | Cucumber `@tag`, Gauge `Tags:`, pytest `-m`, Sphinx `only:: html`, Go `//go:build` | Bazel `select({":arm": …})`, Playwright `{ tag: '@fast' }`, plugin eval `arm: with-only`, promptfoo `providers:`, `pytest.param(marks=)` |
| **By property** (env value, OS, matrix field, any variable) | Karate `@env=dev,qa`, Behave `@use.with_os=win32`, Playwright `test.skip(({browserName}) => …)`, Rust `cfg_attr(target_os, ignore)` | JUnit `assumingThat(cond, () -> …)`, Ansible `when:`, Actions `if: matrix.x`, Robot `Skip If`, pytest `skipif(expr)`, **benchspec `if:` / `unless:` (#136)** |

Item-level scoping by property is where the general-purpose frameworks ended
up, and only JUnit does it for a single assertion inside a larger test.

## Exclusion semantics

| Semantics | Who uses it | In the per-arm rate | Visible per arm | For a scoped benchspec line |
|---|---|---|---|---|
| filter | Cucumber, Gauge, Bazel, Sphinx, promptfoo | no | no | Wrong: a cell that silently loses rows is what the roster exists to prevent. |
| skip | Karate, Behave, Robot, pytest, Playwright, Inspect `None` | no | yes, as skipped | Right where the clause is false. Recorded in `grading.json`; the matrix stays complete. |
| unscored | plugin eval `scored: false`, DeepEval `flaky`, SWE-bench P2P/F2P | no | yes, graded | Right where the clause is true. Graded, shown in a scoped table, kept out of the pooled rate so denominators match. |
| expected fail | pytest `xfail`, Playwright `fail`, TAP TODO, CTest `WILL_FAIL` | inverted | yes | Not this problem. |

## Survey

Each entry: what unit is scoped, what the scope keys on, the effect, and
where the annotation sits. Snippets are verbatim from the linked source.

### BDD languages

**Cucumber / Gherkin.** Feature, Rule, Scenario, Examples. Free tags; boolean
tag expressions at run time (`--tags "@smoke and not @wip"`). Filter. Line
above the block, inherited down. "It is not possible to place tags above
Background or steps." FAQ on conditional steps: "Each scenario should test one
thing and fail for one particular reason. This means there should be no
reason to skip steps." Sources:
https://cucumber.io/docs/cucumber/api/?lang=java#tags ·
https://github.com/cucumber/tag-expressions · https://cucumber.io/docs/faq/

**Karate.** Scenario, Examples table. `@env=dev,qa` and `@envnot=perf,prod`
against `karate.env`; comma is OR. Skip. Line above the block. Tagged
`Examples` tables partition inside one Scenario Outline, and "there is no
concept of a default." Inline `* if (cond) karate.abort()` exists and is
documented as "use sparingly". Source:
https://github.com/karatelabs/karate/blob/v1.4.1/README.md#environment-tags

**Behave active tags.** Feature, Rule, Scenario. `@use.with_os=win32`,
`@not.with_browser=safari`, evaluated against a runtime dict; "A sequence of
active tags is enabled, if all its active tags are enabled (logical-and
operation)"; unknown categories are ignored. Skip. Line above the block.
Source: https://behave.readthedocs.io/en/latest/new_and_noteworthy_v1.2.5/

**Robot Framework.** Test case. `[Tags]    robot:skip` as a setting line
inside the body; `Skip If    ${cond}` per keyword. Filter, skip, and expected
failure (`robot:skip-on-failure`). "With `--exclude` tests are omitted from
the execution altogether and they will not be shown in logs and reports. With
`--skip` they are included, but not actually executed, and they will be
visible." Source:
https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html

**Gauge, SpecFlow/Reqnroll, pytest-bdd.** Spec or scenario; free tags; filter
(`@ignore` skips). No step-level tags in any of them. Sources:
https://docs.gauge.org/writing-specifications ·
https://docs.reqnroll.net/latest/gherkin/gherkin-reference.html ·
https://github.com/pytest-dev/pytest-bdd

### Test frameworks and build systems

**pytest.** Test, or one parametrized case. `@pytest.mark.skipif(expr,
reason=…)`, `@pytest.mark.xfail`, `pytest.param(…, marks=pytest.mark.xfail)`.
Skip and expected failure. Decorator above the item; wrapper around the row.
Source: https://docs.pytest.org/en/stable/how-to/skipping.html

**JUnit 5 `assumingThat`.** A group of assertions inside one test.
`assumingThat("CI".equals(System.getenv("ENV")), () -> { assertEquals(…); });`
with the rest of the test still running and counting. Skip. The only
sub-test scoping in the survey. Source:
https://docs.junit.org/6.1.1/writing-tests/assumptions.html

**Playwright.** Test or describe. `test.skip(({ browserName }) => browserName
!== 'chromium', 'Chromium only!')` reads the project through fixtures;
`test('…', { tag: ['@slow'] }, …)` plus `--project` and `--grep-invert`.
Skip and filter. Source: https://playwright.dev/docs/test-annotations

**Bazel `select()`.** One attribute value keyed by named `config_setting`
with `//conditions:default`. Filter. Source:
https://bazel.build/docs/configurable-attributes

**Rust `cfg_attr`.** `#[cfg_attr(target_os = "windows", ignore)]`: a condition
that toggles another annotation. The summary line counts `ignored` and
`filtered out` separately. Source:
https://doc.rust-lang.org/reference/conditional-compilation.html

**TAP directives.** One test point. `ok 14 - mung the gums # SKIP reason` and
`not ok 15 # TODO`. "Harnesses must not treat failing SKIP test points as a
test failure." Trailing on the line. Source:
https://testanything.org/tap-version-14-specification.html

**Ansible `when:` and GitHub Actions `if:`.** Task or step. A sibling key on
the item, expression over facts or `matrix.*`; `continue-on-error: ${{
matrix.experimental }}` is run-but-not-counted per arm. Skip. Sources:
https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_conditionals.html ·
https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/using-conditions-to-control-job-execution

**Sphinx `only::`.** Indented block keyed by named build tags, `.. only::
html and draft`. Filter. The one conditional block inside a document format;
GFM, MyST, and mdBook have no equivalent. Source:
https://www.sphinx-doc.org/en/master/usage/restructuredtext/directives.html

### Metadata on a checklist line

**Obsidian Tasks.** `- [ ] #task Has a due date [due:: 2023-04-16]`; "Tasks
reads task lines backwards from the end of the line, looking for metadata."
Description first, fields last. Source:
https://publish.obsidian.md/tasks/Reference/Task+Formats/Dataview+Format

**todo.txt.** `(B) Schedule Goodwill pickup +GarageSale @phone due:2010-01-02`;
sigil-prefixed tokens anywhere in the line, `key:value` for extensions.
Source: https://github.com/todotxt/todo.txt

### LLM and agent eval frameworks

**Claude Code `plugin eval`.** One grader file; frontmatter `arm: with-only`
or `both`; every `tool_used: Skill` grader unscored by default. "A check like
'the skill was invoked' can never pass without the plugin, so counting it
would push the without-arm toward zero and inflate Δ. To keep the two arms
comparable, Claude Code excludes such graders from the score in both arms
and reports them in the with-arm as pass/fail indicators only." If every
grader is excluded, they are scored normally. Under `--ablation none`
nothing is excluded. Source: https://code.claude.com/docs/en/plugin-evals

**SWE-bench.** Test id in two lists. `FAIL_TO_PASS` is the lift;
`PASS_TO_PASS` is "tests that should pass before and after", a regression
gate held at 100%. Resolution is FULL only when both are 1. Inflation is
avoided by never putting the two in one mean. Source:
https://github.com/SWE-bench/SWE-bench/blob/main/swebench/harness/grading.py

**Inspect AI.** Scorer × sample. `return None` means out of scope, no entry,
no coverage count; `Score.unscored(reason=…)` means expected a verdict and
got none, NaN excluded from metrics, counted as a gap. `aggregate(…,
on_missing="error"|"skip"|"zero")` makes the choice explicit per metric.
Source: https://inspect.aisi.org.uk/custom-scorers.html

**promptfoo.** Test case only: `providers: [fast-model]` filters a whole test
to some providers; no per-assertion provider key exists; `weight: 0`
auto-passes, so it is not an indicator. Source:
https://www.promptfoo.dev/docs/configuration/test-cases/

**skill-creator.** No declaration; the analyzer discovers "always pass in
both configurations" and "always pass with skill but fail without" from the
data afterwards, and its own example suite contains an unmarked skill-only
expectation. Source:
https://github.com/anthropics/claude-plugins-official/blob/main/plugins/skill-creator/skills/skill-creator/agents/analyzer.md

**DeepEval, Braintrust, LangSmith, Ragas, lm-evaluation-harness, HELM,
Harbor.** No arm concept. DeepEval's `flaky=True` is reported-not-gating per
metric; Braintrust and LangSmith skip by returning `None` or omitting a key;
lm-eval and HELM declare per-metric aggregation but compare runs by diffing
results; Harbor tasks emit one scalar reward.

## Candidates considered

Four shapes were laid over the same eval; the decision and the final grammar
are in the companion note and #136.

1. Trailing arm selectors, `- [ ] … @trial @trial-overrides`. Lineage:
   Cucumber `@tag`, Playwright `{ tag }`, todo.txt. Couples the eval to a
   set's arm names.
2. Trailing property predicate, `- [ ] … @if GREETING_LOCALE=en-GB`.
   Lineage: Karate, Behave, TAP. Has to be stripped from prose before the
   binder's regex-drift guard and the judge see the line.
3. Branch blocks, `## Assertions if …`. Lineage: Karate tagged Examples,
   Sphinx `only::`. The only shape the BDD family endorses; a one-off line
   needs its own section.
4. A sub-bullet under the assertion, `- if: {GREETING_LOCALE} == "en-GB"`.
   Lineage: Ansible, Actions, promptfoo, plugin eval frontmatter. Lands in
   syntax the parser already rejects; the assertion text is never touched.
   Chosen.
