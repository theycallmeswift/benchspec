# E2B backend spike

Research, 2026-11-20. Spike for an E2B sandbox backend, scoped in issue 181
and in [the wall-clock note](2026-11-12-matrix-wall-clock-and-live-view.md)
(R6, R7). benchspec at c591efe, E2B Python SDK `e2b==2.64.0`. Account: a
paid team, tier not reported by the API. Controller: 4 cores, 15 GiB,
Docker 29.10. Guest: 2 vCPU, 2 GiB, `ubuntu:latest` with Claude Code
2.1.329.

## Answer up front

- **Go.** A throwaway `E2BBackend` booted cells from a benchspec snapshot,
  synced `/workspace` and `/project` both ways, and ran 100 cells at once
  with no errors. Cold start is 3 s, sync is about 1 s per cell, and a
  5-minute cell costs about $0.011. Width 100 needs a paid plan.
- **One gap: no agent ran to completion in an E2B guest.** The spike
  environment's model keys work only through its own proxy, so the agent
  CLI started in the guest and was refused by the provider. Grading parity
  and agent-phase timing spread are not measured. Check them first when the
  real backend lands.
- **R7: xdist is not the cap. No async-runner issue.** At `-n 100` against
  real sandboxes (no model, binder or judge) the controller used 5.4 GB and
  1.3 cores, and the account's concurrency limit was not reached.
- **Secret injection works** for `x-api-key` and `Authorization: Bearer`.
- **The egress allowlist holds, with one hole:** all traffic to 8.8.8.8
  stays open, including DNS-over-HTTPS, unless `dns_servers=[]` is set.

## Measurements

| Item | Result | Sample |
| --- | --- | --- |
| Base template build, builder path, 2 vCPU / 2 GiB | 39 s clean, 41 s rebuilt | 1 each |
| Base template build, Dockerfile path | 41 s clean, 11 s cached | 1 each |
| Full snapshot: base, provision, bridge, seal | 38 s | 1 |
| Snapshot disk use | 660 MB of 22 GB; `memory_mb=2048` kept | 1 |
| Cold start from the snapshot, create call | p50 2.8 s, p95 3.0 s | 30 |
| Cold start, create to first command | p50 3.0 s, p95 3.3 s | 30 |
| Upload `/workspace` and `/project` (1.0 MB gzipped, 271 files) | p50 0.9 s | 20 |
| Download `/workspace` | p50 0.3 s | 20 |
| Round-trip fidelity: contents, SHA-256, `gather_facts` output | Identical except modes (see Sync); local container as guest, not run through E2B | 3 trees |
| CPU and IO workload, fresh guest each run, E2B | median 3.85 s, p95 4.44 s, CV 8.5% | 10 |
| Same workload, local Docker `--cpus 2` | median 4.18 s, p95 4.61 s, CV 5.5% | 10 |
| Sandbox seconds beyond the agent phase, by the spike's own timers | 1.6 s per cell | 20 |
| Cost, 2 vCPU / 2 GiB | $0.000037/s; $0.011 per 5-minute cell | list price |
| Concurrency | 100 at once, no refusal | 1 run |
| Create burst | 10 creates in 0.6 s; no 429 in any run | 2 bursts |
| Hobby limits (10 concurrent, 1 create/s) | Not verified; the account is not Hobby | 0 |
| Agent run end to end, grading parity, agent-phase spread | Not measured; see above | 0 |

The timing workload is shell only (hash, sort, 2,000 small files, tar). It
answers the noisy-neighbour question for CPU, not for a whole agent turn.
Keep timing comparisons on one backend.

### Fan-out against real sandboxes

Each cell creates a sandbox from the snapshot, uploads both trees, streams
a 30 s command, downloads `/workspace` and kills the sandbox.

| Workers | Cells | Errors | Start-up to first cell | Upload p50 | Cell p50 | Controller memory | Per worker | Controller CPU |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | 20 | 0 | 2.8 s | 0.9 s | 34.9 s | 0.6 GB | 56 MB | 0.2 cores |
| 50 | 50 | 0 | 11 s | 3.2 s | 38.2 s | 2.8 GB | 54 MB | 1.0 cores |
| 100 | 100 | 0 | 19 s | 7.0 s | 41.8 s | 5.4 GB | 54 MB | 1.3 cores |

- **Gap between one cell ending and the next starting on a worker:** p50
  2 ms at `-n 10`. Not recorded at 50 and 100, where each worker ran one
  cell; a synthetic run with no sandbox gave 17 ms p50 at `-n 100`.
- **What grows is controller work.** Create stays at 3 to 4 s, but every
  worker imports benchspec and gzips the project itself. Stage and pack
  once per run and upload falls back to under a second.

## SDK facts that shape the design

Confirmed by running code.

- **Default command timeout is 60 s.** `timeout=0` lifts it. A timeout
  raises `TimeoutException` and kills the guest process.
- **stdin:** issue 181's `stream_input` is not needed. A background handle
  has `send_stdin` and `close_stdin`.
