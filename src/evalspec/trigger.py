"""Trigger-eval routing detection: did the skill actually fire for a query?

Routing runs inside a microVM (`sandbox.route_in_sandbox`); this module holds the pure
detection + scoring logic it feeds. `dispatches_skill` spots the first skill dispatch in a
stream-json line (the sandbox router early-stops there — routing is decided once a skill is
picked, and waiting out the turn burns minutes); `streamed_activity` tells a budget timeout
that did real work (a clean non-fire) from a launch stall (a retryable `RoutingError`);
`detect_skill_fired` decides whether the fire was OUR skill. `count_fires` drives the
3×/threshold routing loop, retrying transient `RoutingError`s. `dispatches_skill` and
`streamed_activity` are public so a `CodingAgent.detect_dispatch` impl can reuse the
Claude Code event-shape match without crossing a private boundary.
"""

from __future__ import annotations

import json
import time


class RoutingError(RuntimeError):
    """A routing subprocess genuinely failed — a non-zero exit, or no model
    activity at all (a budget timeout that streamed only the startup line, or
    nothing). Distinct from a clean run where the skill simply didn't fire — which
    includes a budget timeout after the agent began a turn but didn't route in
    time. The difference matters: a failed call counted as a non-fire is a false
    negative that looks exactly like a real routing result."""


def _tool_uses(line: str):
    """Yield each tool_use block in one stream-json line; skip empty/malformed lines.

    Names/inputs can be null in a partial event, so callers guard their own string ops
    — a stray null must not crash routing (the call site retries subprocess failures,
    not AttributeErrors, so a crash here would be fatal)."""
    line = line.strip()
    if not line:
        return
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return
    if not isinstance(event, dict):
        return  # valid JSON but a non-object line (bare scalar/array)
    message = event.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            yield block


def detect_skill_fired(stream_lines, skill_name: str) -> bool:
    for line in stream_lines:
        for block in _tool_uses(line):
            name = block.get("name")
            inp = block.get("input")
            inp = inp if isinstance(inp, dict) else {}
            # Primary path: Skill tool with input["skill"] containing the skill name.
            # Matches exact ("my-skill") or namespaced ("my-plugin:my-skill").
            if name == "Skill":
                skill_value = inp.get("skill")
                if isinstance(skill_value, str) and (
                    skill_value == skill_name or skill_value.endswith(f":{skill_name}")
                ):
                    return True
            # Fallback: tool_use whose name itself is the skill (exact or namespaced).
            elif isinstance(name, str) and (
                name == skill_name or name.endswith(f":{skill_name}")
            ):
                return True
    return False


def first_dispatched_skill(stream_lines) -> str | None:
    """The skill the agent routed to first — the value of the first `Skill`
    tool_use's `input["skill"]`, or None if no skill was dispatched.

    Observability only (which skill won the route), distinct from the gate's
    `detect_skill_fired`, which asks whether OUR skill fired anywhere in the
    stream. On a clean fire they agree; on a miss this names the sibling that
    fired instead (the "what stole my route?" signal). Only the `Skill`-tool
    path carries a routable name; the namespaced-tool fallback isn't name-
    resolvable here, so it reads as None — rare in practice."""
    for line in stream_lines:
        for block in _tool_uses(line):
            if block.get("name") == "Skill":
                inp = block.get("input")
                skill = inp.get("skill") if isinstance(inp, dict) else None
                if isinstance(skill, str) and skill:
                    return skill
    return None


def dispatches_skill(line: str, skill_name: str | None = None) -> bool:
    """True if the line shows a skill being routed to. Routing is decided there, so the
    sandbox router stops rather than wait out the skill's (possibly minutes-long) work.

    Matches the two fire shapes `detect_skill_fired` recognizes: any `Skill` tool_use
    (the agent routed to *some* skill), and — when `skill_name` is given — a tool_use
    whose name is our skill (the namespaced-tool fallback). Keeping the two detectors
    aligned means a fire via the fallback path isn't missed by the early-stop and then
    cut by the watchdog."""
    for block in _tool_uses(line):
        name = block.get("name")
        if name == "Skill":
            return True
        if skill_name and isinstance(name, str) and (
            name == skill_name or name.endswith(f":{skill_name}")
        ):
            return True
    return False


def streamed_activity(stream_lines) -> bool:
    """True if the model began a turn (an `assistant` event), not just the startup
    `system`/init line. A budget timeout after real activity is a genuine non-fire
    (the agent worked but didn't route in time); a timeout that streamed only the
    init line is a launch stall worth retrying, not a routing answer."""
    for line in stream_lines:
        text = line.strip()
        if not text:
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "assistant":
            return True
    return False


