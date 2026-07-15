# The sandbox

Every eval cell runs inside a microVM — not a container, not a subprocess on your
machine. The agent starts from a known image and a clean workdir containing only
the files the eval seeded. Your repo is also readable, but immutable, at
`/project` so `setup.sh` can install the skill under test; the agent shares the
guest and can read that mount too. This document covers the lifecycle: what gets
baked into a snapshot, when snapshots rebuild, what a running cell can see, and
how credentials get in without ever being readable in the guest.

The implementation is [microsandbox](https://github.com/microsandbox/microsandbox),
behind a `SandboxBackend` seam. A set selects its backend with the `sandbox` key;
`microsandbox` is the only implementation today (`docker` is recognized but fails
fast as not implemented, so a typo can't silently fall back).

## Host requirements and preflight

microsandbox runs hardware-virtualized guests, so the host must be an **Apple
Silicon Mac** or **Linux with `/dev/kvm`**. Before any cell runs, evalspec
preflights the host — platform, the microsandbox runtime, and a usable agent
credential — and fails with every problem listed, as exit `2`, before a single VM
boots or a paid call is made.

## Snapshots: build once, boot many

Booting a bare Ubuntu image and installing an agent CLI takes minutes; an eval
run boots dozens of VMs. So evalspec builds one **snapshot** per configuration
and boots every cell from it. A snapshot is sealed in five steps:

1. **Base image** — `ubuntu:latest` by default, or `[tool.evalspec] base_image`.
2. **Agent provision** — the harness adapter installs its CLI (for Claude Code,
   `curl -fsSL https://claude.ai/install.sh | bash`; for Codex and OpenCode, an
   npm install of the pinned version).
3. **Skills-home bridge** — the agent's native skill directory is symlinked to
   the fixed, harness-neutral `/home/evalspec/skills`, so a per-eval `setup.sh`
   installs to one path regardless of harness.
4. **Environment script** — `[tool.evalspec] environment_script`, if declared,
   runs under `set -e`: the escape hatch for extra system tools an eval suite
   needs. A failing command aborts the build loudly.
5. **Seal** — the VM stops and the snapshot is recorded.

Snapshots are cached under `~/.microsandbox/snapshots/` and named

```
evalspec-<backend>-<agent>-<agent-version>-<fingerprint>
```

where the 8-character fingerprint hashes the backend id, the declared base-image
reference, the agent's install script, and the environment script's *bytes*.
Change any ingredient — bump `EVALSPEC_CLAUDE_VERSION`, edit the environment
script in place, swap `base_image` — and the name changes, forcing a rebuild;
old snapshots coexist, so a multi-harness set reuses each harness's cache.

> **Edge case:** the fingerprint hashes the base-image *reference*, not the
> pulled digest. A floating tag like `ubuntu:latest` that moves upstream does
> **not** invalidate the cache — a floating base is therefore not reproducible
> across time or machines. Pin a digest in `base_image` if you need that. What
> actually got pulled is recorded per arm in `meta.json` under
> `observed_arms[arm].sandbox.image_digest` — an audit trail, not a cache key.

Builds are lazy and concurrent-safe: the first run that needs a snapshot builds
it under a file lock (at `tmp/.evalspec-snapshot-<name>.lock` in your repo), so
parallel `pytest -n` workers wait for one build instead of racing. To pay the
cost up front — CI warmup, before a demo, offline prep — build explicitly:

```bash
evalspec sandbox:build                 # default backend + repo env
evalspec sandbox:build --set e2e       # that set's sandbox backend + env
evalspec sandbox:build --config x.toml # with a scratch config layered over pyproject
```

`sandbox:build` exits `0` on a built or already-present snapshot, `2` on a
config or host-preflight problem, and `1` on a genuine build failure.

## Inside a running cell

Each `(eval × arm × sample)` boots its own VM from the snapshot — 2 vCPUs, 2 GiB
of memory — and tears it down after the turn. The guest sees:

| Path | Mount | Contents |
|---|---|---|
| `/workspace` | read-write | The clean room: a fresh host temp dir seeded from the eval's `workspace/`. The agent's working directory. The host grades this directory afterward. |
| `/project` | read-only | Your repo root. Exists so the eval's own `setup.sh` can copy the skill under test into the guest — read-only, so nothing an agent or script does can write back into your checkout. |
| `/home/evalspec/skills` | in-VM | The fixed skills home the snapshot's bridge step created; whatever `setup.sh` installs here is what the agent's skill loader sees. |

The order of events in a cell: boot from snapshot → run `setup.sh` (if present,
with the `EVALSPEC_*` cell variables and the arm's `env`) → invoke the agent with
its working directory at `/workspace` → gather facts (file tree, contents,
SHA-256s, the final message, the tool-call stream) → tear down.

## Credentials

Provider credentials never appear as plain environment variables in the guest.
Each harness adapter declares its credential as a microsandbox **secret**, scoped
to the provider's hosts (for example, an `ANTHROPIC_API_KEY` is usable only
toward `api.anthropic.com`) — the value is injected at the network boundary and
is not readable by the agent or by `setup.sh`. The one file-shaped exception is
Codex subscription auth: `CODEX_AUTH_JSON_PATH` is mounted read-only and copied
to `/root/.codex/auth.json` inside the guest. Details per harness in
[`harnesses.md`](harnesses.md).

## Customizing the image

Two knobs, both top-level `[tool.evalspec]` keys, both folded into the cache
identity so a change auto-rebuilds:

```toml
[tool.evalspec]
base_image = "python:3.12-slim"        # must be apt-family with glibc
environment_script = "evals/setup.sh"  # runs after the agent installs, before seal
```

`base_image` swaps the OS layer; it must be a Debian/apt-family image because the
provision step uses `apt-get` and installs glibc-linked CLIs.
`environment_script` is for suite-wide system dependencies (compilers, language
runtimes) — per-eval and per-arm setup belongs in the eval's own `setup.sh`
instead, which runs per cell and can branch on `EVALSPEC_ARM` and `EVALSPEC_SET`.
