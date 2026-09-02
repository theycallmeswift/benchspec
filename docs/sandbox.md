# The sandbox

This page explains where an eval cell actually runs: why every cell gets its own
microVM, what a snapshot is and when it rebuilds, what the guest can see, and how
credentials get in without ever being readable inside it. It is for anyone who
wants to trust or customize the isolation; the [quickstart](quickstart.md) does
not require it. Terms (cell, clean room, `/workspace`, `/project`) are defined in
[concepts.md](concepts.md).

A microVM is a small virtual machine with its own kernel, booted in about a
second: hardware isolation like a full VM, startup cost closer to a container.
harnessbench runs every cell in one because an agent under test executes
arbitrary commands, and the measurement is only honest if the agent starts from
a known image, sees only the files the eval seeded, and cannot touch your
machine, your credentials, or earlier runs' artifacts.

Isolation sits behind a `SandboxBackend` seam: preflight, the snapshot cache,
and the per-cell mounts belong to the backend, and everything else in
harnessbench is backend-agnostic. A set selects its backend with the `sandbox`
key, and two are implemented:

| `sandbox` | Runtime | Host requirement | Isolation |
|---|---|---|---|
| `microsandbox` (default) | [microsandbox](https://github.com/superradcompany/microsandbox) microVMs | Apple Silicon Mac, or Linux with `/dev/kvm` | Own kernel; credentials injected at the network boundary and never readable in the guest |
| `docker` | Docker containers | macOS or Linux with a **local** Docker Engine or Docker Desktop; Windows is untested and a remote `DOCKER_HOST` is not supported | Shared host kernel; credentials are plain environment variables the guest can read |

The contract on this page — the mounts, the staging rules, the cache identity —
holds for both. Where they differ (cache location, sizing, credential handling)
the difference is labeled.

> **Read this before choosing `docker`.** A container shares the host kernel, and
> the agent runs as root with `bypassPermissions` on the promise that the sandbox
> is the containment boundary. Docker also has no equivalent of microsandbox's
> host-scoped secrets, so the arm's provider credential is a readable environment
> variable inside the guest — an agent under test can print it. Choose `docker`
> when microsandbox cannot run on your host, and treat the credential as exposed
> to whatever the agent does.

## Host requirements and preflight

**microsandbox** runs hardware-virtualized guests, so the host must be one of:

- an **Apple Silicon Mac**, or
- **Linux with `/dev/kvm`**.

**Docker** needs the `docker` CLI on `PATH` and a **local** daemon that answers
`docker info` — no virtualization extensions required. Supported on macOS and
Linux; Windows is untested (harnessbench itself uses POSIX-only APIs), and a
remote `DOCKER_HOST` is not supported: every mount is a host path that a remote
daemon would resolve on the wrong machine.

Before any cell runs, harnessbench preflights the *selected* backend — platform
or daemon, plus a usable agent credential — and fails with every problem listed,
as exit `2`, before a single cell boots or a paid call is made.

## Snapshots: build once, boot many

Booting a bare OS image and installing an agent CLI takes minutes; an eval run
boots dozens of VMs. So harnessbench builds one **snapshot** per configuration
and boots every cell from it. A snapshot is sealed in five steps:

1. **Base image**: `ubuntu:latest` by default, or `[tool.harnessbench] base_image`.
2. **Agent provision**: the harness adapter installs its CLI (for Claude Code,
   `curl -fsSL https://claude.ai/install.sh | bash`; for Codex and OpenCode, an
   npm install of the pinned version).
3. **Skills-home bridge**: the agent's native skill directory is symlinked to
   the fixed, harness-neutral `/home/harnessbench/skills`, so a per-eval `setup.sh`
   installs to one path regardless of harness.
4. **Environment script**: `[tool.harnessbench] environment_script`, if declared,
   runs under `set -e` — the escape hatch for extra system tools a suite needs.
   A failing command aborts the build loudly.
5. **Seal**: the VM stops and the snapshot is recorded.

Snapshots are cached — for microsandbox under `~/.microsandbox/snapshots/`, for
Docker as local images in the daemon's image store — and named
`harnessbench-<backend>-<harness>-<harness-version>-<fingerprint>`. The backend id
is the first ingredient of the 8-character fingerprint, so a Docker and a
microsandbox snapshot of the same agent and environment never collide. List the
Docker ones with `docker images "harnessbench-docker-*"` and reclaim the space
with `docker image rm`; they rebuild on demand.

The 8-character fingerprint hashes four ingredients:

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

> **Docker caveat.** A snapshot is sealed with `docker commit`, and a commit does
> not capture paths the base image declares as a `VOLUME`. If your `base_image`
> declares a volume over somewhere the harness CLI or your `environment_script`
> installs into, those files will be missing at cell boot. Pick a base image
> without a `VOLUME` over your install paths.

Builds are lazy and concurrent-safe: the first run that needs a snapshot builds
it under a file lock (`tmp/.harnessbench-snapshot-<name>.lock` in your repo), so
parallel `pytest -n` workers wait for one build instead of racing. To pay the
cost up front — CI warmup, before a demo, offline prep — build explicitly:

```bash
harnessbench sandbox:build                 # default backend + repo env
harnessbench sandbox:build --set e2e       # that set's backend + env, one snapshot per harness
harnessbench sandbox:build --config x.toml # with a scratch config layered over pyproject
```

`sandbox:build` exits `0` on a built or already-present snapshot, `2` on a
config or host-preflight problem, and `1` on a genuine build failure.

## Inside a running cell

Each `(eval × arm × sample)` boots its own guest from the snapshot — a microVM
under microsandbox, a container under Docker, both sized 2 vCPUs and 2 GiB — and
tears it down after the turn. A cell, in order:

1. Stages the project (see below).
2. Boots from the snapshot.
3. Runs `setup.sh` if present, with the `HARNESSBENCH_*` cell variables and the
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
| `/home/harnessbench/skills` | in-VM | The fixed skills home the snapshot's bridge step created; whatever `setup.sh` installs here is what the agent's skill loader sees. |

### What `/project` contains

`/project` is never a bind mount of your checkout. Each cell stages a fresh copy
and mounts that:

- **In**: what a `git clone` would contain — tracked files plus untracked files
  that `.gitignore` does not ignore — with the relative layout preserved, so
  `setup.sh` paths, a project-local `.claude/skills/`, and
  `harness_args = ["--plugin-dir", "/project"]` resolve as they would against
  the checkout.
- **Always out**, tracked or not: dotenv files (`.env`, `.env.local`,
  `.env.example`, …), `.git`, and harnessbench's own `tmp/` artifact root —
  earlier runs' transcripts and grades, which an agent must not be able to crib
  from.
- **Without git** (or outside a checkout): a plain walk that skips `.venv`,
  `node_modules`, `__pycache__`, and `.worktrees` by name; `.gitignore` is not
  honored on that path.
- **Fail-closed**: if a dotenv file would still land in the stage, the cell
  refuses to boot rather than mount it.

## Credentials

- **Under `microsandbox`**, each harness adapter declares its credential as a
  scoped **secret** — usable only toward the provider's hosts (an
  `ANTHROPIC_API_KEY` works only toward `api.anthropic.com`). The value is
  injected at the network boundary and is not readable by the agent or by
  `setup.sh`.
