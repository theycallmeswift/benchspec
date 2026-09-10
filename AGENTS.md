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

## Conventions

- Ask one question at a time.
- Follow existing patterns in whatever file you're touching.
- Verify changes with `make test` and `make lint` before claiming work is done. Show the output.

## Landing changes

- Every PR lands on `main` as one squash commit, through GitHub's merge only. Never
  push to or rewrite `main`. A stack says whether it is one feature (one commit) or
  separate features (one per PR). Details in
  [`docs/style/development.md`](docs/style/development.md#landing-changes).
- No Claude attribution anywhere: no footer, no `Co-Authored-By`, no session URL.

## Directory Structure

- Put plans, specs, and research in `docs/{plans,specs,research}/`.

## References

Load on demand.

- [`docs/style/development.md`](docs/style/development.md) — code style: conventions, error handling, testing, Python specifics. Read before writing or editing code.
- [`README.md`](README.md) — human-facing overview and the rest of `docs/`.
