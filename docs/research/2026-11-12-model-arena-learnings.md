# Model Arena learnings

Research, 2026-11-12. What benchspec should take from MLH/model-arena, judged
by one metric: total wall-clock to finish a harness × model matrix. Sources:
model-arena at c47d2d4, benchspec at 834ef8d, E2B docs and pricing fetched
2026-11-12.

## Answer up front

- **Model-arena is fast mostly because its task is small:** one streamed API
  call per model, with no VM and no tool loop. What carries over is the shape
  of the runner: fan out everything, cap every job, never let one failure stop
  the rest.
- **benchspec's wall-clock is bound by its controller, not its sandbox.** Runs
  are sequential by default and capped at 6 workers in `make`. Each cell runs
  binder, then agent, then judge, one after another. A VM boots in about 1s,
  so boot time is not the problem.
- **Cheapest wins first:** a real worker setting, binder dedupe across the
  run, and hard per-cell caps. Then add one event stream so the CLI and an
  optional web view read the same events.
- **On cost, E2B roughly breaks even with its peers.** Its advantage is width:
  100 concurrent sandboxes on Pro, with nothing running on a laptop. That only
  pays off once the controller can fan out, so it's a time-boxed spike after
  the controller work.

## Ranked recommendations

| # | Recommendation | What it buys for matrix wall-clock | Effort | Depends on |
|---|---|---|---|---|
| 1 | **First-class worker setting with a sensible default** | The biggest single lever. Today the default is 1 worker unless you pass `-n`. | S | None |
| 2 | **Binder dedupe across the run (L1)**, always on, no setting | Removes repeated binder calls across arms and samples. It also eases the Gemini timeout that holds workers at 6. | S | None |
| 3 | **Hard per-cell wall-clock caps that keep partial output** | Bounds the slowest cell, which is what sets wall-clock under fan-out. | S | None |
| 4 | **One run event stream, written to JSONL** | Doesn't speed runs up directly, but you can see stuck or failing cells early and kill the run. | M | None |
| 5 | **Optional live web view** (CLI stays the default) | Progress and a speed and cost leaderboard while the run is going. | M | 4 |
| 6 | **E2B backend spike**, time-boxed | Concurrency beyond what local RAM allows (about 100 at once on Pro), with nothing running on a laptop. | M | 1, 2 |
| 7 | **Async cell runner**, only if the spike shows xdist is the limit | Many cells per process, without paying for one process per cell. | L | 6 |
| 8 | **Reasoning and routing settings declared on each arm** | Stable, explainable timings. The settings become part of what the arm measures. | S | None |

## Speed

### Why model-arena is fast, and what transfers

Built into its task, so it won't transfer:

- One stateless `fetch` per model, with no tools, no turns and no sandbox.
- Short, bounded outputs: one SVG, 12 lines of text, a 120-line app.
- No grading. The person at the booth judges quality.

Design choices, so they do transfer:

- Everything starts at once (`Promise.all`), so wall-clock is set by the
  slowest model.
- A per-task time cap that keeps partial output.
- Each model call never throws. It always returns `{ok, error, ms, cost}`.
- Reasoning and host routing pinned per model, timed by hand and dated in
  comments.

### Where benchspec's time goes

```text
Today: 1 worker by default, 6 in make. Each cell runs serially.
  worker 1: bind xN -> boot -> agent CLI (<= 600 s) -> judge CLI
  worker 2: bind xN -> boot -> agent CLI -> judge CLI

Target: bind once per run, then many lanes with a cap on every cell.
  controller: bind once
  lane 1: boot -> agent (capped) -> judge
  lane 2: boot -> agent (capped) -> judge
  lane 3: boot -> agent (capped) -> judge
```

Schematic, not to scale. Nothing in benchspec measures phase timings yet. The
agent run almost certainly dominates.

- **Sequential by default.** Parallelism is only pytest-xdist's `-n`, passed
  straight through. benchspec has no setting or default of its own.
