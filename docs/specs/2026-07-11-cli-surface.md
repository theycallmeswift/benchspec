**TL;DR** — Promote the pytest-and-Make-only run path into four first-class `evalspec` subcommands — `lint`, `analyze`, `sandbox:build`, `run` — with a curated flag surface that translates to the plugin's `--evalspec-*` options, a wrapper over `sandbox.cli_build` (no duplicate build path), and a documented exit-code contract. Phase 5 of the #1 roadmap. `lint` and `analyze` already exist; this phase adds `run` and `sandbox:build` and unifies conventions.

## Problem

- **Symptom:** The CLI exposes only two of the four promised commands. `__main__.main` dispatches `{"lint": lint.run, "analyze": analyze.run}` (`__main__.py:23-40`); `evalspec run` and `evalspec sandbox:build` do not exist, though the vision names all four (`evalspec-readme-vision.md:263-280`).
- **Symptom:** Running a benchmark is a raw pytest invocation, not a product surface. `make evals` is `pytest -m binder_corpus -n … evals/binder` (`Makefile:16`); output evals rely on the plugin self-registering `cases.py` so a run is `pytest -p evalspec.plugin` plus flags (`plugin.py:370-373`, `cases.py:103-104`). A user must know the plugin name, the `--evalspec-*` option spellings (`plugin.py:47-186`), and pytest's own conventions to run one eval set.
- **Symptom:** The snapshot build machinery exists but is unreachable from `evalspec`. `sandbox.cli_build()` (`sandbox.py:605-615`) preflights, resolves the agent + environment, and builds the snapshot if missing — but it is wired only to `make evals:build`, not to a CLI command. The roadmap wants `evalspec sandbox:build` to use exactly this machinery, not a second copy.
- **Symptom:** There is no exit-code contract for a run. `lint.run`/`analyze.run` return `0`/`1` (`lint.py:79-90`, `analyze.py:73-101`), but a benchmark run's status (infra failure vs. `--evalspec-fail-under` gate vs. no-evals-collected) is whatever pytest's raw exit code happens to be, undocumented.
- **Exposed by:** Roadmap #1 Phase 5 Done-When: `evalspec lint`, `evalspec analyze`, `evalspec sandbox:build`, and `evalspec run` exist with useful exit codes; `sandbox:build` uses the existing snapshot build machinery rather than duplicating it.
- **Scope:** Add `run` and `sandbox:build` subcommands; give all four a shared arg/exit-code convention; translate a curated CLI flag set to the plugin's `--evalspec-*` options. **Not** new runtime behavior — discovery, grading, arms, judge, and sandbox stay as-is.
- **Constraint:** No compatibility shims (roadmap rule). `run` wraps the existing plugin; `sandbox:build` wraps `cli_build`; no parallel run/build implementation is introduced.
- **Constraint:** `evalspec run` and `sandbox:build` must not re-derive config. They pass through to the plugin's already-tested resolution (`resolved_run_set`, `resolved_judge_config`, `resolve_eval_paths`, layered `--evalspec-config`; `plugin.py:264-281`) so CLI and pytest paths cannot drift.
- **Note:** `analyze` shipped in Phase 4; this phase only folds it under the shared conventions (root positional, exit codes) — no behavior change. `lint` likewise. The bulk of the work is `run` and `sandbox:build`.

## Solution

```text
$ evalspec run --set default                 # translate → pytest -p evalspec.plugin --evalspec-set=default
  benchmark  runner: pytest  sandbox: microsandbox
  <matrix>                                    # exit 0: ran clean and met any fail-under gate

$ evalspec run --set default --fail-under 80  # gate; exit 1 if a group's rate < 80
$ evalspec run --config ./scratch.toml --set trial   # scratch [tool.evalspec] overlay
$ evalspec sandbox:build --set default        # wraps sandbox.cli_build: preflight + build/reuse snapshot
$ evalspec lint . ; evalspec analyze .        # unchanged, now under one convention
```

```text
exit codes (all four commands)
  0  success (lint/analyze clean; run ran with no infra error and met the gate)
  1  finding / gate failure (lint findings; run fail-under gate tripped or an arm infra-errored)
  2  usage error (bad flag, unknown set, unresolved config) — before any paid arm
  5  nothing to do (no evals discovered for the selected paths) — surfaced explicitly, not raw pytest 5
```

One product surface: authoring (`lint`/`analyze`), sandbox warmup (`sandbox:build`), and execution (`run`) are `evalspec` subcommands with one flag vocabulary and one exit contract.

## User Stories

