"""Bind prose assertions to deterministic checker specs when safe.

The binder is a fixed classifier with two interchangeable transports, chosen by the
run's `BinderConfig`: Gemini's native `generateContent` API, or OpenRouter's
chat-completions API carrying the same Gemini model under its vendor-prefixed slug. Both
return a `BinderReply`; the failure taxonomy is total for each — a rejected credential
is a `BinderAuthError` that stops the run, every other failure a `RuntimeError`.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import textwrap
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from benchspec.agents import OPENROUTER_PROVIDER
from benchspec.grading.binder_config import (
    GEMINI_BINDER_MODEL,
    GEMINI_PROVIDER,
    OPENROUTER_BINDER_MODEL,
    BinderConfig,
)
from benchspec.grading.judge import _balanced_objects
from benchspec.specs.schema import SchemaError, _validate_checker_obj

GEMINI_API_PATH = "generativelanguage.googleapis.com/v1beta"
OPENROUTER_API_PATH = "openrouter.ai/api/v1"
_API_PATHS = {GEMINI_PROVIDER: GEMINI_API_PATH, OPENROUTER_PROVIDER: OPENROUTER_API_PATH}
_GEMINI_URL = f"https://{GEMINI_API_PATH}/models/{{model}}:generateContent"
_OPENROUTER_URL = f"https://{OPENROUTER_API_PATH}/chat/completions"
_BINDER_TIMEOUT_SECONDS = 60.0
_GEMINI_TIMEOUT_SECONDS = _BINDER_TIMEOUT_SECONDS
_AUTH_HTTP_CODES = {401, 403}
# OpenRouter: 401 is a bad key and 402 an unfunded account. 403 is content moderation
# there, not auth, so it stays in the RuntimeError half of the taxonomy.
_OPENROUTER_AUTH_HTTP_CODES = {401, 402}
# The credential each binder provider reads from the host environment.
_CREDENTIAL_ENV = {GEMINI_PROVIDER: "GEMINI_API_KEY", OPENROUTER_PROVIDER: "OPENROUTER_API_KEY"}

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
_NAMES_REGEX_RE = re.compile(r"\b(?:regexp?|regular expression)\b", re.IGNORECASE)

logger = logging.getLogger(__name__)

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
    - not_file_exists  {{"checker":"not_file_exists","path":"<rel/path>"}}
    - glob_count   {{"checker":"glob_count","glob":"<pattern>","count":<int>}}
      (use EITHER "count" XOR "min", never both)
    - frontmatter_has {{"checker":"frontmatter_has","path":"<rel/path>","key":"<key>"}}
      (optional "value":"<expected>")
    - regex        {{"checker":"regex","path":"<rel/path>","pattern":"<regex>"}}
    - sha256_match
      {{"checker":"sha256_match","path":"<rel/path>","original":"<named-pre-run-file>"}}
      (or "sha256":"<64 hex>")
    - skill_invoked {{"checker":"skill_invoked","skill":"<skill-name>"}}
    - not_skill_invoked {{"checker":"not_skill_invoked","skill":"<skill-name>"}}
    </primitives>

    <rules>
    Reply with one JSON object and nothing else: a checker from <primitives>, or
    {{"punt": true, "reason": "<why>"}}.

    Bind only when the assertion is a single plain, mechanical fact: a path (file or
    directory) exists or is gone, N files match a glob, a file has a frontmatter key, a
    file has a line matching a regex, a file is byte-identical to a named pre-run file, or
    a bare "- Skill `X` invoked" / "not invoked" line.

    Punt everything else. In particular:
    - Anything that needs the content read for meaning — "reflects the facts", "is
      accurate", "the summary states X", "contains the right files".
    - Anything that bundles two facts — "exists and contains all six templates", a skill
      line that also says what was or wasn't written.
    - Any claim that something was NOT changed, removed, duplicated, or added — "still
      present", "left intact", "not duplicated", "no new entry was written to X". A surface
      check can't see the "…and nothing else happened" half. The one exception is an
      explicit "byte-identical" / sha256 claim, which binds to sha256_match.

    Copy paths exactly as written — full workdir-relative, `./` and `{{TODAY}}` included,
    never a basename or label. Patterns too, character for character, without the
    surrounding quotes — when the assertion hands you a regex, the `pattern` is that regex
    verbatim. When a file is byte-identical to its own pre-run content, `original` is the
    same path as `path`.
    </rules>

    <examples>
    Assertion: the file report.md exists
    {{"checker":"file_exists","path":"report.md"}}

    Assertion: the ./output/ directory was created
    {{"checker":"file_exists","path":"output"}}

    Assertion: exactly 3 files match notes/*.md
    {{"checker":"glob_count","glob":"notes/*.md","count":3}}

    Assertion: At least one file matches ./out/{{TODAY}}/*.json
    {{"checker":"glob_count","glob":"./out/{{TODAY}}/*.json","min":1}}

    Assertion: - Skill `my-skill` invoked
    {{"checker":"skill_invoked","skill":"my-skill"}}

    Assertion: - Skill `my-skill` not invoked
    {{"checker":"not_skill_invoked","skill":"my-skill"}}

    Assertion: The skill did NOT invoke the `to-spec` skill — no Skill or Task call that runs
    to-spec, and no spec file written. It announces handoff readiness only.
    {{"punt": true, "reason": "compound — bundles the activation fact with 'no spec file
      written' and an intent claim; not_skill_invoked checks only one bare activation line"}}

    Assertion: the ./tmp/scratch.md file no longer exists
    {{"checker":"not_file_exists","path":"tmp/scratch.md"}}

    Assertion: No new 'archive' entry was written to ./.meta/logs/{{TODAY}}.md
    {{"punt": true, "reason": "persistence-negation — the log path exists; 'no new entry
      was written' is a contents/absence-of-action claim, not a path-absence not_file_exists sees"}}

    Assertion: out/session.jsonl is byte-identical to .store/projects/proj/sess-0001.jsonl
    (the active session source)
    {{"checker":"sha256_match","path":"out/session.jsonl",
      "original":".store/projects/proj/sess-0001.jsonl"}}

    Assertion: the existing file 9. Archive/Sources/2099-01-01/notes.md is byte-identical
    to its pre-run content — the collision did not overwrite it
    {{"checker":"sha256_match","path":"9. Archive/Sources/2099-01-01/notes.md",
      "original":"9. Archive/Sources/2099-01-01/notes.md"}}

    Assertion: ./x matches the regex "(?i)foo\\s+'bar'"
    {{"checker":"regex","path":"./x","pattern":"(?i)foo\\\\s+'bar'"}}

    Assertion: the ./build/ directory now exists and contains all the compiled assets
    {{"punt": true, "reason": "compound — bare existence binds, but 'contains all the assets'
      is a separate, unverifiable contents claim"}}

    Assertion: the original note item.md is still present and was not duplicated
    {{"punt": true, "reason": "persistence/negation — blind to the not-duplicated clause"}}

    Assertion: the summary faithfully reflects the three key facts from the source
    {{"punt": true, "reason": "semantic — needs reading content for meaning"}}
    </examples>

    <assertion>{assertion}</assertion>
    """
)