- **Capped at 6 on purpose.** The Makefile comment says each cell reserves
  2 GB, and running the binder on more than about 12 workers trips Gemini's
  60s timeout.
- **Each cell runs serially:** N blocking binder calls, then the agent, then a
  judge, which is a second full agent CLI run whenever the binder hands an
  assertion to it.
- **No async fan-out anywhere:** one `asyncio.run` per cell, and one cell per
  process at a time.

### R1: Worker setting (S)

Make worker count a `benchspec run --workers` flag with a project default in
config. Default it to an auto value based on cores and free RAM (2 GB per cell
locally). `-n` keeps working underneath.

### R2: Binder dedupe across the run, L1 (S)

`bind()` depends only on the assertion text, the binder model and the prompt
template, so it's safe to compute once per run. Today it is deduplicated only
within a single cell. It is always on with no setting, and failed binds are
never stored. xdist workers are separate processes, so the shared result has
to be computed in one place. Options:

- **Bind before cells start (recommended).** The controller collects the
  unique assertions after collection, binds them with a bounded thread pool,
  and hands the specs to each cell. A failed bind is left empty, so that cell
  binds it itself as it does today.
- **Shared store across workers.** A file-locked store under the iteration
  directory that workers fill on demand. No upfront pass, but it adds locking
  and races to deal with.

Not doing: a persistent `.benchspec.cache` kept across runs.

### R3: Hard per-cell caps (S)

The arm timeout is already 600s. Make it a cap over the whole cell (bind,
boot, agent and judge together), and when it fires, grade whatever the agent
left in `/workspace`. Record a timeout as a result, not an infra error. In
model-arena, a timed-out model still shows its partial answer.

### R7: More workers or an async runner

- **More `-n` plus R1 and R2 (recommended now).** Keeps pytest. Locally, RAM
  is the limit. On E2B, each worker is a light process waiting on a remote VM.
- **Async cell runner (L).** Cells run under a semaphore in one process, with
  the binder and judge made async. Wide fan-out with few processes, but it
  means rewriting the runner and moving off xdist.
- **Hybrid.** Each xdist worker runs K async cells. Keeps pytest reporting and
  multiplies width.

Revisit after the E2B spike measures whether one process per cell is the real
cap.

### R8: Reasoning and routing settings on each arm (S)

Model-arena turns reasoning down to go fast. A benchmark can't do that
quietly, because those settings are part of what is being measured. Make
reasoning effort and provider routing explicit fields on each arm, record them
in provenance, and give them dated comments the way `models.mjs` does.

## Live view: one event stream, several ways to watch it

Model-arena has one output channel, `send()`. The Node server and the
Cloudflare Worker are just two places that channel can write to. benchspec
should copy that: every run emits one event stream, and the CLI progress line,
a JSONL artifact and an optional web view all read from it. The CLI stays the
default, and the web view is opt-in.

| Event | Payload | Emitted from |
|---|---|---|
| `run.start` | arms, evals, cell count, config hash | controller, after collection |
| `cell.start` | eval, arm, sample, worker | `run_eval_arm` |
| `cell.phase` | bind · boot · setup · agent · grade, with t | `SandboxSession` lifecycle |
| `cell.delta` | tool call or text line | a general version of `exec_watched` (today used only for early stop) |
| `cell.done` / `cell.error` | pass rate, ms, first-output ms, cost if reported, error kind | after grading |
| `run.end` | totals, report paths | `pytest_sessionfinish` |

Each xdist worker appends to its own `events-<worker>.jsonl` in the iteration
directory. That avoids interleaved writes, and the files themselves are the
machine-readable artifact.

Options for the web view:

- **`benchspec watch` (recommended).** A separate command that tails the JSONL
  files and serves a local page over SSE. It's independent of pytest and
  xdist, and works on a run in progress or one that has finished.
  `run --watch` can just start it for you.
