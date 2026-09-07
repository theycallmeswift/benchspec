# The sandbox

This page explains where an eval cell actually runs: why every cell gets its own
microVM, what a snapshot is and when it rebuilds, what the guest can see, and how
credentials get in without ever being readable inside it. It is for anyone who
wants to trust or customize the isolation; the [quickstart](quickstart.md) does
not require it. Terms (cell, clean room, `/workspace`, `/project`) are defined in
[concepts.md](concepts.md).

A microVM is a small virtual machine with its own kernel, booted in about a
second: hardware isolation like a full VM, startup cost closer to a container.
benchspec runs every cell in one because an agent under test executes
arbitrary commands, and the measurement is only honest if the agent starts from
a known image, sees only the files the eval seeded, and cannot touch your
machine, your credentials, or earlier runs' artifacts.

Isolation sits behind a `SandboxBackend` seam: preflight, the snapshot cache,
and the per-cell mounts belong to the backend, and everything else in
benchspec is backend-agnostic. A set selects its backend with the `sandbox`
key. [microsandbox](https://github.com/superradcompany/microsandbox) is the only
backend implemented to date (`docker` is recognized but fails fast as not
implemented, so a typo cannot silently fall back). The contract on this page —
the mounts, the staging rules, the credential model — holds for any backend;
cache paths and VM sizes are microsandbox specifics and are labeled as such.

## Host requirements and preflight

microsandbox runs hardware-virtualized guests, so the host must be one of:

- an **Apple Silicon Mac**, or
- **Linux with `/dev/kvm`**.

Before any cell runs, benchspec preflights the host — platform, the sandbox
runtime, a usable agent credential — and fails with every problem listed, as
exit `2`, before a single VM boots or a paid call is made.

## Snapshots: build once, boot many

Booting a bare OS image and installing an agent CLI takes minutes; an eval run
boots dozens of VMs. So benchspec builds one **snapshot** per configuration
and boots every cell from it. A snapshot is sealed in five steps:

1. **Base image**: `ubuntu:latest` by default, or `[tool.benchspec] base_image`.
2. **Agent provision**: the harness adapter installs its CLI (for Claude Code,
   `curl -fsSL https://claude.ai/install.sh | bash`; for Codex and OpenCode, an
   npm install of the pinned version).
3. **Skills-home bridge**: the agent's native skill directory is symlinked to
   the fixed, harness-neutral `/home/benchspec/skills`, so a per-eval `setup.sh`
   installs to one path regardless of harness.
4. **Environment script**: `[tool.benchspec] environment_script`, if declared,
   runs under `set -e` — the escape hatch for extra system tools a suite needs.
   A failing command aborts the build loudly.
5. **Seal**: the VM stops and the snapshot is recorded.

Snapshots are cached (for microsandbox, under `~/.microsandbox/snapshots/`) and
named `benchspec-<backend>-<harness>-<harness-version>-<fingerprint>`. The
8-character fingerprint hashes four ingredients:

- the backend id,
- the declared base-image reference,
- the harness's install script,
- the environment script's *bytes*.

Change any one — bump a version pin, edit the environment script, swap
`base_image` — and the name changes, forcing a rebuild. Old snapshots coexist,
so a multi-harness set reuses each harness's cache.

> **Edge case:** the fingerprint hashes the base-image *reference*, not the
> pulled digest. A floating tag like `ubuntu:latest` that moves upstream does
> **not** invalidate the cache, so a floating base is not reproducible across
> time or machines. Pin a digest in `base_image` if you need that. What actually
> got pulled is recorded per arm in `meta.json` under
> `observed_arms[arm].sandbox.image_digest`: an audit trail, not a cache key.

Builds are lazy and concurrent-safe: the first run that needs a snapshot builds
it under a file lock (`tmp/.benchspec-snapshot-<name>.lock` in your repo), so
parallel `pytest -n` workers wait for one build instead of racing. To pay the
cost up front — CI warmup, before a demo, offline prep — build explicitly:

```bash
benchspec sandbox:build                 # default backend + repo env
benchspec sandbox:build --set e2e       # that set's backend + env, one snapshot per harness
benchspec sandbox:build --config x.toml # with a scratch config layered over pyproject
```

`sandbox:build` exits `0` on a built or already-present snapshot, `2` on a
config or host-preflight problem, and `1` on a genuine build failure.

To reclaim the disk those snapshots and any leftover sandboxes hold:

```bash
benchspec sandbox:clean
```

`sandbox:clean` stops and removes `eval-*`, `benchspec-build-*`, and
`trigger-*` sandboxes, removes `benchspec-*` snapshots, and deletes the repo's
`tmp/.benchspec-snapshot-*.lock` files. It stops *running* sandboxes too, so
do not run it during a live `benchspec run`. Snapshots rebuild lazily on the
next run, or explicitly via `sandbox:build`. It always exits `0` (nothing to
prune is success); `2` only on a bad invocation.

## Inside a running cell

Each `(eval × arm × sample)` boots its own VM from the snapshot (for
microsandbox: 2 vCPUs, 2 GiB of memory) and tears it down after the turn. A cell,
in order:

1. Stages the project (see below).
2. Boots from the snapshot.
3. Runs `setup.sh` if present, with the `BENCHSPEC_*` cell variables and the
   arm's `env`.
4. Invokes the agent with its working directory at `/workspace`.
5. Gathers facts: file tree, contents, SHA-256s, the final message, the
   tool-call stream.
6. Tears down and removes the stage.

The guest sees three paths:

| Path | Mount | Contents |
|---|---|---|
| `/workspace` | read-write | The clean room: a fresh host temp dir seeded from the eval's `workspace/`. The agent's working directory. The host grades this directory afterward. |
| `/project` | read-only | A staged copy of your repo (below). Exists so the eval's own `setup.sh` can copy the skill under test into the guest; read-only, so nothing an agent or script does can write back into your checkout. |
| `/home/benchspec/skills` | in-VM | The fixed skills home the snapshot's bridge step created; whatever `setup.sh` installs here is what the agent's skill loader sees. |

### What `/project` contains

`/project` is never a bind mount of your checkout. Each cell stages a fresh copy
and mounts that:

- **In**: what a `git clone` would contain — tracked files plus untracked files
  that `.gitignore` does not ignore — with the relative layout preserved, so
  `setup.sh` paths, a project-local `.claude/skills/`, and
  `harness_args = ["--plugin-dir", "/project"]` resolve as they would against
  the checkout.
- **Always out**, tracked or not: dotenv files (`.env`, `.env.local`,
  `.env.example`, …), `.git`, and benchspec's own `tmp/` artifact root —
  earlier runs' transcripts and grades, which an agent must not be able to crib
  from.
- **Without git** (or outside a checkout): a plain walk that skips `.venv`,
  `node_modules`, `__pycache__`, and `.worktrees` by name; `.gitignore` is not
  honored on that path.
- **Fail-closed**: if a dotenv file would still land in the stage, the cell
  refuses to boot rather than mount it.

## Credentials

Provider credentials never appear as plain environment variables in the guest:

- Each harness adapter declares its credential as a scoped **secret** — usable
  only toward the provider's hosts (an `ANTHROPIC_API_KEY` works only toward
  `api.anthropic.com`). The value is injected at the network boundary and is
  not readable by the agent or by `setup.sh`.
- The one file-shaped exception is Codex subscription auth:
  `CODEX_AUTH_JSON_PATH` is mounted read-only and copied to
  `/root/.codex/auth.json` inside the guest.

Per-harness credential details live in [`harnesses.md`](harnesses.md).

## Customizing the image

Two knobs, both top-level `[tool.benchspec]` keys, both folded into the cache
identity so a change auto-rebuilds:

```toml
[tool.benchspec]
base_image = "python:3.12-slim"        # must be apt-family with glibc
environment_script = "evals/setup.sh"  # runs after the agent installs, before seal
```

`base_image` swaps the OS layer; it must be a Debian/apt-family image because
the provision step uses `apt-get` and installs glibc-linked CLIs.
`environment_script` is for suite-wide system dependencies (compilers, language
runtimes). Per-eval and per-arm setup belongs in the eval's own `setup.sh`
instead, which runs per cell and can branch on `BENCHSPEC_ARM` and
`BENCHSPEC_SET`.

The authoritative modules are `benchspec.sandbox.backend` (preflight,
fingerprint, snapshot build, mounts), `benchspec.sandbox.sandbox` (the cell
lifecycle), and `benchspec.sandbox.project` (the `/project` stage). If this
page and those modules ever disagree, the modules are right.
