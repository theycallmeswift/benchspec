# E2B backend spike: what is settled without an E2B account

Research, 2026-11-20. Spike for an E2B sandbox backend, scoped in issue 181
and in [the wall-clock note](2026-11-12-matrix-wall-clock-and-live-view.md)
(R6, R7). benchspec at 6898ab0, E2B Python SDK `e2b==2.64.0`, E2B docs
fetched 2026-11-20.

## Status up front

- **The spike is incomplete.** The environment it ran in had no E2B API key
  (`E2B_TOKEN` and `E2B_API_KEY` both unset), so nothing here was run against
  the E2B service. No sandbox or template was created. E2B spend: $0. Model
  spend: $0.
- **No go or no-go yet.** Cold start, timing spread, real concurrency, egress
  and secret injection decide it, and all need the service. Nothing found so
  far argues against E2B; see [the provisional lean](#go-or-no-go).
- **Settled from the SDK source and docs:** the SDK shape, the template path,
  lifetime and limits, prices, and what the egress filter and secret
  injection promise.
- **Settled by local measurement:** a tar round trip keeps everything grading
  reads, and 100 xdist workers waiting on remote cells cost about 3 GB and no
  wall-clock. R7 is provisionally "no async runner needed".
- **Found on the way:** `gather_facts` raises `FileNotFoundError` on a
  dangling symlink in `/workspace`, on every backend.

Evidence labels used below: **source** (read in the SDK at 2.64.0, not run),
**docs** (vendor claim), **local** (measured here, no E2B), **not run**
(needs an API key).

## Measurements

Host for local runs: 4 cores, 15 GiB, Linux, Docker 29.10. Region and plan:
none, no account was used.

| Item | Result | Evidence | Sample |
| --- | --- | --- | --- |
| Authenticates with `E2B_API_KEY` | Not run | not run | 0 |
| `commands.run` options | `envs`, `cwd`, `user`, `timeout`, `on_stdout`, `on_stderr`, `stdin`, `background` all present | source | n/a |
| Default command timeout | 60 s; `timeout=0` lifts it | source | n/a |
| Default sandbox lifetime | 300 s, then killed; `timeout=` on create or `set_timeout()` extends it, to 1 h on Hobby | source, docs | n/a |
| Template from a Dockerfile | `Template().from_dockerfile(path_or_text)` and `from_image(ref)` both exist | source | n/a |
| `memory_mb` sticks at 2 GiB | Not run | not run | 0 |
| Cost of a 2 vCPU / 2 GiB cell | $0.000037/s, $0.0022/min, $0.011 per 5-minute cell | docs | n/a |
| Template build time, clean and cached | Not run | not run | 0 |
| Template size | Not run | not run | 0 |
| Cold start p50 / p95 | Not run | not run | 0 of 30 |
| Sync cost, `hello` eval folder (3 files, 855 B) | 0.5 s round trip, all of it `docker exec` start-up | local | 1 |
| Sync cost, staged benchspec repo (271 files, 3.7 MB, 1.0 MB gzipped) | 0.6 s round trip | local | 1 |
| Sync cost, 5,000 files, 100 MB (57 MB gzipped) | 11 s round trip, 8.6 s of it gzip | local | 1 |
| Sync cost over the E2B network | Not run | not run | 0 |
| Fidelity: contents and SHA-256 | Identical; `gather_facts` output identical on all three trees | local | 3 trees |
| Fidelity: empty dirs, relative symlinks, exec bit, `0600`, Unicode names | Kept | local | 1 tree |
| Fidelity: `0444`, `0666`, setuid | Changed by safe extraction (see below) | local | 1 tree |
| Fidelity: absolute and escaping symlinks | Refused by safe extraction | local | 1 tree |
| Timing spread, 10 × E2B and 10 × Docker | Not run | not run | 0 |
| Idle billing, billed seconds vs agent wall-clock | Not run; billing is per second while running | docs | 0 |
| Real Hobby concurrency | Docs now say 10; not probed | docs | 0 |
| Create rate, burst of 10 | Not run; docs say 1/s on Hobby and do not say what an over-limit create returns; the SDK retries a 429 three times | docs, source | 0 |
| Egress allowlist end to end | Not run | not run | 0 |
| Egress filter scope | Ports 80 and 443 only; UDP and QUIC unfiltered; all traffic to 8.8.8.8 open unless `dns_servers=[]` | docs | n/a |
| Secret injection | Public beta, no plan gate stated, 10 secrets on Hobby; not run | docs | 0 |
| API rate limits in a wide run | Not run; listed limits cover `GET` list endpoints only | docs | 0 |
| xdist at `-n 10` / `50` / `100`, synthetic remote cells | Wall-clock within 1% of ideal; 30 MB per worker | local | 30 / 150 / 300 cells |
| xdist against real E2B sandboxes | Not run | not run | 0 |
| Grading parity with Docker | Not run | not run | 0 |
| Failure classification | Mapping written, not exercised | source | n/a |
| Cleanup after a dead controller | Sandbox dies at its own `timeout`; not observed | docs | 0 |

## SDK facts from the research note

Each line is confirmed or contradicted against the SDK source at 2.64.0.
None was confirmed by running code.

- **`AsyncSandbox.create()` exists.** Confirmed. It takes `template`,
  `timeout`, `metadata`, `envs`, `network`, `lifecycle` and `cpu_count`.
- **`commands.run(..., background=True, stdin=True, on_stdout=...)`.**
  Confirmed, with `envs`, `cwd`, `timeout`, `on_stderr` and `user` as well.
- **`commands.stream_input` / `close_stdin`.** Partly contradicted. The
  handle a background command returns has `send_stdin(data)` and
  `close_stdin()`; that pair is the simpler fit for benchspec's one-shot
  `stdin: bytes`.
- **Default command timeout.** 60 s. A 600 s agent turn must pass
  `timeout=0` (no limit) and keep its own deadline.
- **Non-zero exit.** `wait()` raises `CommandExitException`, which carries
  `exit_code`, `stdout` and `stderr`. A backend catches it and returns a
  normal result.
- **`kill` on a background command.** Confirmed: `handle.kill()` sends
  SIGKILL to the guest process. That is stronger than Docker's, where a
  killed `docker exec` leaves the guest process running until teardown.
- **Output is text, not bytes.** New. The SDK decodes stdout and stderr as
  UTF-8 with replacement before any callback sees them. `ExecStreamEvent.data`
  is `bytes`, so a backend re-encodes, and binary on stdout is lossy. Never
  move a tar over stdout.
- **`files.write` takes a file object or bytes iterator; `write_files` takes
  a list.** Confirmed. Still no directory or tar upload, and no size limit
  in the source.
- **`files.read` return type.** `str` by default, `bytearray` with
  `format="bytes"`, an async byte stream with `format="stream"`.
- **Default sandbox timeout.** 300 s, then the sandbox is killed and its
  state is gone. A cell must create with `timeout` above its arm timeout.
- **No documented Dockerfile path.** Contradicted. `from_dockerfile` parses
  a Dockerfile into builder steps, and `from_image` starts from any registry
  image, so `base_image` maps directly.
- **`Template.build(template, name, cpu_count=, memory_mb=)`.** Confirmed.
- **Default user.** New. Commands run as a non-root default user unless
  `user="root"` is passed. benchspec guests assume root (`/root/.codex`).
- **Sandbox snapshots.** New, and it changes the template plan.
  `sandbox.create_snapshot(name, mode="filesystem")` persists a running
  sandbox as a named template that `AsyncSandbox.create(name)` boots from.
- **Secrets.** Confirmed in shape: `Secret.create(name, value)`,
  `Secret.fill(name)`, and `network.rules[host][].transform.headers`.
- **Errors.** `SandboxNotRunningException` (sandbox killed or timed out)
  subclasses `TimeoutException`, so one `except` cannot tell a slow command
  from a dead sandbox. A backend checks `is_running()` to split them.
  `RateLimitException` is the 429.
- **Listing for prune.** `AsyncSandbox.list(query=SandboxQuery(metadata=...))`
  filters by metadata; `AsyncTemplate.list()` and `delete()` cover templates.

## Local measurements in detail

### Tar round trip

A local container stood in for the guest. The host packs a tree with
`tarfile`, the guest unpacks it into `/workspace`, the guest packs
`/workspace`, and the host unpacks with `tarfile`'s `data` filter. The
commands are the ones an E2B backend would run; only the network leg is
missing, so times are a floor.

- **What grading reads survives.** `gather_facts` returned identical trees,
  contents and SHA-256s for the `hello` folder, the staged repo and the
  5,000-file tree. No checker in `src/benchspec` reads file modes or
  symlink targets.
- **Safe extraction changes some modes.** The guest is untrusted, so the
  host must extract with the `data` filter. It turns `0444` and `0666` into
  `0644` and drops setuid. The exec bit and `0600` are kept.
- **Safe extraction refuses hostile links.** A symlink to `/etc/passwd` or
  to `../../outside` raises `AbsoluteLinkError` or
  `LinkOutsideDestinationError`. A backend skips the member and records it.
  Bind mounts keep such links today, so this is a small, deliberate
  difference.
- **Cost is small for real projects.** Under a second for the benchspec
  repo. A 100 MB tree costs 11 s, mostly gzip; skip compression or use
  `zstd` if large projects matter.
- **`/project` cannot be read-only.** A remote sandbox has no read-only
  mount, and root ignores file modes. Nothing can write back to the host
  checkout either way, so the guarantee that matters holds.

### Controller fan-out

A synthetic suite stood in for remote cells: each test imports the modules
a cell worker carries, opens an event loop for 15 s and drains a stream of
events, as a worker waiting on a remote sandbox does. It ran under xdist
with the benchspec plugin loaded.

| Workers | Cells | Cell phase, measured / ideal | Start-up to first cell | Peak memory, all processes | Per process | Gap between cells on one worker, p50 / p95 |
| --- | --- | --- | --- | --- | --- | --- |
| 10 | 30 | 45.2 s / 45.0 s | 1.0 s | 329 MB | 30 MB | 5 ms / 8 ms |
| 50 | 150 | 45.3 s / 45.0 s | 3.6 s | 1.5 GB | 29 MB | 18 ms / 28 ms |
| 100 | 300 | 45.4 s / 45.0 s | 7.2 s | 3.0 GB | 30 MB | 17 ms / 37 ms |

Memory is proportional set size. CPU averaged under half a core at every
width; it was sampled once a second, so treat it as a ceiling, not a figure.

What this leaves out: a real cell also runs the binder fallback and the
judge CLI on the controller host, and parses a transcript. Those costs are
the same on any backend and are what caps local runs today.

## Proposed backend shape

Provisional: written against the SDK, not run. The throwaway backend and
the two measurement scripts are on the scratch branch
`spike/e2b-backend-scratch` under `scratch/e2b-spike/`.

### Where the sync hook lives

On `LiveSandbox`, called by `SandboxSession`:

```python
class LiveSandbox(Protocol):
    async def sync_out(self, host_workdir: Path) -> None:
        """Make the host workdir match the guest's /workspace."""
```

- **Why `LiveSandbox`.** The live guest already knows how to reach itself.
  Docker and microsandbox implement it as a no-op.
- **Where it is called.** At the end of `SandboxSession._run`, after
  `agent.invoke` returns and before `_read_authored`. `_run_arm_turns` calls
  `gather_facts(workdir)` while the session is still open, so the pull must
  finish inside the turn, not in `__aexit__`.
- **On a cell timeout.** The per-cell cap (PR 190, open at the time of
  writing) grades the workspace as it stands. A cap that fires in the agent
  turn still returns from `invoke`, so the call above covers it. A cap that
  fires during `setup.sh` does not: that path needs its own `sync_out`
  before the guest stops, or a remote cell grades the pre-setup tree.
- **Removing a cancelled boot.** PR 190 adds `discard_quietly(name)`. E2B
  sandboxes are addressed by ID, not name, so the backend stores the cell
  name in metadata and looks it up.
- **Upload stays inside `create_sandbox`.** It already receives
  `host_workdir` and `host_repo_root`. No seam change.

```python
async def sync_out(self, host_workdir: Path) -> None:
    await self._checked(f"tar -czf {GUEST_ARCHIVE} -C {GUEST_WORKDIR} .")
    payload = await self._sandbox.files.read(
        GUEST_ARCHIVE, format="bytes", user="root"
    )
    for child in host_workdir.iterdir():  # deletions must propagate
        shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
    with tarfile.open(fileobj=io.BytesIO(bytes(payload)), mode="r:gz") as archive:
        archive.extractall(host_workdir, filter="data")
```

### Template and fingerprint mapping

- **Fingerprint.** `fingerprint_inputs_for("e2b", agent, env)`, unchanged.
- **`build_snapshot`.** Mirror Docker's run, provision, commit:
  1. Build a base template from `base_image` with `from_image`, sized
     `cpu_count=2, memory_mb=2048`.
  2. Create a sandbox from it and run `agent.provision`,
     `bridge_skills_home` and `run_environment_script` unchanged.
  3. `create_snapshot(name, mode="filesystem")`, then kill the sandbox.

  This keeps the harness `provision` scripts as they are. The alternative,
  re-expressing them as `run_cmd` steps, needs every harness to expose its
  install as text. Not verified: that a snapshot keeps the base template's
  2 GiB, and that filesystem-only snapshots are enabled on Hobby (the SDK
  documents a 400 when they are not; a full snapshot is the fallback).
- **`snapshot_exists`.** `AsyncTemplate.exists(name)`. It is a network call
  per worker at start-up, where Docker's is local.
- **`image_identity`.** Unavailable: E2B exposes a template and build ID,
  not an OCI digest. Record the build ID once the provenance schema allows.
- **Template names.** benchspec's snapshot names fit E2B's `name:tag` form
  only if the fingerprint moves into the tag or the name stays tag-free.
  Not verified against the API's naming rules.

### Credentials

- **First version: plain env vars**, passed as `envs=` on create. Same
  exposure as Docker, documented the same way.
- **Later: secret injection**, if it holds up. Three things to check first:
  - The egress proxy re-signs TLS for hosts with a transform. A harness
    CLI that carries its own CA bundle, as Node programs do, needs
    `NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt` in the guest.
  - A harness CLI probably refuses to start without a key, so the guest
    would still hold a placeholder. The transform overrides the header on
    the wire.
  - Injection fails open: an unresolved secret forwards the request with
    the header removed. The symptom is a 401 from the provider, which must
    be read as an infra error, not an agent failure.
- **The Codex auth file** rides `extra_volumes` as a bind mount today. On
  E2B it is one more upload, written with mode `0600`.

### Preflight and prune

- **Preflight.** `E2B_API_KEY` set, and the `e2b` package importable. The
  key never goes in a file, a log or a template.
- **Prune.** Tag every sandbox with `metadata={"owner": "benchspec"}`, list
  by that metadata and kill; list templates and delete those named
  `benchspec-*`. With no key set, do nothing. `sandbox:clean` already loops
  over registered backends, so it needs no change.
- **Leak bound.** Create every sandbox with `timeout` a little over the arm
  timeout. If the controller dies, E2B kills the sandbox at that deadline,
  so a leaked cell costs at most about $0.03.

### Failure classification

| SDK exception | Becomes |
| --- | --- |
| `CommandExitException` | a normal result with its exit code |
| `TimeoutException`, sandbox still running | `TimeoutError` |
| `TimeoutException`, sandbox gone (`SandboxNotRunningException`) | `SandboxError` |
| `RateLimitException`, `SandboxException`, create failures | `SandboxError` |

### Changes the seam needs

- `LiveSandbox.sync_out`, and no-op implementations on both backends and
  `FakeSandbox`.
- `ExtraVolumes` assumes bind mounts. A remote backend needs "host file to
  guest path" without a mount type.
- A `user` for guest commands, or a template whose default user is root.
- A create stagger: one create per second on Hobby, five on Pro. Today
  every worker creates at once.
- An optional extra, `benchspec[e2b]`, with imports inside method bodies
  the way `microsandbox.py` does it.

## Go or no-go

**Withheld.** The decision rests on numbers this spike could not take.

Provisional lean: go. The seam change is one method, the SDK covers every
`LiveSandbox` call, sync is cheap and faithful, and the controller is not
in the way. What would turn it into a no-go:

- Cold start p95 above about 10 s for a snapshot with an agent CLI in it.
- A coefficient of variation for the agent phase well above local Docker's.
- A harness that cannot run behind the egress allowlist.
- Snapshots that do not keep 2 GiB, with no other way to size a cell.

## R7: is xdist the cap?

**Provisionally no; no async-runner issue filed.** At `-n 100` the
controller spent 3 GB and under half a core, and wall-clock tracked the
ideal within 1%. On a 16 GB laptop that leaves the plan's concurrency
limit, not the process model, as the cap. Confirm against real sandboxes
at `-n 10`; the open risk is create-rate throttling, which a stagger fixes
and an async runner would not.

## What `docs/sandbox.md` can claim about egress

From the docs, not tested. An allowlist of model API hosts does not mean
"the agent cannot reach arbitrary hosts":

- Only ports 80 and 443 are filtered by name. Other ports follow IP rules.
- UDP and QUIC are not filtered by name.
- All traffic to 8.8.8.8 is open once any domain is allowed, unless
  `dns_servers=[]` is passed and names are pinned in `/etc/hosts`.
- On shared CDNs, allowing one hostname can allow others on the same
  endpoint.

A backend doc should say "HTTP and TLS egress is limited to these hosts",
and no more.

## Remaining runs

Each needs `E2B_API_KEY`. Estimated E2B cost at $0.0022 per sandbox-minute;
model cost on the cheapest `hello` arm.

| Step | Sandbox-minutes | E2B cost | Model calls |
| --- | --- | --- | --- |
| Auth and SDK smoke test | 2 | under $0.01 | 0 |
| Template build, two ways, clean and cached | 10 | $0.02 | 0 |
| 30 cold starts | 5 | $0.01 | 0 |
| `hello` through the throwaway backend | 10 | $0.02 | 5 cells |
| 10 × E2B and 10 × Docker timing | 20 | $0.05 | 20 cells |
| Concurrency and create-rate probe | 5 | $0.01 | 0 |
| Egress and secret injection | 10 | $0.02 | 2 cells |
| Fan-out at `-n 10` | 20 | $0.05 | 0, synthetic |
| **Total** | **about 80** | **under $0.25** | **about 27 cells** |

Not planned: `-n 50` and `-n 100` against real sandboxes. They need Pro at
$150 a month.

## Sources

- SDK: `e2b==2.64.0` from PyPI, modules `sandbox_async`, `template`,
  `template_async`, `secret`, `exceptions`, `retry`.
- <https://docs.e2b.dev/docs/billing>, which now also serves the rate-limit
  page.
- <https://docs.e2b.dev/docs/network/internet-access>
- <https://docs.e2b.dev/docs/secrets/inject>
- <https://docs.e2b.dev/docs/sandbox/snapshots>
- <https://docs.e2b.dev/docs/faq/sandbox-lifetime>
- <https://e2b.dev/pricing>: the calculator's values work out to $0.000014
  per vCPU-second and $0.0000045 per GiB-second.