- **Under `docker`**, the same declaration becomes a plain container environment
  variable. Docker has no host-scoping primitive, so the value IS readable in the
  guest and `allow_hosts` is not enforced. Prefer a narrowly-scoped API key over a
  subscription token for Docker runs.
- The one file-shaped exception, on either backend, is Codex subscription auth:
  `CODEX_AUTH_JSON_PATH` is mounted read-only and copied to
  `/root/.codex/auth.json` inside the guest.

Per-harness credential details live in [`harnesses.md`](harnesses.md).

Trigger routing — the probe that asks whether a harness would reach for your
skill unprompted — runs under `microsandbox` only. A set with `sandbox = "docker"`
runs its evals in containers as normal; routing checks are not available for it.

## Customizing the image

Two knobs, both top-level `[tool.harnessbench]` keys, both folded into the cache
identity so a change auto-rebuilds:

```toml
[tool.harnessbench]
base_image = "python:3.12-slim"        # must be apt-family with glibc
environment_script = "evals/setup.sh"  # runs after the agent installs, before seal
```

`base_image` swaps the OS layer; it must be a Debian/apt-family image because
the provision step uses `apt-get` and installs glibc-linked CLIs.
`environment_script` is for suite-wide system dependencies (compilers, language
runtimes). Per-eval and per-arm setup belongs in the eval's own `setup.sh`
instead, which runs per cell and can branch on `HARNESSBENCH_ARM` and
`HARNESSBENCH_SET`.

The authoritative modules are `harnessbench.sandbox.backend` (the microsandbox
backend) and `harnessbench.sandbox.docker` (the Docker backend) for preflight,
snapshot build, and mounts; `harnessbench.sandbox.primitives` for the pieces
both share (mount paths, VM sizing, the cache fingerprint); `harnessbench.sandbox.sandbox`
for the cell lifecycle; and `harnessbench.sandbox.project` for the `/project`
stage. If this page and those modules ever disagree, the modules are right.