- **SSE server inside `run`.** Lives in the controller process. Closer to
  model-arena, but tangled up with pytest's lifecycle, and it goes away when
  the run ends.
- **Terminal UI (rich live).** A live view with no browser. A good second way
  to watch, but it can't show live SVG or HTML or sort a leaderboard.

Worth copying from the page:

- An "N of M finished" counter.
- One tile per cell with its state: waiting, booting, working, done or failed.
- A leaderboard sortable by time, first-output time, cost and pass rate. Show
  each as a ratio to the best, and drop unfinished cells to the bottom.
- Cost as the provider reports it, never from a pricing table of our own.

## E2B: a way to get width, not a cost cut

**Cost check:** a 5-minute cell at 2 vCPU / 4 GiB costs about $0.014, so 200
cells come to about $2.76. That's about the same rate as Daytona and a little
under Modal. The case for E2B is concurrency and taking load off laptops, not
price.

| Plan | Concurrent | Creates / s | Max session | 200 × 5-min cells | Monthly fee |
|---|---|---|---|---|---|
| Hobby | 10 (unverified: some sources say 20) | 1 | 1 h | ~100 min | $0, plus a one-time $100 credit |
| Pro | 100 | 5 | 24 h | ~10 min | $150, plus usage |
| Pro + add-on | 600 | 5 | 24 h | ~5 min, all cells at once | $650 |

### Fit with benchspec

- **The seam exists.** `registered_backends()` says a third backend is just an
  addition. A new backend implements `SandboxBackend` and `LiveSandbox`, and
  snapshot build and lookup map to E2B templates.
- **The real gap is bind mounts.** Cells mount `/workspace` (read-write) and
  `/project` (read-only) from the host, and grading reads the host directory.
  On E2B, the backend has to upload on create and download `/workspace` before
  grading. That probably needs a sync hook on `LiveSandbox`.
- **Credentials work like Docker's:** environment variables, not
  microsandbox's network-scoped secrets. E2B's beta outbound-header rules
  might keep keys out of the VM. Not verified.

### Risks

- **Timing from shared CPUs is noisy.** E2B's CPUs are shared, which would
  skew any timing benchspec reports. Keep timing comparisons on one backend.
- **You pay for idle time.** Resources are reserved while the agent waits on
  the model.
- **Egress allowlist gaps:** only HTTP and TLS on ports 80 and 443 are
  filtered. UDP and QUIC aren't supported, and allowing any domain also opens
  8.8.8.8.
- **Creation rate limits wide bursts:** 1 per second on Hobby, 5 on Pro.
  Stagger cell starts.
- **Lock-in:** the template builder and SDK are E2B-specific. The infra is
  open source (Apache-2.0) and E2B-compatible hosts exist, which lowers the
  risk.

### R6: Time-boxed spike, after R1 and R2 (M)

Build a minimal `E2BBackend`, run the `e2e/hello` suite on Hobby, then try one
Pro month at `-n 50`. Things to verify:

- Cold start p50 and p95 for our snapshot size. Published numbers are vendor
  claims.
- Python SDK signatures for streaming stdout, `envs`, `files` and timeouts.
- Whether a template can be built from our Dockerfile or has to use the
  `Template()` builder.
- How long uploading and downloading the workspace adds per cell.
- The egress allowlist for model API hosts, and how to inject secrets.
- Timing spread: the same cell 10 times on E2B and 10 times locally.
- Hobby's real concurrency limit.

## Other learnings

- **A fake streaming backend in tests.** `fetch` and `now` are injected, the
  fake streams small chunks plus a cost frame, and server tests check the
  event order: `race` first, then one `done` per model, then `end`.
  benchspec's `FakeSandbox` could check the event stream the same way.
- **Dated config comments from real measurements.** `models.mjs` records why
  each host was pinned and when it was timed. Do the same for arm defaults.
- **A core with no dependency on where it runs.** `core.mjs` uses only fetch,
  streams and TextDecoder, and takes `send`, `signal` and `fetchImpl` as
  parameters. The runner core shouldn't know whether it's writing to a
  terminal, a JSONL file or SSE.