class BinderAuthError(Exception):
    """Raised when the binder's API rejects the configured credential.

    Deliberately NOT a RuntimeError subclass — every RuntimeError catch site in the
    binder's failure taxonomy (execution.py's degrade-to-judge path, the corpus
    retry loop) must let this propagate instead of silently rerouting every
    assertion to paid judge grading behind a green run.
    """


@dataclass(frozen=True)
class BinderReply:
    """One binder model response, whichever transport produced it.

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
) -> BinderReply:
    """Call the Gemini binder model and return its reply.

    Args:
        prompt: The rendered binding prompt.
        timeout: Request timeout in seconds.
        model: The Gemini model name. Defaults to the fixed production constant;
            no environment variable is read here — only the corpus suite's own
            recording `call_model` wrapper (`evals/binder/conftest.py`) reads
            `BENCHSPEC_BINDER_MODEL` and passes it through this keyword.

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


def _parse_gemini_payload(payload: object, *, latency_ms: float) -> BinderReply:
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

    text = _candidate_text(payload)
    usage = payload.get("usageMetadata")
    usage = usage if isinstance(usage, dict) else {}

    return BinderReply(
        text=text,
        prompt_tokens=_usage_int(usage.get("promptTokenCount")),
        output_tokens=_usage_int(usage.get("candidatesTokenCount")),
        latency_ms=latency_ms,
    )


