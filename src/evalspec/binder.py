"""Bind prose assertions to deterministic checker specs when safe."""

from __future__ import annotations

import json
import os
import re
import textwrap
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from evalspec.judge import _balanced_objects
from evalspec.schema import SchemaError, _validate_checker_obj

GEMINI_BINDER_MODEL = "gemini-3.1-flash-lite"

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_GEMINI_TIMEOUT_SECONDS = 60.0
_AUTH_HTTP_CODES = {401, 403}

_PATHLIKE_RE = re.compile(r"^(?:\.?/|/|\.[^/\s]+/)|/")
_BARE_EXISTS_RE = re.compile(
    r"^(?:the\s+)?(?:(?:file|folder|directory)\s+)?(?P<path>.+?)"
    r"(?:\s+(?:file|folder|directory))?\s+"
    r"(?:exists|exist|was created|were created|now exists)\.?\s*$",
    re.IGNORECASE,
)
_COMPOUND_AFTER_EXISTS_RE = re.compile(
    r"\b(?:exists?|created)\b\s*(?:,|;|\band\b|\bwith\b|\bcontaining\b|\bcontains?\b)",
    re.IGNORECASE,
)

# Prompt wording is part of binder accuracy. The semantic and conjunction rules below keep
# surface checks from accepting wrong output.
# Keep `{assertion}` as the only format field.
_BINDING_PROMPT = textwrap.dedent(
    """\
    <role> You convert exactly ONE eval assertion into a deterministic checker
    spec, OR you decline. You are a conservative classifier, not a grader: you never
    judge whether the assertion is true, only whether it can be checked mechanically by
    one of the primitives below WITHOUT reading content for meaning. Declining ("punt")
    is always free — the assertion will be graded by an LLM judge anyway. The ONLY
    unacceptable error is emitting a checker that could pass on WRONG output. When in
    any doubt, punt. </role>

    <primitives>
    Each spec is a JSON object with a `checker` field plus that checker's args:
    - file_exists  {{"checker":"file_exists","path":"<rel/path>"}}
      (optional "should_exist": false to assert absence)
    - glob_count   {{"checker":"glob_count","glob":"<pattern>","count":<int>}}
      (use EITHER "count" XOR "min", never both)
    - frontmatter_has {{"checker":"frontmatter_has","path":"<rel/path>","key":"<key>"}}
      (optional "value":"<expected>")
    - regex        {{"checker":"regex","path":"<rel/path>","pattern":"<regex>"}}
    - sha256_match
      {{"checker":"sha256_match","path":"<rel/path>","original":"<named-pre-run-file>"}}
      (or "sha256":"<64 hex>")
    - skill_invoked {{"checker":"skill_invoked","skill":"<skill-name>"}}
    Canonical skill-activation assertion line:
    `- Skill \\`X\\` invoked` → {{"checker":"skill_invoked","skill":"X"}}.
    </primitives>

    <rules>
    Output ONLY a single JSON object and nothing else — either ONE checker object from
    <primitives>, OR a punt: {{"punt": true, "reason": "<why>"}}.

    A9 HARD RULE — presence / persistence / negation assertions ALWAYS punt. If the assertion
    asserts that something is "still present", "still contains", "not replaced", "not
    duplicated", "in place, not duplicated", "preserved", "did not discard", "left intact",
    "remains", "unchanged", or any other claim that something WAS NOT altered/removed/added,
    you MUST punt. A surface check sees the present substring but is blind to the invisible
    "…and was not replaced/duplicated" clause, so binding it would be a false-positive. The ONLY
    carve-out is a BYTE-IDENTITY claim against pre-run content: "byte-identical to the pre-run X"
    (a named distinct file) OR "byte-identical to its pre-run content" (the SAME file, unchanged)
    binds to sha256_match — the one allowed persistence check, because the hash fully captures
    "not altered" with no blind clause. A trailing explanatory gloss on such a claim ("— did not
    overwrite it", "— left it untouched", "— did not re-stamp or reformat it") does NOT turn it
    into a punt; the byte-identity IS the check. This carve-out requires the words "byte-identical"
    (or "byte-for-byte" / an explicit sha256): a persistence claim WITHOUT a byte-identity phrase
    ("still present", "not duplicated", "left intact") still punts under the rule above.

    PATHS — when you bind any path-bearing checker, copy `path`, `glob`, `original`, and target
    filenames inside regex assertions as the FULL workdir-relative path EXACTLY as written in the
    assertion. Never shorten to a basename, substitute a descriptive label, drop directory
    components, or drop meaningful `.` path components such as the dot in `./.meta/...`. A leading
    `./` may remain; the checker resolver treats it only as a workdir anchor. For sha256_match,
    the pre-run SHA map is keyed by the full path, so a basename or label will not resolve. For a
    SELF-COMPARISON ("byte-identical to its pre-run content" — no separate file named), set
    `original` to the SAME full path as `path`.

    Punt on any assertion whose truth needs reading content for meaning, correctness, or
    faithfulness — e.g. "reflects the facts", "names the three primitives", "the summary
    states X", "surfaces the contradiction", "reads well", "is accurate". No primitive can
    verify meaning.

    Punt when an assertion bundles two facts with "and" (e.g. "the file exists and is
    accurate") — one object checks one fact.

    DIRECTORIES — file_exists tests whether a path EXISTS as a file or a directory, so a BARE
    existence claim about a folder ("the directory X was created", "X no longer exists") binds
    to file_exists with that path. A claim that ALSO says what the directory contains — "exists
    and contains all six templates", "holds the seven PARA folders" — is compound: punt. A bare
    file count with an explicit glob ("exactly 3 files match notes/*.md") still binds glob_count,
    but "contains all the right files" is NOT a count — glob_count can't tell the right files
    from the wrong ones, so binding it would be a false-positive.

    Prefer punting. A false negative costs nothing (the judge grades it). A false positive — a
    deterministic check passing on wrong output — is the exact failure this prompt prevents.
    </rules>

    <examples>
    Assertion: the file report.md exists
    {{"checker":"file_exists","path":"report.md"}}

    Assertion: the ./output/ directory was created
    {{"checker":"file_exists","path":"output"}}

    Assertion: exactly 3 files match notes/*.md
    {{"checker":"glob_count","glob":"notes/*.md","count":3}}

    Assertion: - Skill `my-skill` invoked
    {{"checker":"skill_invoked","skill":"my-skill"}}

    Assertion: out/session.jsonl is byte-identical to .store/projects/proj/sess-0001.jsonl
    (the active session source)
    {{"checker":"sha256_match","path":"out/session.jsonl",
      "original":".store/projects/proj/sess-0001.jsonl"}}

    Assertion: the existing file 9. Archive/Sources/2099-01-01/notes.md is byte-identical
    to its pre-run content — the collision did not overwrite it
    {{"checker":"sha256_match","path":"9. Archive/Sources/2099-01-01/notes.md",
      "original":"9. Archive/Sources/2099-01-01/notes.md"}}

    Assertion: the ./build/ directory now exists and contains all the compiled assets
    {{"punt": true, "reason": "compound — bare existence binds, but 'contains all the assets'
      is a separate, unverifiable contents claim"}}

    Assertion: the original note item.md is still present and was not duplicated
    {{"punt": true, "reason": "A9 persistence/negation — blind to the not-duplicated clause"}}

    Assertion: the summary faithfully reflects the three key facts from the source
    {{"punt": true, "reason": "semantic — needs reading content for meaning"}}
    </examples>

    <assertion>{assertion}</assertion>
    """
)


