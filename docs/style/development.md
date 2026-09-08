# Code style

Personal, language-agnostic code style. Examples are Python; the principles
apply anywhere.

Tiebreaker when two good goals conflict: **self-documenting, readable code
wins.** A reader should never be surprised.

## Core values

- **Self-documenting above all.** The code carries the meaning. Reach for a
  clearer name or smaller unit before a comment.
- **Readability trumps compactness.** Use vertical whitespace to separate
  logical beats — inside functions, between guard clauses and the return.
  Never compress at the cost of clarity.
- **Rigor scales with lifespan.** Maintained code meets the full standard
  below. Genuine throwaway code may relax *typing and tests* only — everything
  else still holds.

## Comments and documentation

- **Inline comments earn their place only two ways:**
  - The **why**, never the **what**. Explain intent or rationale; never
    restate the code.
  - **Landmine warnings** — ordering, side-effect, or keep-in-sync traps a
    reader could miss.
- **Section-header comments are a smell.** Wanting `# ---- parsing ----` means
  the region should be extracted into its own named unit.
- **Never comment to skip a lint or type check.** No `# noqa`, no
  `# type: ignore`, no `# ty: ignore`, no per-file disables — a suppression is a
  smell. Fix the underlying issue; if the rule is wrong, change the linter
  config, not the code.
- **Comments never reference external context.** No PR/issue/commit references
  (`# fixes #123`), no "added for X" provenance, no caller notes
  (`# called from server.py`), no pointers to research notes or specs. That
  history lives in git and the tracker; in source it rots the moment plans
  change.
- **Docstrings on everything** — every module, class, and function, including
  tiny private helpers. **Google style** (`Args:` / `Returns:` / `Raises:`).
  One line for trivial helpers; full sections when there's a real contract to
  describe.

```python
def provision(image: str, cpus: int) -> Sandbox:
    """Start a sandbox for the given image.

    Args:
        image: OCI image reference.
        cpus: vCPU count.

    Returns:
        A running Sandbox.

    Raises:
        SandboxError: if provisioning fails.
    """

def _slugify(name: str) -> str:
    """Convert a display name to a URL slug."""  # trivial helper: one line
    return name.lower().replace(" ", "-")
```

```python
# GOOD (why): retry twice — the upstream API 500s on cold start
# WARNING: must run before init() — sets the env var init() reads at import time
```

## Naming

- **Always fully descriptive. No single-letter names.**
- `_` is allowed for a genuinely unused value.
- Even lambdas and comprehensions use full names.

```python
sorted(users, key=lambda user: user.name)   # not lambda u
[user.name for user in users if user.active]
retry_budget = 3
for _ in range(retry_budget): ...            # unused -> _
```

## Structure and organization

- **Split by responsibility/cohesion, never by line count.** A long function
  doing one clear thing stays; a short one doing two things gets split. No
  size caps.
- **Package by feature** (`sandbox/`, `binder/`, `runner/`), not by layer
  (`models/`, `utils/`).
- **Imports at the top of the file.** Absolute (`from benchspec.sandbox import
  provision`), never relative. Grouped and ordered stdlib → third-party →
  local, linter-enforced.
- **Paradigm-agnostic.** Functions, classes, or procedural code per the shape
  of the problem — functional pipelines for transforms, classes for stateful
  services, procedural for scripts.
- **No mutation/immutability dogma.** Pick whichever reads clearest here; just
  don't surprise-mutate a caller's arguments.

## Errors and observability

- **Fail fast and loud.** Validate at boundaries, raise early with a clear
  message, don't catch what you can't handle. No swallowing, no silent
  fallbacks, no `return None` on error.
- **Except at the user-facing boundary.** The outermost layer (e.g. the CLI)
  catches, prints a clean error plus non-zero exit code, and shows the full
  traceback only under `--debug`/`-v`.

  ```python
  # internals
  if not config.token:
      raise ValueError("SANDBOX_TOKEN is required")

  # cli top level
  except BenchspecError as error:
      print(f"error: {error}")
      sys.exit(1)
  ```

- **Logging is minimal.** Prefer a clear exception with context over a log
  line; log only what a failure won't otherwise surface.
- **User-facing progress output is a separate concern** from internal
  logging. Progress and results (`3/10 evals passed…`) go through the
  reporter/stdout, held to a UX bar — not `log.debug`.

## Design

- **DRY early.** Extract on the *second* copy for one source of truth. If the
  copies later diverge in reason-to-change, that's the signal to un-DRY them —
  divergence is a future problem, duplication the present risk.