- **`handle.kill()`** SIGKILLs the guest process; `wait()` then reports
  exit code -1. Docker's kill leaves the process running.
- **Output is decoded text.** Binary on stdout is lossy, so never move a
  tar over stdout.
- **Sandbox lifetime defaults to 300 s,** then the sandbox is killed.
  `timeout=` on create or `set_timeout()` changes it.
- **A dead sandbox raises `SandboxNotRunningException`,** a subclass of
  `TimeoutException`. Check `is_running()` to tell it from a slow command.
- **Templates:** `from_image` and `from_dockerfile` both work; the research
  note left this unconfirmed.
- **Snapshots:** `create_snapshot(name, mode="filesystem")` turns a live
  sandbox into a template. It lists under snapshots, not templates.

## Findings

### Sync

- **Tar over the file API, one archive each way.**
- **Extract with `tarfile`'s `data` filter;** the guest is untrusted. It
  rewrites `0444` and `0666` to `0644`, drops setuid, and refuses absolute
  or escaping symlinks. No checker reads modes or link targets.
- **`/project` cannot be read-only** in a remote guest. The host checkout
  is still unreachable, which is the guarantee that matters.

### Egress allowlist

Tested with `allow_out` set to the model API hosts and all else denied.

- **Other hosts are blocked** on 80 and 443, by name and by IP. SSH to a
  public host and UDP DNS to 1.1.1.1 are blocked too, so UDP is filtered by
  address. QUIC (UDP 443): not tested.
- **8.8.8.8 is fully open:** TCP 53, UDP 53 and HTTPS. A DNS-over-HTTPS
  lookup from the guest succeeded, so it is a data channel.
- **`dns_servers=[]` closes it.** With names pinned in `/etc/hosts`,
  allowed hosts still worked.

### Secret injection

Tested with a dummy value against an echo service and the Anthropic API.

- **Both header styles are injected,** and the injected header replaces
  one the guest sends.
- **The value never reaches the guest:** not in its environment, not on
  disk.
- **No CA setup needed for Claude Code.** It accepted the re-signing proxy
  and got the provider's own "invalid key" answer. Codex and OpenCode are
  untested.
- **Public beta;** 10 secrets on Hobby, 100 on Pro (docs).

### Failures and cleanup

- **Mapping:** a sandbox killed or expired under a command becomes
  `SandboxError`, a slow command `TimeoutError`, a killed stream a `failed`
  event. Each was exercised against a real sandbox.
- **Leaks:** a sandbox whose controller dies is killed at its own
  `timeout`. With a lifetime just over the arm timeout, a leak costs at
  most $0.03.

## Proposed backend shape

The throwaway backend and every measurement script are on the scratch
branch `spike/e2b-backend-scratch`, under `scratch/e2b-spike/`.

- **Sync hook:** `async def sync_out(self, sandbox, host_workdir)` on
  `SandboxBackend`, with one inherited no-op in `SharedBackendBehavior`.
  `SandboxSession._run` calls it after `agent.invoke`, before
  `gather_facts` reads the host directory. It must still run after the
  cell's cap has fired, in the agent turn or in `setup.sh`, so it cannot
  draw on the cell's remaining budget. Upload stays inside
  `create_sandbox`.
- **Snapshot:** build a base template from `base_image` at 2 vCPU and
  2 GiB, create a sandbox, run the existing `provision`, bridge and
  environment steps, then `create_snapshot(name, mode="filesystem")`.
  Harness install scripts run unchanged.
- **Fingerprint:** `fingerprint_inputs_for("e2b", agent, env)`, unchanged.
  The snapshot name benchspec generated was accepted as a template name.
- **`image_identity`:** unavailable; E2B has a build ID, no OCI digest.
- **Credentials:** env vars first, readable by the agent, as on Docker.
  Then secret injection with a placeholder in the guest, which also scopes
  each key to its host.
- **Network:** `allow_out` = provider hosts, `dns_servers=[]`, hosts
  pinned; required before `docs/sandbox.md` claims limited egress.
- **Preflight:** `E2B_API_KEY` set and `e2b` importable.
- **Prune and `discard_quietly`:** tag sandboxes with metadata, list by it
  and kill; delete `benchspec-*` snapshots and templates. Never delete by
  listing alone: a team's other templates share the project.
- **Other changes:** an optional `benchspec[e2b]` extra; pack the project
  once per run; upload the Codex auth file instead of mounting it.

## Spend and cleanup

About $0.27 of E2B credit at list price (7,200 sandbox-seconds by the
spike's timers; build minutes not included), a few binder calls, no agent
tokens. Nothing is left on E2B.

## Sources

- SDK: `e2b==2.64.0` from PyPI.
- <https://docs.e2b.dev/docs/billing>
- <https://docs.e2b.dev/docs/network/internet-access>
- <https://docs.e2b.dev/docs/secrets/inject>
- <https://docs.e2b.dev/docs/sandbox/snapshots>
- <https://e2b.dev/pricing>
