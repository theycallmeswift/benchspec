"""Document the behavior."""

from __future__ import annotations

import json
import re
import textwrap

from evalspec.agents.judge_cli import run_host_judge
from evalspec.judge import _balanced_objects
from evalspec.schema import SchemaError, _validate_checker_obj

BINDER_MODEL = "haiku"

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

# LOAD-BEARING and TUNABLE: this wording is the binder's whole accuracy budget. The
# A9 / semantic / conjunction rules below are what stop a surface check from
# producing a false-positive on wrong output — do not trim them. Keep `{assertion}` as
# the only format field.
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


def bind(
    assertion_text: str,
    *,
    model: str = BINDER_MODEL,
    call_host: object = run_host_judge,
) -> dict | None:
    """Return a deterministic checker spec dict for `assertion_text`, or None to punt.

    The returned dict is the exact checker-spec shape `checkers.run_assertion` consumes.
    `call_host` is injected so tests drive the binder with recorded envelopes (no
    network).
    """
    if spec := _bind_bare_exists(assertion_text):
        return spec
    prompt = _BINDING_PROMPT.format(assertion=assertion_text)
    # Shared host-claude transport (run_host_judge — "judge" is a slight misnomer here; it's
    # the generic host `claude -p` call). An infra RuntimeError propagates (fail loud).
    raw = call_host(prompt, model=model, timeout=60)
    return _parse_binding(raw)


def _clean_bare_exists_path(raw: str) -> str:
    """Handle _clean_bare_exists_path."""
    path = raw.strip()
    if len(path) >= 2 and path[0] == path[-1] and path[0] in {"'", '"', "`"}:
        path = path[1:-1].strip()
    return path


def _bind_bare_exists(assertion_text: str) -> dict | None:
    """Handle _bind_bare_exists."""
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


def _parse_binding(raw: str) -> dict | None:
    """Document the behavior."""
    if not isinstance(raw, str):
        return None  # non-string host output (e.g. None on an abnormal call) → punt
    try:
        outer = json.loads(raw)
        inner = outer.get("result", "") if isinstance(outer, dict) else raw
    except json.JSONDecodeError:
        inner = raw
    if not isinstance(inner, str):
        return None  # unexpected non-string `result` → punt, preserving the never-raises contract
    for candidate in _balanced_objects(inner):
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
    return None  # no envelope / no object / unparseable → punt
