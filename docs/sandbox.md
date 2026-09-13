# The sandbox

Where an eval cell runs: the snapshot it boots from, what the guest sees, and how
credentials get in. Terms (cell, clean room, `/workspace`, `/project`) are defined
in [concepts.md](concepts.md).

The agent under test runs arbitrary commands, so every cell gets its own sandbox:
a known image, only the files the eval seeded, no reach into your machine, your
credentials, or earlier runs' artifacts. The sandbox, not any harness's own
permission prompts, is the containment boundary: every harness runs with its
approvals bypassed inside the guest. A set picks its backend with the
`sandbox` key: `docker` (the default), where each cell is a container driven
through the `docker` CLI, or `microsandbox`, where each cell boots its own
**microVM** — its own kernel, booted in about a second.

## Docker

A container shares the host kernel, so the boundary the agent's
`bypassPermissions` relies on is the container, not a VM. Credentials under
Docker are container environment variables (`-e KEY=VALUE`), readable by the
agent and by `setup.sh`, with no host scoping; microsandbox scopes each
credential to its provider host at the network boundary instead (see
[Credentials](#credentials)). The value is readable outside the guest too:
`docker inspect <container>` returns it while the container exists, and it
appears in the `docker run` argv in the host process list. Pick microsandbox when
that gap matters.

> **Untested.** Rootless Docker and Podman have not been exercised; preflight
> assumes a conventional `docker info` shape and may misreport on either. A Codex
> subscription token (`CODEX_AUTH_JSON_PATH`) mounted into the guest is exposed
> like any other file in the container, with no per-credential network scoping to
> fall back on, so treat subscription-auth arms as higher-trust under Docker.
> Windows is untested entirely; the `docker` invocations and mount-path handling
> assume a POSIX host.

## Host requirements and preflight

benchspec preflights the host — sandbox runtime, agent credential — and fails
with every problem listed, as exit `2`, before a sandbox boots or a paid call is
made.

| Backend | Host | Preflight |
|---|---|---|
| Docker | Any host with a running daemon; no platform gate | `docker` on `PATH` (or at `BENCHSPEC_DOCKER_PATH`, the way `MSB_PATH` overrides microsandbox's `msb`), and `docker info` succeeding |
| microsandbox | An Apple Silicon Mac, or Linux with `/dev/kvm`, plus the extra (`pip install "benchspec[microsandbox]"`) | That platform, plus the runtime installed |

Docker failures produce one of:

- ``docker CLI not found — install Docker Engine or Docker Desktop, or set BENCHSPEC_DOCKER_PATH to the binary``
- ``Docker daemon unreachable (`docker info` failed: …) — start Docker Desktop or the docker service``

## Snapshots: build once, boot many

A run boots dozens of sandboxes and installing an agent CLI takes minutes, so
benchspec builds one **snapshot** per configuration and boots every cell from it:

1. **Base image**: `ubuntu:latest`, or `[tool.benchspec] base_image`.
2. **Agent provision**: the harness adapter installs its CLI (Claude Code,
   `curl -fsSL https://claude.ai/install.sh | bash`; Codex and OpenCode, an npm
   install of the pinned version).
3. **Skills-home bridge**: the agent's skill directory is symlinked to the fixed
   `/home/benchspec/skills`, so one `setup.sh` path works for every harness.
4. **Environment script**: `[tool.benchspec] environment_script`, if declared,
   runs under `set -e`; a failing command aborts the build loudly.
5. **Seal**: the guest stops and the snapshot is recorded (a `docker commit` for
   Docker, a sealed microVM snapshot for microsandbox).

Snapshots cache as `benchspec-<backend>-<harness>-<harness-version>-<fingerprint>`
— for Docker an image tag
(`benchspec-snapshot:benchspec-docker-<harness>-<version>-<fingerprint>`, listed
by `docker images benchspec-snapshot`), for microsandbox an entry under
`~/.microsandbox/snapshots/`. The 8-character fingerprint hashes four
ingredients:

- the backend id,
- the declared base-image reference,
- the harness's install script,
- the environment script's *bytes*.

Change any one and the name changes, forcing a rebuild; old snapshots coexist, so
a multi-harness set reuses each harness's cache.

> **Edge case:** the fingerprint hashes the base-image *reference*, not the
> pulled digest. A floating tag like `ubuntu:latest` that moves upstream does
> **not** invalidate the cache, so a floating base is not reproducible across time
> or machines. Pin a digest in `base_image` if you need that. What was pulled is
> recorded per arm in `meta.json` at `observed_arms[arm].sandbox.image_digest`:
> an audit trail, not a cache key.

Builds are lazy: the first run needing a snapshot builds it under a file lock
(`tmp/.benchspec-snapshot-<name>.lock` in your repo), so parallel `pytest -n`
workers wait instead of racing. To build ahead of time:

```bash
benchspec sandbox:build                 # default backend + repo env
benchspec sandbox:build --set e2e       # that set's backend + env, one snapshot per harness
benchspec sandbox:build --config x.toml # with a scratch config layered over pyproject
```

It exits `0` on a built or already-present snapshot, `2` on a config or
host-preflight problem, and `1` on a genuine build failure.

`benchspec sandbox:clean` reclaims the disk snapshots and leftover sandboxes
hold, on whichever backend created them:

| Removes | |
|---|---|
| Sandboxes | every `benchspec-*` guest: leaked `benchspec-eval-*` cells, `benchspec-trigger-*` probes, `benchspec-build-*` build sandboxes |
| Snapshots | `benchspec-snapshot:benchspec-*` Docker images, microsandbox's `benchspec-*` entries |
| Locks | the repo's `tmp/.benchspec-snapshot-*.lock` files |

Anything without the prefix is left alone (untagged Docker images, other
`~/.microsandbox` entries). It stops *running* sandboxes too, so do not run it
during a live `benchspec run`; snapshots rebuild on the next run, or via
`sandbox:build`. It always exits `0` (nothing to prune is success); `2` only on a
bad invocation.

## Inside a running cell

Each `(eval × arm × sample)` boots its own sandbox from the snapshot (2 vCPUs,
2 GiB on either backend — `--cpus 2 --memory 2048m` for a Docker container) and
tears it down after the turn. A cell, in order:

1. Stages the project (below).
2. Boots from the snapshot.
3. Runs `setup.sh` if present, with the `BENCHSPEC_*` cell variables and the
   arm's `env`.
4. Invokes the agent with its working directory at `/workspace`.
5. Gathers facts: file tree, contents, SHA-256s, the final message, the
   tool-call stream.
6. Tears down and removes the stage.

Under Docker, a guest command that times out or is killed ends the `docker exec`
client on the host, not the process it started inside the container: that process
runs until the container is removed at teardown, which is what reclaims it.

The guest sees three paths:

| Path | Mount | Contents |
|---|---|---|
| `/workspace` | read-write | The clean room: a fresh host temp dir seeded from the eval's `workspace/`. The agent's working directory; the host grades it afterward. |
| `/project` | read-only | A staged copy of your repo (below), so the eval's `setup.sh` can copy the skill under test in. Nothing in the guest can write back to your checkout. |
| `/home/benchspec/skills` | in-guest | The fixed skills home from the bridge step; what `setup.sh` installs here is what the agent's skill loader sees. |

### What `/project` contains

`/project` is never a bind mount; each cell stages a fresh copy and mounts that:

- **In**: what a `git clone` would contain — tracked files plus untracked files
  `.gitignore` does not ignore — layout preserved, so `setup.sh` paths, a
  project-local `.claude/skills/`, and
  `harness_args = ["--plugin-dir", "/project"]` resolve as against the checkout.
- **Always out**, tracked or not: dotenv files (`.env`, `.env.local`,
  `.env.example`, …), `.git`, and benchspec's own `tmp/` artifact root — earlier
  runs' transcripts and grades, which an agent must not crib from.
- **Without git** (or outside a checkout): a plain walk that skips `.venv`,
  `node_modules`, `__pycache__`, and `.worktrees` by name; `.gitignore` is not
  honored on that path.
- **Fail-closed**: if a dotenv file would still land in the stage, the cell
  refuses to boot rather than mount it.

## Credentials

Each harness declares its credential once; the backends expose it differently:

| Backend | Exposure |
|---|---|
| microsandbox | A scoped **secret**, injected at the network boundary and usable only toward the provider's hosts (an `ANTHROPIC_API_KEY` works only toward `api.anthropic.com`; under `provider = "openrouter"` the key works only toward `openrouter.ai`). Not readable by the agent or by `setup.sh`. |
| Docker | A plain container environment variable (`-e ANTHROPIC_API_KEY=...`), readable by the agent and by `setup.sh`. No per-credential host scoping. |

The one file-shaped exception, on both backends: Codex subscription auth,
`CODEX_AUTH_JSON_PATH`, is mounted read-only and copied to
`/root/.codex/auth.json` inside the guest. Per-harness details are in
[`harnesses.md`](harnesses.md).

## Customizing the image

Two top-level `[tool.benchspec]` keys, both folded into the cache identity, so a
change auto-rebuilds:

```toml
[tool.benchspec]
base_image = "python:3.12-slim"        # must be apt-family with glibc
environment_script = "evals/setup.sh"  # runs after the agent installs, before seal
```

`base_image` must be Debian/apt-family: the provision step uses `apt-get` and
installs glibc-linked CLIs. `environment_script` is for suite-wide system
dependencies; per-eval and per-arm setup belongs in the eval's own `setup.sh`,
which can branch on `BENCHSPEC_ARM` and `BENCHSPEC_SET`. The full variable list,
`BENCHSPEC_BASELINE` included, is in
[writing-evals.md](writing-evals.md#setupsh-what-differs-per-arm).

`BENCHSPEC_BASE_IMAGE` in the host environment overrides `base_image` for that
host only. It exists for hosts whose base must carry something the shared config
shouldn't: the provision step fetches the agent CLI over HTTPS, so a host behind
a TLS-intercepting proxy needs its CA trusted inside the guest before the
install runs. `environment_script` runs too late for that.

### Claude Code on the web

The cloud VM is one such host. Once the environment is set up as below, a fresh
session's whole hello world is `make install && make e2e`.
[`scripts/cloud-env-setup.sh`](../scripts/cloud-env-setup.sh) is the
environment's setup script: paste it into the **Setup script** field of the
cloud environment at claude.ai/code. It installs the Codex judge, writes the
dotenv file described below, starts `dockerd`, builds `benchspec-base:proxy-ca`
from `ubuntu:latest` with the proxy CA installed and `NODE_EXTRA_CA_CERTS`
pointing at it, and writes the session hook described below. The script runs
once; the environment snapshot keeps its files and images for later sessions.

Two things the snapshot can't carry:

- **A running daemon.** Sessions after the first start with `dockerd` stopped.
  The setup script writes a gitignored `.claude/settings.local.json` in the clone
  whose `SessionStart` hook starts `dockerd` whenever `docker info` fails, so a
  session can run `make e2e` with no manual step. Without the hook, start it by
  hand:

  ```bash
  setsid nohup dockerd >/tmp/benchspec-dockerd.log 2>&1 </dev/null &
  ```

- **The Claude credential.** Claude Code strips its own auth variables
  (`CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`) from every command it runs,
  so a value under either name in the cloud environment never reaches
  `make e2e`. Store the token as `BENCHSPEC_CLAUDE_OAUTH_TOKEN` instead. The
  setup script writes `/home/user/.env`, above the clone, mapping it back;
  benchspec's dotenv loader walks up from the repo and expands the reference at
  run time, so the file holds no secret. The same file carries
  `BENCHSPEC_BASE_IMAGE`.

`GEMINI_API_KEY`, `OPENROUTER_API_KEY`, and the Codex credential (`CODEX_API_KEY`
or `OPENAI_API_KEY`) pass through untouched. The Codex judge also needs
`api.openai.com` in the environment's allowed domains, and `make e2e`'s
OpenRouter run needs `openrouter.ai`; the Trusted default list includes neither.

The authoritative modules are `benchspec.sandbox.backend` (the seam: the
protocol, the shared constants, the fingerprint, the shared build steps),
`benchspec.sandbox.docker` and `benchspec.sandbox.microsandbox` (the two backend
implementations), `benchspec.sandbox.registry` (backend selection),
`benchspec.sandbox.sandbox` (the cell lifecycle), and `benchspec.sandbox.project`
(the `/project` stage). If this page and those modules ever disagree, the modules
are right.