1. As a CLI user, I want **`evalspec run --set <name>`**, so I execute a benchmark without knowing the pytest plugin name or the `--evalspec-*` option spellings.
2. As a CLI user, I want **`evalspec sandbox:build --set <name>`**, so I can prebuild or refresh sandbox images before a run instead of paying the build cost inside the first timed arm.
3. As a CLI user, I want **a documented exit-code contract**, so CI can distinguish a usage error, a gate failure, and an empty run.
4. As a maintainer, I want **`run` and `sandbox:build` to wrap the existing plugin and `cli_build`**, so the CLI and the pytest path resolve config identically and cannot drift.
5. As an eval author, I want **`lint`, `analyze`, `sandbox:build`, and `run` to share a root positional and flag conventions**, so the four commands feel like one tool.

## Implementation Decisions

```text
evalspec <cmd> [root] [flags]
   │
   ├─ lint / analyze  → lint.run(root) / analyze.run(root)          (exists; adopt shared convention)
   │
   ├─ run   → translate curated flags → pytest.main([...])           (in-process; plugin auto-loaded)
   │           --set→--evalspec-set  --config→--evalspec-config
   │           --model(s)/--harness/--effort/--env/--eval-paths/--fail-under/--judge-*  → --evalspec-*
   │           passthrough: `evalspec run -- <extra pytest args>`
   │           map pytest exit status → the documented contract
   │
   └─ sandbox:build → sandbox.cli_build (resolve agent+env, build/reuse)   (wrap; no duplicate path)
```

