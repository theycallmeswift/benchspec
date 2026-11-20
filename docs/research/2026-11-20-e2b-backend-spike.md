# E2B backend spike

Research, 2026-11-20. Spike for an E2B sandbox backend, scoped in issue 181
and in [the wall-clock note](2026-11-12-matrix-wall-clock-and-live-view.md)
(R6, R7). benchspec at c591efe, E2B Python SDK `e2b==2.64.0`.

## Answer up front

- **Go.** A throwaway `E2BBackend` booted cells from a benchspec snapshot,
  synced `/workspace` and `/project` both ways, and ran 100 cells at once
  with no errors. Cold start is 3 s, sync is about 1 s per cell, and a
  5-minute cell costs about $0.011.
- **One gap: no agent ran to completion in an E2B guest.** The spike
  environment's model keys work only through its own proxy, so the agent
  CLI started in the guest and was refused by the provider. Grading parity
  and agent-phase timing spread are not measured. Check them first when the
  real backend lands.
- **R7: xdist is not the cap. No async-runner issue.** At `-n 100` against
  real sandboxes the controller used 5.4 GB and 1.3 cores, and the plan's
  concurrency was never hit. Start-up work per worker is what grows.
- **Secret injection works** for `x-api-key` and `Authorization: Bearer`,
  and the Claude CLI trusts the egress proxy without extra setup.
- **The egress allowlist holds, with one hole:** all traffic to 8.8.8.8
  stays open, including DNS-over-HTTPS, unless `dns_servers=[]` is set.

## Setup

- **Account:** a paid team, not Hobby. It held 24 sandboxes at once and
  later 100. Hobby's limits (10 concurrent, 1 create/s) are not verified.
- **Auth:** the SDK calls `api.e2b.app` by default. Here the key was
  injected for `api.e2b.dev`, so runs set `E2B_API_URL`.
- **Controller host:** 4 cores, 15 GiB, Linux, Docker 29.10.
- **Guest:** 2 vCPU, 2 GiB, built from `ubuntu:latest` with Claude Code
  2.1.329. Region: not reported by the API.

## Measurements

| Item | Result | Sample |
| --- | --- | --- |
| Base template build, builder path, 2 vCPU / 2 GiB | 39 s clean, 41 s rebuilt | 1 each |
| Base template build, Dockerfile path | 41 s clean, 11 s cached | 1 each |
| Full snapshot: base, provision, bridge, seal | 38 s | 1 |
| Snapshot disk use | 660 MB of 22 GB; `memory_mb=2048` kept | 1 |
| Cold start from the snapshot, create call | p50 2.8 s, p95 3.0 s | 30 |
| Cold start, create to first command | p50 3.0 s, p95 3.3 s | 30 |
| Cold start from E2B's stock `base` template | p50 0.19 s | 10 |
| Upload `/workspace` and `/project` (1.0 MB gzipped, 271 files) | p50 0.9 s | 20 |
| Download `/workspace` | p50 0.3 s | 20 |
| Raw transfer, 60 MB file | 3.2 s up, 2.2 s down | 1 |
| Round-trip fidelity: contents, SHA-256, `gather_facts` output | Identical (local container as guest) | 3 trees |
| CPU and IO workload, fresh guest each run, E2B | median 3.85 s, p95 4.44 s, CV 8.5% | 10 |
| Same workload, local Docker `--cpus 2` | median 4.18 s, p95 4.61 s, CV 5.5% | 10 |
| Billed seconds beyond the agent phase | 1.6 s per cell | 20 |
| Cost, 2 vCPU / 2 GiB | $0.000037/s; $0.011 per 5-minute cell | list price |
| Concurrency | 100 at once, no refusal | 1 run |
| Create burst | 10 creates in 0.6 s, no 429 | 2 bursts |
| API rate limits | None hit in any run | all runs |
| Agent run end to end, grading parity, agent-phase spread | Not measured; see above | 0 |

The timing workload is shell only (hash, sort, 2,000 small files, tar). It
answers the noisy-neighbour question for CPU, not for a whole agent turn.
Keep timing comparisons on one backend.

### Fan-out against real sandboxes

Each cell creates a sandbox from the snapshot, uploads both trees, streams
a 30 s command, downloads `/workspace` and kills the sandbox. No model.

| Workers | Cells | Errors | Start-up to first cell | Upload p50 | Cell p50 | Controller memory | Controller CPU |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | 20 | 0 | 2.8 s | 0.9 s | 34.9 s | 0.6 GB | 0.2 cores |
| 50 | 50 | 0 | 11 s | 3.2 s | 38.2 s | 2.8 GB | 1.0 cores |
| 100 | 100 | 0 | 19 s | 7.0 s | 41.8 s | 5.4 GB | 1.3 cores |

Create time stays at 3 to 4 s. What grows is controller work: every worker
imports benchspec and gzips the project itself. Stage and pack once per
run and the upload cost falls back to under a second.

## SDK facts

All confirmed by running code unless marked.

- **`commands.run`** takes `envs`, `cwd`, `user`, `timeout`, `on_stdout`,
  `on_stderr`, `stdin` and `background`.
- **Default command timeout is 60 s.** `timeout=0` lifts it. A timeout
  raises `TimeoutException` and kills the guest process.
- **Non-zero exit raises `CommandExitException`** with `exit_code`,
  `stdout` and `stderr`.
- **stdin:** the research note's `stream_input` is not needed. A background
  handle has `send_stdin` and `close_stdin`; 200 kB went through.
