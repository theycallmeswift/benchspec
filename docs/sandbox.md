# The sandbox

This page explains where an eval cell actually runs: why every cell gets its own
sandbox, what a snapshot is and when it rebuilds, what the guest can see, and how
credentials get in. It is for anyone who wants to trust or customize the
isolation; the [quickstart](quickstart.md) does not require it. Terms (cell,
clean room, `/workspace`, `/project`) are defined in [concepts.md](concepts.md).

benchspec runs every cell in an isolated sandbox because an agent under test
executes arbitrary commands, and the measurement is only honest if the agent
starts from a known image, sees only the files the eval seeded, and cannot
touch your machine, your credentials, or earlier runs' artifacts.

Isolation sits behind a `SandboxBackend` seam: preflight, the snapshot cache,
and the per-cell mounts belong to the backend, and everything else in
benchspec is backend-agnostic. A set selects its backend with the `sandbox`
key. Two backends are implemented:

- **Docker** (the default): each cell is a container, driven through the
  `docker` CLI. Any host with a running Docker daemon works — no platform
  check.
- **microsandbox** (the opt-in, `sandbox = "microsandbox"` on the set): each
  cell boots its own **microVM** — a small virtual machine with its own
  kernel, booted in about a second: hardware isolation like a full VM,
  startup cost closer to a container. It needs an Apple Silicon Mac or Linux
  with `/dev/kvm`, plus the `microsandbox` extra (`pip install
  "benchspec[microsandbox]"`).

The contract on this page — the mounts, the staging rules, the credential
model — holds for any backend; cache paths, image names, and credential
exposure differ per backend and are labeled as such.

## Docker

A container shares the host kernel. The agent runs as root with
`bypassPermissions` on the promise that the sandbox is the containment
boundary, and under Docker that boundary is the container, not a VM.
Credentials under Docker are container environment variables — injected as
`-e KEY=VALUE` — readable by the agent and by `setup.sh`, with no host
scoping; microsandbox scopes each credential to its provider host at the
network boundary instead (see [Credentials](#credentials)). Pick microsandbox
when that isolation gap matters for what you are testing.

Otherwise Docker behaves like any backend on this page: same mounts, same
staging rules, same snapshot lifecycle. The backend-specific facts are the
image cache naming (`benchspec-snapshot:benchspec-docker-<harness>-<version>-<fingerprint>`,
see [Snapshots](#snapshots-build-once-boot-many)), the container-sizing flags
(`--cpus 2 --memory 2048m`, see [Inside a running cell](#inside-a-running-cell)),
and the credential model above.

> **Untested.** Rootless Docker and Podman have not been exercised against
> this backend; preflight assumes a conventional `docker info` shape and may
> misreport on either. Because Docker credentials are plain container
> environment variables, a Codex subscription token
> (`CODEX_AUTH_JSON_PATH`) mounted into the guest is exposed the same way any
> other file in the container is — there is no per-credential network
> scoping to fall back on, so treat subscription-auth arms as higher-trust
> under Docker than under microsandbox. Windows is untested entirely; the
> `docker` CLI invocations and mount-path handling assume a POSIX host.

## Host requirements and preflight

Before any cell runs, benchspec preflights the host — the sandbox runtime, a
usable agent credential — and fails with every problem listed, as exit `2`,
before a single sandbox boots or a paid call is made.

**Docker** (the default) has no platform gate: Docker Desktop and Docker
Engine cover every host benchspec runs on, so preflight only checks that the
`docker` CLI is on `PATH` (or at `BENCHSPEC_DOCKER_PATH`, the way
`MSB_PATH` overrides microsandbox's `msb`) and that `docker info` succeeds.
Failures produce one of:

- ``docker CLI not found — install Docker Engine or Docker Desktop, or set BENCHSPEC_DOCKER_PATH to the binary``
- ``Docker daemon unreachable (`docker info` failed: …) — start Docker Desktop or the docker service``

**microsandbox** (the opt-in) runs hardware-virtualized guests, so it further
needs the host to be one of:

- an **Apple Silicon Mac**, or
- **Linux with `/dev/kvm`**.

## Snapshots: build once, boot many

Booting a bare OS image and installing an agent CLI takes minutes; an eval run
boots dozens of sandboxes. So benchspec builds one **snapshot** per
configuration and boots every cell from it. A snapshot is sealed in five
steps:

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
5. **Seal**: the guest stops and the snapshot is recorded (a `docker commit`
   for Docker, a sealed microVM snapshot for microsandbox).

Snapshots are cached — for Docker, as committed images (`docker images
benchspec-snapshot` lists them) tagged
`benchspec-snapshot:benchspec-docker-<harness>-<harness-version>-<fingerprint>`;
for microsandbox, under `~/.microsandbox/snapshots/`, named
`benchspec-microsandbox-<harness>-<harness-version>-<fingerprint>`. The
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

`sandbox:clean` stops and removes every `benchspec-*` sandbox (leaked
`benchspec-eval-*` cells, `benchspec-trigger-*` probes, `benchspec-build-*`
build sandboxes) across whichever backend created them, removes every
`benchspec-*` snapshot — `benchspec-snapshot:benchspec-*` Docker images
alongside microsandbox's `benchspec-*` entries — and deletes the repo's
`tmp/.benchspec-snapshot-*.lock` files. Containers or VMs other tools keep
(untagged Docker images, other `~/.microsandbox` entries) are left alone. It
stops *running* sandboxes too, so do not run it during a live `benchspec run`.
Snapshots rebuild lazily on the next run, or explicitly via `sandbox:build`. It
always exits `0` (nothing to prune is success); `2` only on a bad invocation.

## Inside a running cell

Each `(eval × arm × sample)` boots its own sandbox from the snapshot (2 vCPUs,
2 GiB of memory on either backend — `--cpus 2 --memory 2048m` for a Docker
container) and tears it down after the turn. A cell, in order:

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
| `/home/benchspec/skills` | in-guest | The fixed skills home the snapshot's bridge step created; whatever `setup.sh` installs here is what the agent's skill loader sees. |

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

Each harness adapter declares its credential once, but the two backends expose
it differently:

- **microsandbox** injects it as a scoped **secret** — usable only toward the
  provider's hosts (an `ANTHROPIC_API_KEY` works only toward
  `api.anthropic.com`). The value is injected at the network boundary and is
  not readable by the agent or by `setup.sh`.
- **Docker** injects the same credential as a plain container environment
  variable (`-e ANTHROPIC_API_KEY=...`), readable by the agent and by
  `setup.sh`. There is no per-credential host scoping under Docker.

The one file-shaped exception, on both backends: Codex subscription auth,
`CODEX_AUTH_JSON_PATH`, is mounted read-only and copied to
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

The authoritative modules are `benchspec.sandbox.backend` (the seam, preflight,
fingerprint, snapshot build, mounts, microsandbox), `benchspec.sandbox.docker`
(the Docker implementation), `benchspec.sandbox.sandbox` (the cell lifecycle),
and `benchspec.sandbox.project` (the `/project` stage). If this page and those
modules ever disagree, the modules are right.