- **Add `run` and `sandbox:build` to `__main__`.** Register both in the subparser table beside `lint`/`analyze` (`__main__.py:23-40`), reusing `_add_root_argument` for the shared optional `root` positional. `sandbox:build` uses a literal colon in the command name (matches the vision's `evalspec sandbox:build`); argparse treats it as an opaque subcommand string.
- **`run` wraps the plugin in-process via `pytest.main`.** Build a translated argv — `["-p", "evalspec.plugin", <translated --evalspec-* flags>, str(root)]` — and call `pytest.main`. The plugin is already the `pytest11` entry point (`pyproject.toml:56-57`) and self-registers `cases.py` (`plugin.py:370-373`), so no positional test target is threaded. Curated CLI flags map 1:1 onto existing options (`--set`→`--evalspec-set`, `--config`→`--evalspec-config`, `--model`/`--models`, `--harness`, `--effort`, `--env`, `--eval-paths`, `--fail-under`, and the `--judge-*` family; sources at `plugin.py:47-186`). Everything after `--` passes through to pytest verbatim for power users. No new resolution logic — the plugin owns it.
- **`sandbox:build` wraps `sandbox.cli_build`.** Call the existing builder (`sandbox.py:605-615`) rather than reimplementing preflight/build. For Phase 5 it builds the snapshot for the resolved default agent + environment from `root`, honoring `--config`/`--set` for *which* config file/set is read. **Arm-aware, multi-harness prebuild** (one snapshot per harness in the resolved set) depends on Phase 7 (backend selection) and Phase 8 (arm-aware metadata) and is deferred there; Phase 5 delivers the single-agent build path through the CLI. `cli_clean` (`sandbox.py:619`) is not exposed here (separate `make` concern).
- **Define the exit-code contract.** `run` maps pytest's raw status to the documented codes: `0`→0, tests-failed/gate `1`→1, usage `2`/`4`→2, no-tests `5`→a distinct `5` with a readable "no evals discovered under <paths>" message. `sandbox:build` returns `0` on built/reused, `2` on preflight/usage failure, `1` on a build error. `lint`/`analyze` keep their `0`/`1` and gain `2` for a discovery/parse error. A shared helper centralizes the mapping so all four agree.
- **Keep resolution single-sourced; keep Make targets thin.** The plugin's structural judge preflight (`plugin.py:632-637`) and set resolution run unchanged under `run`. `make evals`/`make evals:build` may become thin aliases to `evalspec run`/`evalspec sandbox:build` during the branch; the `make evals → make e2e` rename is a separate roadmap line item, not owned here.

## Testing Plan

### Logic
- **Flag translation is exact** — each curated `run` flag produces the matching `--evalspec-*` token in the argv handed to `pytest.main`; `--` passthrough is forwarded verbatim; absent flags add nothing.
- **Exit-code mapping is total** — every pytest status the run can produce maps to a documented code; `5` (no evals) is distinguished from `1` (gate/infra) and `2` (usage).
- **`sandbox:build` calls the existing builder** — it invokes `cli_build`'s resolve+build path and does not re-implement preflight or snapshot naming; a present snapshot is reused, not rebuilt.

### Behavior
- **`evalspec run --set <fixture-set>` runs a benchmark end to end** — discovery→arms→grading→matrix through the plugin, exit `0` on a clean run, producing the same output as the equivalent raw `pytest -p evalspec.plugin` invocation.
- **`evalspec run --fail-under` gates** — a set whose group rate is below the threshold exits `1`; above it exits `0`.
- **`evalspec run` on an empty selection exits `5`** — a root with no discoverable evals surfaces the explicit "no evals discovered" message, not a raw pytest code.
- **`evalspec sandbox:build --set <fixture-set>` builds or reuses** — first call builds the snapshot; a second call reports reuse; both exit `0`.
- **Usage errors exit `2` before any paid arm** — an unknown `--set`, an unreadable `--config`, or a bad judge flag exits `2` at collection with a readable diagnostic.

### Interface
- **Four subcommands are registered** — `evalspec --help` lists `lint`, `analyze`, `sandbox:build`, `run`; each accepts the shared `root` positional.
- **The exit contract is stable** — the documented codes are what CI observes for each command across success, finding/gate, usage, and empty-run cases.
- **CLI and pytest paths agree** — a set run via `evalspec run --set X` and via `pytest -p evalspec.plugin --evalspec-set=X` resolve the same arms, judge, and eval paths (no divergent resolution).

## Open Questions

- **In-process `pytest.main` vs. a `subprocess` pytest invocation for `run`?** Recommendation: in-process `pytest.main` (no interpreter spawn, direct exit-code access, plugin already loaded); revisit subprocess only if plugin/global state bleed between `run` and other commands proves a problem. Resolved by a call before implementation.
- **Should `run` accept eval paths as positionals (`evalspec run <path>…`) in addition to `--eval-paths`?** The Phase 3 note deferred positional overrides to Phase 5. Recommendation: accept an optional `root` only for now and keep path selection on `--eval-paths`, matching `lint`/`analyze`; add positional path overrides if a concrete need appears.
- **How much of the `--judge-*` family to surface as curated flags vs. `--` passthrough?** Recommendation: surface the common ones (`--judge-harness`, `--judge-model`, `--judge-effort`) and leave the long tail (harness-arg, env, timeout) to `--` passthrough until demand is shown.

## Documentation Plan

- **`docs/quickstart.md`**: replace pytest-only usage with `evalspec lint`, `evalspec analyze`, `evalspec sandbox:build`, and `evalspec run` as the primary flow; note `--` passthrough for power users.
- **`docs/configuration.md`**: document the curated `run` flags and how each maps to a `--evalspec-*` option, plus the exit-code contract.
- **`README.md`**: point the run examples at `evalspec run` once the command lands.

## Out of Scope

- Validating `runner`/`sandbox` as first-class config and failing fast on unsupported values — Phase 7 (surfaced *through* `run`/`sandbox:build`, defined there).
- Arm-aware, multi-harness `sandbox:build` (one snapshot per harness in the set) — Phases 7/8.
- Recording resolved CLI/run facts in `meta.json` / index rows — Phase 8 (metadata).
- The matrix `rate (+Npp)` / `All evals` report shape — Phase 6.
- The `make evals → make e2e` target rename and the expanded contract suite — separate roadmap line / Phase 9.
- New grading, discovery, arm, or judge behavior — this phase is a CLI surface over existing runtime.

## References

- #1 — roadmap umbrella; Phase 5 Done-When and the "promote pytest behavior into CLI commands" decision.
- `docs/research/evalspec-readme-vision.md:263-302` — the `evalspec` command surface (`lint`, `analyze`, `sandbox:build`, `run`) and its intent.
- `src/evalspec/__main__.py:23-40` — current dispatch (`lint`/`analyze` only) and `_add_root_argument`.
- `src/evalspec/plugin.py:47-186` — the `--evalspec-*` option surface `run` translates to; `plugin.py:264-281,370-373,621-643` — set/judge resolution, cases self-registration, and eval-arm parametrization.
- `src/evalspec/cases.py:103-104` — `test_eval`, the entrypoint the plugin runs.
- `src/evalspec/sandbox.py:605-615` — `cli_build`, the build machinery `sandbox:build` wraps; `sandbox.py:194-210` — `ensure_snapshot`.
- `src/evalspec/lint.py:79-90`, `src/evalspec/analyze.py:73-101` — the `run(repo_root) -> int` shape the new commands mirror.
- `pyproject.toml:53-57,92` — the console script, the `pytest11` entry point, and `testpaths`.
- `Makefile:15-18` — current `make evals`/`make evals:build` targets the CLI supersedes.

## Verification

- `make test` — proves flag translation, the exit-code mapping, the `sandbox:build` wrapper, and the four-subcommand registration pass.
- `make lint` — proves package and docs satisfy lint after the CLI additions.
- `evalspec run --set <contract-set>` — proves the public CLI executes a selected benchmark set end to end through the pytest-backed runner (exit `0`).
- `evalspec run --set <contract-set> --fail-under <n>` — proves the gate maps to exit `1` below threshold, `0` above.
- `evalspec run <root-with-no-evals>` exits `5` — proves an empty selection is surfaced distinctly, not as a raw pytest code.
- `evalspec sandbox:build --set <contract-set>` — proves the CLI prebuilds/reuses the expected snapshot via the existing `cli_build` machinery.
- `evalspec run --set <unknown>` and `evalspec run --config <unreadable>` exit `2` — prove usage errors fail fast before any paid arm.