def fire_threshold(mode: str, should_trigger: bool, passes: int) -> int:
    """Minimum fires (out of `passes`) for `fired=True`, per scoring mode.

    - `majority`  — `> half` of the passes. The robust default: the skill must
      route the query *reliably*, so a single flaky fire/miss doesn't decide it.
    - `best-of`   — a single fire. Lenient: "can the skill route this at all?".
      Makes positives forgiving but negatives strict (one spurious fire fails).
    - `asymmetric`— `best-of` for should-trigger positives (a query that routes
      even once counts), `majority` for should-not negatives (so a lone spurious
      fire doesn't fail a near-miss). The lenient-positive / robust-negative mix.
    """
    majority = passes // 2 + 1
    if mode == "majority":
        return majority
    if mode == "best-of":
        return 1
    if mode == "asymmetric":
        return 1 if should_trigger else majority
    raise ValueError(f"unknown trigger mode: {mode!r}")


def xfail_applies(xfail: dict, model: str) -> bool:
    """Whether a tier-scoped xfail relaxes the gate for `model`.

    `xfail["models"]` lists the tiers where the query is a known routing failure.
    The mark is attached only when the running model is one of them; any other
    tier runs strict, so an opus run still fails on a regression a sonnet-scoped
    xfail would otherwise mask. The match is substring-tolerant so a bare alias
    ("sonnet") and a provider-qualified id ("anthropic/claude-sonnet-4-6") both
    resolve to the same tier."""
    return any(tier in model for tier in xfail["models"])


def trigger_record(query: dict, *, mode: str, threshold: int, fires: int,
                   per_pass: list, model: str) -> dict:
    """The persisted per-sample trigger artifact. Carries the VERDICT (`fired`,
    `passed`) and the query text, not just the raw counts — so a report or external
    aggregator never has to reimplement the threshold rule, and a failing query is
    diagnosable from artifacts alone."""
    fired = fires >= threshold
    record = {
        "slug": query["slug"],
        "model": model,
        "query": query["query"],
        "should_trigger": query["should_trigger"],
        "mode": mode,
        "threshold": threshold,
        "fires": fires,
        "fired": fired,
        "passed": fired == query["should_trigger"],
        "passes_run": len(per_pass),
        "per_pass": per_pass,
    }
    if query.get("xfail"):
        record["xfail"] = query["xfail"]
    return record


def count_fires(
    query: str,
    skill_name: str,
    repo_root,
    model: str,
    *,
    route,
    detect_fired=detect_skill_fired,
    first_skill=first_dispatched_skill,
    passes: int = 3,
    timeout: int = 20,
    attempts: int = 3,
    sleep=time.sleep,
    threshold: int | None = None,
    on_pass=None,
    effort: str = "low",
) -> int:
    """Route `query` up to `passes` times through real skill-routing; count fires.

    `threshold` is the fire count at which the outcome is locked (see
    `fire_threshold`); it defaults to a majority. The pass loop short-circuits once
    the result can't change — `fires >= threshold` (locked fired) or
    `fires + remaining < threshold` (locked not-fired) — so a clear-cut query
    doesn't spend all `passes`. Each pass retries up to `attempts` times on
    `RoutingError` (a failed routing run, e.g. a rate limit under heavy `-n`) with
    exponential backoff, so a transient failure isn't miscounted as a non-fire; a
    persistent failure propagates (a loud error beats a silent false negative).
    `route` (required) and `sleep` are injectable so the loop is testable without a VM.
    `on_pass(duration_ms, fired, fired_skill)`, if given, is called once per pass
    that runs — for timing/diagnostics (which pass fired, and which skill won the
    route).
    """
    if threshold is None:
        threshold = passes // 2 + 1
    fires = 0
    for i in range(passes):
        started = time.perf_counter()
        for attempt in range(1, attempts + 1):
            try:
                lines = route(
                    query, repo_root, model, timeout,
                    effort=effort, skill_name=skill_name,
                )
                break
            except RoutingError:
                if attempt == attempts:
                    raise
                sleep(min(2 ** attempt, 10))
        fired = detect_fired(lines, skill_name)
        routed = first_skill(lines)
        if fired:
            fires += 1
        if on_pass is not None:
            on_pass(int((time.perf_counter() - started) * 1000), fired, routed)
        remaining = passes - (i + 1)
        if fires >= threshold or fires + remaining < threshold:
            break
    return fires