- **Keyword-first signatures, few args.** Group related params into a
  config/dataclass object once they grow.

  ```python
  provision(image=img, cpus=2, memory_mb=512)
  provision(SandboxConfig(...))   # many params -> one object
  ```

- **Name reused or tunable constants**; leave obvious one-off literals inline.

  ```python
  MAX_RETRIES = 3        # reused + tunable -> named
  return text[1:]        # obvious slice -> inline
  ```

## Typing

- **Maintained code:** fully typed, checked by [ty](https://docs.astral.sh/ty/)
  as part of `make lint` and CI.
- **Annotations name the real type.** An `object` (or `Any`) written only to
  satisfy the annotation linter is a suppression by another name: it passes the
  linter while telling the checker nothing. Reach for a `Protocol`, a `TypeVar`,
  or a union instead, and let a test double satisfy the protocol structurally.
- **Genuine throwaway code:** typing is optional.

## Tests

I write all three tiers; each earns its place:

- **Unit** — fast. Ensure things behave as expected in a controlled
  environment.
- **Integration** — fast. Ensure systems talk to each other as expected.
- **E2E** — primarily the happy path. Confirms the system works for real.

**Mock only the painful edges** — network, time, paid APIs, the sandbox. Use
real domain objects everywhere they're cheap, so a behavior-preserving
refactor keeps tests green.

**Tests are DAMP, not DRY.** The `DRY early` rule is for production code; test
code wants Descriptive And Meaningful Phrases. Each test reads top-to-bottom
as its own small story — state, action, result — even at the cost of some
duplication. Rule of thumb: **DRY the plumbing (object builders, assertion
helpers, boilerplate), DAMP the story** (the input that triggers the behavior
and the value you assert stay inline and visible). Don't hide cause-and-effect
behind a shared fixture.

**Four-phase layout** (setup, exercise, verify, teardown), phases separated by
blank lines so the reader sees at a glance where the system under test is
invoked and what's checked. Teardown is usually a no-op the framework handles.
The blank lines are load-bearing; phase comments are optional. Skip separators
only for trivial tests (one setup line, one exercise-and-assert); anything past
~5 lines uses the layout. One exercise per test.

```python
def test_multi_sample_reports_zero_stdev(tmp_path):
    iteration = tmp_path / "iteration-1"
    iteration.mkdir()
    seed_arm(iteration, "alpha", passes=2, total=2, sample=0)
    seed_arm(iteration, "alpha", passes=2, total=2, sample=1)

    benchmark = report.build_benchmark(iteration, label="iteration_01 · alpha")

    assert benchmark["arms"]["alpha"]["pass_rate"] == 1.0
    assert benchmark["arms"]["alpha"]["pass_rate_stdev"] == 0.0
```

## Tooling and version control

- **Linter/formatter-driven.** Defer to the tool; encode rules in the linter
  rather than policing by hand.
- **Conventional-commit messages** (`feat:` / `fix:` / `chore:`).
- **Rebase workflow; curate into a few well-scoped commits.** Interactively
  rebase a branch into a few cohesive commits (each a self-contained,
  reviewable change), then fast-forward onto `main` (`git merge --ff-only`).
  Avoid merge commits. Don't squash a branch to a single commit by default —
  the *commit* is the unit of history, and a well-scoped commit tells its own
  story.

## Project documentation

- A strong **README** as the front door (orientation + quickstart), plus a
  focused **`docs/`** for real subsystems and decisions (ADRs).
- If a doc drifts from the code, **fix it or delete it** — a doc that lies is
  worse than none.

## Python

The only language-specific section; everything above is language-agnostic.

- **`from __future__ import annotations` is the first import**, right after the
  module docstring. Consistent across every file, so new modules never have to
  think about it.
- **Indented multi-line strings use `textwrap.dedent`.** Never bake leading
  whitespace into a raw triple-quoted string. The `\` after the opening `"""`
  suppresses the leading newline; the closing `"""` sits on its own line for a
  clean trailing newline. Module-level template constant for repeated or
  substituted strings; inline `dedent` for short, single-use ones.

```python
from __future__ import annotations

from textwrap import dedent

prompt = dedent("""\
    Summarize this in one sentence.
    Be specific.
""")

# Wrong — indentation leaks into the string
prompt = """
    Summarize this in one sentence.
"""
```

## Non-goals

- No arbitrary size limits on functions or files.
- No comments that restate the code; no section-header comments.
- No relative imports; no wildcard imports.
- No mutation/immutability dogma.
- No chatty internal logging.