def _candidate_text(payload: dict) -> str:
    """Return the first candidate's joined text, raising RuntimeError on degenerate shapes."""
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

    text = "".join(_part_text(part) for part in parts)
    if not text:
        raise RuntimeError("Gemini API response had empty text")
    return text


def _part_text(part: object) -> str:
    """Return one content part's text ("" for non-text parts), raising on non-string text."""
    if not isinstance(part, dict):
        return ""
    value = part.get("text")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RuntimeError("Gemini API response part text must be a string")
    return value


def _usage_int(value: object) -> int:
    """Return non-boolean integer usage values, 0 otherwise."""
    if type(value) is int:
        return value
    return 0


def _call_openrouter(
    prompt: str,
    *,
    timeout: float = _BINDER_TIMEOUT_SECONDS,
    model: str = OPENROUTER_BINDER_MODEL,
) -> BinderReply:
    """Call the binder model through OpenRouter's chat-completions API and return its reply.

    The request pins `temperature: 0` and JSON mode, and sets
    `provider.require_parameters` so OpenRouter refuses a route that would silently drop
    either instead of serving it.

    Args:
        prompt: The rendered binding prompt.
        timeout: Request timeout in seconds.
        model: The vendor-prefixed OpenRouter model slug.

    Returns:
        The model's text plus token usage and measured latency.

    Raises:
        BinderAuthError: the key was rejected (HTTP 401) or the account is unfunded (402).
        RuntimeError: every other transport, HTTP, or degenerate-response failure —
            including a 200 whose choice carries an `error` or finished with `error`.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "provider": {"require_parameters": True},
    }).encode("utf-8")
    request = urllib.request.Request(
        _OPENROUTER_URL,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        if error.code in _OPENROUTER_AUTH_HTTP_CODES:
            raise BinderAuthError(
                f"OpenRouter rejected the credential (HTTP {error.code}): {error_body[:500]}"
            ) from error
        raise RuntimeError(f"OpenRouter HTTP {error.code}: {error_body[:500]}") from error
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        raise RuntimeError(f"OpenRouter transport failure: {error}") from error
    latency_ms = (time.perf_counter() - started) * 1000

    return _parse_openrouter_payload(payload, latency_ms=latency_ms)


def _parse_openrouter_payload(payload: object, *, latency_ms: float) -> BinderReply:
    """Validate a 200-OK OpenRouter chat-completions payload and extract text + usage.

    Raises RuntimeError for every degenerate shape: no choices, a choice carrying an
    `error` object or `finish_reason: "error"` (a provider failure OpenRouter relays
    inside a 200), or empty message content.
    """
    if not isinstance(payload, dict):
        raise RuntimeError("OpenRouter returned a non-object payload")

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("OpenRouter response had no choices")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise RuntimeError("OpenRouter choice was not an object")
    if choice.get("error") or choice.get("finish_reason") == "error":
        raise RuntimeError(f"OpenRouter relayed a provider error: {choice.get('error')}")

    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise RuntimeError("OpenRouter response message content must be a string")
    if not content:
        raise RuntimeError("OpenRouter response had empty text")

    usage = payload.get("usage")
    usage = usage if isinstance(usage, dict) else {}

    return BinderReply(
        text=content,
        prompt_tokens=_usage_int(usage.get("prompt_tokens")),
        output_tokens=_usage_int(usage.get("completion_tokens")),
        latency_ms=latency_ms,
    )


def _transport(provider: str) -> Callable[..., BinderReply]:
    """The request function for a binder provider, looked up at call time."""
    transports: dict[str, Callable[..., BinderReply]] = {
        GEMINI_PROVIDER: _call_gemini,
        OPENROUTER_PROVIDER: _call_openrouter,
    }
    return transports[provider]


def binder_credential_env(config: BinderConfig) -> str:
    """The host environment variable the configured binder provider authenticates with."""
    return _CREDENTIAL_ENV[config.provider]


def preflight_verify_binder_key(config: BinderConfig) -> None:
    """Raise RuntimeError if the configured provider's key is missing or empty.

    A key set to the empty string counts as missing — python-dotenv never overrides an
    already-set environment variable, so `GEMINI_API_KEY= make evals` can't be silently
    repopulated from `.env`; this makes that invocation fail fast, before any paid call,
    exactly as intended.

    Args:
        config: The run's resolved binder config, which names the provider.
    """
    env_name = binder_credential_env(config)
    if not os.environ.get(env_name):
        raise RuntimeError(
            f"{env_name} is required — the binder calls the {config.provider} API for every "
            "graded run. Set it in the environment or a repo-root .env."
        )


def binder_identity(config: BinderConfig | None = None) -> dict:
    """Return the configured binder's transport identity for artifact metadata.

    Run-level — describes the assertion binder, not the grader: the resolved provider,
    its model, and the API path that provider posts to. Never reads or includes any
    key material.

    Args:
        config: The run's resolved binder config; the built-in default when None.
    """
    config = config or BinderConfig()
    return {
        "provider": config.provider,
        "model": config.model,
        "api_path": _API_PATHS[config.provider],
    }


CallModel = Callable[..., BinderReply]


def call_model_for(config: BinderConfig) -> CallModel:
    """The transport `bind` calls under `config`: its provider's request shape, model pinned."""
    return functools.partial(_transport(config.provider), model=config.model)


