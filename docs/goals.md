# Goals

evalspec's standing objectives — the durable "what we're aiming at" that point-in-time design notes serve. For how the runner works, see the [README](../README.md); for the agent seam this doc keeps pointing at, see [`agents.md`](agents.md).

## Core objectives

- **A portable eval harness.** evalspec is a pytest-native runner that benchmarks two things: whether *adding a skill* changes a coding agent's behavior, and the harness mechanics around that — activation, sandboxing, and honest paired-arm grading. The benchmark is the marginal contribution of the skill, not the agent's baseline competence.
- **A single portable seam, not a single tool.** evalspec abstracts every agent-specific concern behind the `CodingAgent` protocol so it isn't bound to one CLI. Today that seam carries `claude-code`, `opencode`, and `codex`; it grows toward the harness compatibility targets below. Adding a harness is additive — one file plus a registry entry, never a change to the sandbox lifecycle, judge, or honesty contract.
- **Open by default.** evalspec is on track to be open-sourced as a standalone library; its docs are written to read for adopters outside any one consumer.

## Harness compatibility targets

When assessing cross-harness **compatibility or portability** — whether evalspec's runner, the `CodingAgent` seam, or an eval design carries across AI coding tools beyond Claude Code — these five are the harnesses in scope. Scope compatibility comparisons to this set unless a task says otherwise.

| Harness | Vendor | Notes |
|---|---|---|
| **OpenCode** (`sst/opencode`) | sst | Open-source agent/CLI. Already a first-class eval runner via the `CodingAgent` protocol. |
| **Codex** | OpenAI | Codex CLI is a first-class eval runner via the `CodingAgent` protocol; Codex Cloud remains out of scope. |
| **Cursor** | Anysphere | AI IDE; also ships a CLI/agents surface. |
| **GitHub Copilot** | GitHub | CLI + coding agent + editor integration. |
| **Antigravity** | Google | Agentic IDE. |

The `CodingAgent` interface (today: `claude-code` + `opencode` + `codex`) is the existing portable layer; this list is the broader horizon it grows toward. New portability research should state this set explicitly and assess each harness against it. See [`agents.md`](agents.md) for how a harness is added as an eval runner.