- **Provider-reported cost.** Read `usage.cost` from the response, and show
  "not reported" rather than estimating.
- **Roster checks as tests:** unique IDs, valid model IDs, open and closed
  models both present. These map to arm-config lint.

## What not to copy

- **No grading.** Model-arena only measures speed and cost. benchspec's point
  is the graded matrix.
- **No retries.** Model-arena fixes a failing host by editing the roster.
  benchspec should keep counting infra errors separately and consider
  retrying them.
- **Starting everything at once with no limit.** That's fine for 20 API calls
  but not for sandboxes, which hit create-rate limits, RAM and binder
  timeouts. Use a bounded semaphore.
- **Reasoning turned down for speed.** In a benchmark, that changes what's
  being measured (see R8).
- **Workers or stateless hosting.** The JSONL and report artifacts need a
  filesystem.

## Non-goals and next steps

Non-goals:

- Removing the CLI runner. The web view is optional.
- A persistent `.benchspec.cache` file. Binder dedupe stays within one run.
- Building anything in this pass.

Next steps:

- Spec R1 to R3 together as one controller change. Spec R4 and R5 next.
- Plan the E2B spike with its list of things to verify.

## Sources

### model-arena (MLH/model-arena at c47d2d4)

- Fan-out: `app/server.mjs:119`, `worker/src/index.js:43`
- One streamed call per model: `app/core.mjs:86-114`
- Per-task caps: `core.mjs:93`, `models.mjs:81,89,98,127`
- Partial output kept: `core.mjs:26-31,122`
- Calls that never throw: `core.mjs:75-78,120-125`
- Reasoning and routing pins: `core.mjs:90`, `models.mjs:12-28,46-54`
- Event shapes: `core.mjs:84,112,117-123`, `server.mjs:116-123`
- Leaderboard: `app/page/app.mjs:229-288,384-391`
- Host-independent core: `core.mjs:1-4,79`
- Tests: `app/test/model-arena.test.mjs:32-42,106-129,205-226`

### benchspec (at 834ef8d)

- Run lifecycle: `src/benchspec/runners/run.py:186-201`, `__main__.py:249-283`
- Serial cell: `orchestration/execution.py:171-190,236-275,474-654`
- Worker cap reasoning: `Makefile:14-28`
- Default timeouts: `agents/base.py:62`, `grading/judges/config.py:31`
- Binder purity: `grading/binder.py:482-507`
- Sandbox seam: `sandbox/backend.py`, `sandbox/registry.py`
- Bind mounts: `docker.py:701-721`, `microsandbox.py:354-375`
- Stream tap: `agents/base.py` (`exec_watched`)
- Report at session end: `runners/pytest.py:340-480`

### E2B and alternatives (fetched 2026-11-12)

Items marked unverified were not confirmed against a primary source.

- Pricing and plans: <https://e2b.dev/pricing>
- Rate limits and concurrency: <https://docs.e2b.dev/sandbox/rate-limits>
  (unverified: Hobby 10 vs 20)
- Egress control: <https://docs.e2b.dev/docs/sandbox/internet-access>
- Templates: <https://docs.e2b.dev/docs/template/quickstart> (unverified:
  Dockerfile path unconfirmed)
- BYOC: <https://docs.e2b.dev/byoc>
- Cold start and resume:
  [fly.io comparison](https://fly.io/learn/sprites-vs-e2b-vs-modal-vs-daytona/)
  (unverified: vendor claims, no published p50/p99)
- Idle billing: <https://fly.io/learn/ai-sandbox-pricing/>
- Peer pricing:
  [marktechpost, Aug 2026](https://www.marktechpost.com/2026/08/27/best-agent-sandboxes-2026-cold-start-pricing-network-policy/)
  (unverified: secondary source)
- Unverified: Python SDK signatures, secrets API, region availability