def bind(
    assertion_text: str,
    *,
    config: BinderConfig | None = None,
    call_model: CallModel | None = None,
) -> dict | None:
    """Return a deterministic checker spec dict for `assertion_text`, or None to punt.

    The returned dict is the exact checker-spec shape `checkers.run_assertion` consumes.
    The transport comes from `config` (the built-in Gemini default when None) unless
    `call_model` is injected, which is how tests drive the binder with fake BinderReply
    objects (no network) and how the corpus suite meters every reply.

    Args:
        assertion_text: The assertion prose to bind.
        config: The run's resolved binder config; selects the provider and model.
        call_model: A transport override; when given, `config` is not consulted.
    """
    if spec := _bind_bare_exists(assertion_text):
        return spec
    if call_model is None:
        call_model = call_model_for(config or BinderConfig())
    prompt = _BINDING_PROMPT.format(assertion=assertion_text)
    reply = call_model(prompt, timeout=_BINDER_TIMEOUT_SECONDS)
    return _parse_binding(reply.text, assertion_text)


def binder_for(config: BinderConfig) -> Callable[[str], dict | None]:
    """`bind` with the run's config fixed, in the one-argument shape grading consumes."""
    return functools.partial(bind, config=config)


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


def _names_regex(assertion_text: str) -> bool:
    """Return whether the assertion hands the binder a literal regex."""
    return _NAMES_REGEX_RE.search(assertion_text) is not None


def _is_drifted_regex(spec: dict, assertion_text: str) -> bool:
    """Return whether a regex spec's pattern is not a verbatim copy of the assertion's regex.

    Only assertions that name a regex are checked: prose like "has a line beginning
    'aliases:'" legitimately binds to a translated pattern such as `^aliases:`. The
    match is a substring test because the assertion wraps the pattern in quotes.

    Args:
        spec: A validated checker spec.
        assertion_text: The assertion prose the spec was bound from.

    Returns:
        True when the spec is a regex checker, the assertion names a regex, and the
        pattern does not appear verbatim in the assertion.
    """
    if spec["checker"] != "regex" or not _names_regex(assertion_text):
        return False
    return spec["pattern"] not in assertion_text


def _parse_binding(text: str, assertion_text: str) -> dict | None:
    """Parse a binder reply's text into a checker spec or punt.

    Args:
        text: The binder model's reply text, expected to carry one JSON object.
        assertion_text: The assertion prose the reply was bound from.

    Returns:
        The validated checker spec, or None to punt — on an explicit punt, unparseable
        or schema-invalid output, or a regex pattern that drifted from the one the
        assertion spells out.
    """
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
            # The author wrote the regex, so anything but a verbatim copy is a mangled
            # draw: punting costs nothing, a false negative fails a correct file.
            if _is_drifted_regex(spec, assertion_text):
                logger.info(
                    "binder punted regex for %r: pattern %s is not verbatim in the assertion",
                    assertion_text,
                    spec["pattern"],
                )
                return None
            return spec
    return None  # no object / unparseable → punt