class BinderAuthError(Exception):
    """Raised when the Gemini API rejects the configured credential.

    Deliberately NOT a RuntimeError subclass — every RuntimeError catch site in the
    binder's failure taxonomy (execution.py's degrade-to-judge path, the corpus
    retry loop) must let this propagate instead of silently rerouting every
    assertion to paid judge grading behind a green run.
    """


@dataclass(frozen=True)
class GeminiReply:
    """One Gemini generateContent response.

    Trimmed to what the binder and the binder corpus suite need.
    """

    text: str
    prompt_tokens: int
    output_tokens: int
    latency_ms: float


def _call_gemini(
    prompt: str,
    *,
    timeout: float = _GEMINI_TIMEOUT_SECONDS,
    model: str = GEMINI_BINDER_MODEL,
) -> GeminiReply:
    """Call the Gemini binder model and return its reply.

    Args:
        prompt: The rendered binding prompt.
        timeout: Request timeout in seconds.
        model: The Gemini model name. Defaults to the fixed production constant;
            no environment variable is read here — only the corpus suite's own
            recording `call_model` wrapper (`evals/binder/conftest.py`) reads
            `EVALSPEC_BINDER_MODEL` and passes it through this keyword.

    Returns:
        The model's text plus token usage and measured latency.

    Raises:
        BinderAuthError: the API key was rejected (HTTP 401/403, or a 400 body
            naming API_KEY_INVALID).
        RuntimeError: every other transport, HTTP, or degenerate-response failure.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }).encode("utf-8")
    request = urllib.request.Request(
        _GEMINI_URL.format(model=model),
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        is_invalid_key = error.code == 400 and "API_KEY_INVALID" in error_body
        if error.code in _AUTH_HTTP_CODES or is_invalid_key:
            raise BinderAuthError(
                f"Gemini API rejected the credential (HTTP {error.code}): {error_body[:500]}"
            ) from error
        raise RuntimeError(f"Gemini API HTTP {error.code}: {error_body[:500]}") from error
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        # ValueError catches json.JSONDecodeError (its subclass) and the rare
        # UnicodeDecodeError-as-ValueError case — the taxonomy is total: nothing
        # raised inside this try may escape as a raw, unnormalized exception.
        raise RuntimeError(f"Gemini API transport failure: {error}") from error
    latency_ms = (time.perf_counter() - started) * 1000

    return _parse_gemini_payload(payload, latency_ms=latency_ms)


def _parse_gemini_payload(payload: object, *, latency_ms: float) -> GeminiReply:
    """Validate a 200-OK Gemini payload and extract text + usage.

    Raises RuntimeError for every degenerate shape (no candidates, empty parts,
    finishReason SAFETY/MAX_TOKENS, promptFeedback block) — a 200 status code alone
    does not mean the model produced usable text.
    """
    if not isinstance(payload, dict):
        raise RuntimeError("Gemini API returned a non-object payload")
    feedback = payload.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        raise RuntimeError(f"Gemini API blocked the prompt: {feedback['blockReason']}")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError("Gemini API response had no candidates")
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise RuntimeError("Gemini API candidate was not an object")
    finish_reason = candidate.get("finishReason")
    if finish_reason in ("SAFETY", "MAX_TOKENS"):
        raise RuntimeError(f"Gemini API candidate finished with {finish_reason}")
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list) or not parts:
        raise RuntimeError("Gemini API response had no content parts")
    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        part_text = part.get("text")
        if part_text is None:
            continue
        if not isinstance(part_text, str):
            raise RuntimeError("Gemini API response part text must be a string")
        texts.append(part_text)
    text = "".join(texts)
    if not text:
        raise RuntimeError("Gemini API response had empty text")

    usage = payload.get("usageMetadata")
    usage = usage if isinstance(usage, dict) else {}
    return GeminiReply(
        text=text,
        prompt_tokens=_usage_int(usage.get("promptTokenCount")),
        output_tokens=_usage_int(usage.get("candidatesTokenCount")),
        latency_ms=latency_ms,
    )


def _usage_int(value: object) -> int:
    """Return non-boolean integer usage values, 0 otherwise."""
    if type(value) is int:
        return value
    return 0


def preflight_gemini_key() -> None:
    """Raise RuntimeError if GEMINI_API_KEY is missing or empty.

    A GEMINI_API_KEY set to the empty string counts as missing — python-dotenv
    never overrides an already-set environment variable, so `GEMINI_API_KEY=
    make evals:binder` can't be silently repopulated from `.env`; this makes that
    invocation fail fast, before any paid call, exactly as intended.
    """
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError(
            "GEMINI_API_KEY is required — the binder calls the Gemini API for every "
            "graded run. Set it in the environment or a repo-root .env."
        )


def bind(
    assertion_text: str,
    *,
    call_model: object = _call_gemini,
) -> dict | None:
    """Return a deterministic checker spec dict for `assertion_text`, or None to punt.

    The returned dict is the exact checker-spec shape `checkers.run_assertion` consumes.
    `call_model` is injected so tests drive the binder with fake GeminiReply objects
    (no network); production and the corpus suite both default to `_call_gemini`.
    """
    if spec := _bind_bare_exists(assertion_text):
        return spec
    prompt = _BINDING_PROMPT.format(assertion=assertion_text)
    reply = call_model(prompt, timeout=60)
    return _parse_binding(reply.text)


def _clean_bare_exists_path(raw: str) -> str:
    """Normalize a bare existence assertion path for binding."""
    path = raw.strip()
    if len(path) >= 2 and path[0] == path[-1] and path[0] in {"'", '"', "`"}:
        path = path[1:-1].strip()
    return path


def _bind_bare_exists(assertion_text: str) -> dict | None:
    """Bind a simple file-existence assertion to a checker spec."""
    text = assertion_text.strip()
    if _COMPOUND_AFTER_EXISTS_RE.search(text):
        return None
    match = _BARE_EXISTS_RE.fullmatch(text)
    if not match:
        return None
    path = _clean_bare_exists_path(match.group("path"))
    if not path or not _PATHLIKE_RE.search(path) or " " in path:
        return None
    spec = {"type": "deterministic", "checker": "file_exists", "path": path}
    try:
        _validate_checker_obj(spec, "binder")
    except SchemaError:
        return None
    return spec


def _parse_binding(text: str) -> dict | None:
    """Parse a Gemini reply's text into a checker spec or punt."""
    for candidate in _balanced_objects(text):
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("punt"):
            return None
        if "checker" in obj:
            spec = {**obj, "type": "deterministic"}
            try:
                _validate_checker_obj(spec, "binder")
            except SchemaError:
                return None  # malformed/hallucinated args → punt, never a false-positive
            return spec
    return None  # no object / unparseable → punt