- **`handle.kill()`** SIGKILLs the guest process; `wait()` then reports
  exit code -1. Docker's kill leaves the process running.
- **Output is decoded text.** Binary on stdout is lossy, so never move a
  tar over stdout.
- **Files:** `files.write` takes bytes or a file object; `files.read`
  returns `str`, or `bytearray` with `format="bytes"`.
- **Sandbox lifetime defaults to 300 s,** then the sandbox is killed.
  `timeout=` on create or `set_timeout()` changes it.
- **A dead sandbox raises `SandboxNotRunningException`,** a subclass of
  `TimeoutException`. Check `is_running()` to tell it from a slow command.
- **Templates:** `from_image` and `from_dockerfile` both work, contradicting
  the research note. `Template.build(..., cpu_count=, memory_mb=)` sticks.
- **Snapshots:** `create_snapshot(name, mode="filesystem")` turns a live
  sandbox into a template. It lists under snapshots, not templates.
- **Default user** is `user` on the stock template and `root` on templates
  built from an image.

## Findings

### Sync

- **Tar over the file API, one archive each way.** Cheap, and faithful for
  everything grading reads.
- **Extract with `tarfile`'s `data` filter;** the guest is untrusted. It
  rewrites `0444` and `0666` to `0644`, drops setuid, and refuses absolute
  or escaping symlinks. No checker reads modes or link targets.
- **`/project` cannot be read-only** in a remote guest. The host checkout
  is still unreachable, which is the guarantee that matters.

### Egress allowlist

Tested with `allow_out` set to the model API hosts and all else denied.

- **Allowed hosts work,** and the Claude CLI reaches its API.
- **Other hosts are blocked** on 80 and 443, by name and by IP. SSH to a
  public host and UDP DNS to 1.1.1.1 are blocked too, so UDP is filtered by
  address, contradicting the research note.
- **8.8.8.8 is fully open:** TCP 53, UDP 53 and HTTPS. A DNS-over-HTTPS
  lookup from the guest succeeded, so it is a data channel.
- **`dns_servers=[]` closes it.** With names pinned in `/etc/hosts`,
  allowed hosts still worked.
- **For `docs/sandbox.md`:** an E2B backend may claim "egress is limited to
  these hosts" only if it sets `dns_servers=[]` and pins the hosts.

### Secret injection

Tested with a dummy value against an echo service and the Anthropic API.

- **Both header styles are injected,** and the injected header replaces
  one the guest sends.
- **The value never reaches the guest:** not in its environment, not on
  disk.
- **Plain HTTP gets nothing injected.**
- **No CA setup needed for Claude Code.** It accepted the re-signing proxy
  and got the provider's own "invalid key" answer. Codex and OpenCode are
  untested.
- **Public beta;** 10 secrets on Hobby, 100 on Pro (docs).

### Failures

The throwaway wrapper maps a sandbox killed or expired under a command to
`SandboxError`, a slow command to `TimeoutError`, and a killed stream to a
`failed` event. Each was exercised against a real sandbox.

### Cleanup

A sandbox whose controller dies is killed at its own `timeout`. Create
cells with a lifetime just over the arm timeout: a leak then costs at most
$0.03.

## Proposed backend shape

The throwaway backend and every measurement script are on the scratch
branch `spike/e2b-backend-scratch`, under `scratch/e2b-spike/`.

- **Sync hook:** `async def sync_out(self, host_workdir: Path)` on
  `LiveSandbox`; a no-op on Docker and microsandbox. `SandboxSession._run`
  calls it after `agent.invoke`, before `gather_facts` reads the host
  directory. A cap that fires during `setup.sh` needs the same call before
  the guest stops. Upload stays inside `create_sandbox`.
- **Snapshot:** build a base template from `base_image` at 2 vCPU and
  2 GiB, create a sandbox, run the existing `provision`, bridge and
  environment steps, then `create_snapshot(name, mode="filesystem")`.
  Harness install scripts run unchanged.
- **Fingerprint:** `fingerprint_inputs_for("e2b", agent, env)`, unchanged.
  The snapshot name benchspec generated was accepted as a template name.
- **`image_identity`:** unavailable; E2B has a build ID, no OCI digest.
- **Credentials:** env vars first, as on Docker. Then secret injection with
  a placeholder in the guest, which also scopes each key to its host.
- **Preflight:** `E2B_API_KEY` set and `e2b` importable.
- **Prune and `discard_quietly`:** tag sandboxes with metadata, list by it
  and kill; delete `benchspec-*` snapshots and templates. Never delete by
  listing alone: a team's other templates share the project.
- **Seam changes:** `sync_out`; `ExtraVolumes` without a mount type, for
  the Codex auth file; an optional `benchspec[e2b]` extra; pack the project
  once per run.

## Spend and cleanup

- **E2B:** about 7,200 sandbox-seconds, about $0.27 at list price, counted
  from the spike's own timers and not checked against the console. Template
  build minutes are not included.
- **Model:** a few binder calls and no agent tokens. Two `hello` cells
  ran and failed at the provider's key check.
- **Left behind:** nothing. Zero sandboxes, snapshots and secrets remain,
  and the three spike templates are deleted.

## Sources

- SDK: `e2b==2.64.0` from PyPI.
- <https://docs.e2b.dev/docs/billing>
- <https://docs.e2b.dev/docs/network/internet-access>
- <https://docs.e2b.dev/docs/secrets/inject>
- <https://docs.e2b.dev/docs/sandbox/snapshots>
- <https://e2b.dev/pricing>
