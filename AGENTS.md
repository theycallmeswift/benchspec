# benchspec

A benchmark framework for agents: Markdown-authored evals run across named arms (harness × model), sandboxed under microsandbox and graded into matrix reports with machine-readable artifacts. The codebase is actively converging on that vision — internals are in motion, so don't over-index on how things fit together today.

## Working With Me

I'm Swift. I value directness, bias to action, and learning by doing. Lead with the point, keep it short, don't hedge.

## Autonomy

- **Freely reversible** — just do it. Edits, local commands, scratch files.
- **Consequential** — state intent and proceed. Pushes, new deps.
- **Irreversible** — always confirm first. Force-push, deletes, shared posts.

## Decision Principles

- **Propose, then confirm.** Present 2–3 options with a recommendation before writing code.
- **Action over asking.** Exhaust reversible options — read the code, run a small experiment — before asking.
- **Simple over clever.** Don't over-engineer. Question whether a concept needs to exist before adding one.
- **Concise over verbose.** Cut preamble.
- **No backwards compatibility.** benchspec is pre-1.0, so backwards compatibility is explicitly not a goal. Don't write migration paths, legacy-format handling, or shims for older dependency versions or stale local state — break cleanly and let users clean up (e.g. `benchspec sandbox:clean`).

## Conventions

- Ask one question at a time.
- Follow existing patterns in whatever file you're touching.
- Use conventional commits (`feat:`, `fix:`, `docs:`, `chore:`).
- Verify changes with `make test` and `make lint` before claiming work is done. Show the output.
- Keep `docs/` current: read the relevant doc before changing architecture,
  and update it in the same PR.

## Environment

- Needs Python 3.11 or newer and uv; run `make install` when `uv.lock`
  changes.
- `make test` needs no credentials.
- `make lint` runs Ruff, ty, then houserules, which needs `GEMINI_API_KEY`.
  `make lint:ruff` and `make lint:ty` need none.
- `make e2e` and `make evals` spend model credit; the README's Development
  section lists what they need.

## Landing changes

- Open every PR as a draft from `.github/pull_request_template.md`. Mark it
  ready yourself once every Ready gate row has evidence. If CI fails after
  that, convert it back to a draft, fix it, and mark it ready again.
- The PR body carries no process narrative and no line-number citations.
- Every PR lands on `main` as one squash commit, through GitHub's merge only.
  Never push to or rewrite `main`. Update a branch by rebase, or by merging
  `main` into it; the squash keeps history linear.
- The PR title is the squash commit's title: `<type>(<scope>): summary`.
- A stack says, when it opens, whether it is one feature or separate
  features. One feature split for review lands as a single squash of the
  stack's top branch, and its review PRs close as superseded. Separate
  features that block each other merge bottom-up, one commit per PR.
- No Claude attribution anywhere: no footer, no `Co-Authored-By`, no session
  URL. The `attribution` block in `.claude/settings.json` overrides any
  harness default.

## Project structure

- `src/benchspec/`: the package, by feature.
- `tests/`: the test suite, mirroring `src/benchspec/`; `tests/support/` and
  `tests/fixtures/` hold shared helpers and test data.
- `evals/`: the in-repo suites, `e2e/hello/` and the `binder/` corpus.
- `scripts/`: tools run by hand or by `make`; never part of the package.
- `docs/*.md`: undated guides, one per feature or setup, indexed in
  `README.md`.
- `docs/plans/`, `docs/specs/`, `docs/research/`: dated `YYYY-MM-DD-<slug>.md`
  files.
- `docs/style/`: how code, tests, and docs are written.
- `tmp/`: scratch space, git-ignored.

## References

Load on demand.

- [`docs/style/development.md`](docs/style/development.md) — code style. Read
  before changing source, tests, or docs, then the guide it names.
- [`README.md`](README.md) — human-facing overview and the rest of `docs/`.
