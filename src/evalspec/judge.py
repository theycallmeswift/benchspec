"""LLM-judge grading: build the facts-fed judge prompt, run it via the agent's judge.

call, parse it.

The judge reasons only from supplied evidence (assertion list, resulting file tree, file
contents, runner-computed SHA-256s, agent final message) — not from recall. One retry on
malformed JSON, then all assertions are marked errored. Spawning the judge subprocess is
`CodingAgent.judge`'s job; this module is agent-agnostic — only the prompt build and the
parse live here.
"""

from __future__ import annotations

import json
import subprocess
import textwrap


def build_judge_prompt(
    assertions: list[str],
    tree: str,
    file_contents: dict[str, str],
    shas: dict[str, str],
    final_message: str,
    original_shas: dict[str, str] | None = None,
    process_facts: str = "",
) -> str:
    """Build the prompt sent to the judge model."""
    numbered = "\n".join(f"{i}. {a}" for i, a in enumerate(assertions, 1))
    files_block = "\n\n".join(
        f"### {name} (sha256={shas.get(name, 'n/a')})\n{content}"
        for name, content in file_contents.items()
    )
    orig_block = ""
    if original_shas:
        orig_lines = "\n".join(
            f"  {sha}  {relpath}" for relpath, sha in sorted(original_shas.items())
        )
        orig_block = f"\nORIGINAL FILES (before the task ran), SHA-256:\n{orig_lines}\n"
    process_block = ""
    if process_facts:
        process_block = (
            "\nPROCESS / TOOL ACTIVITY (tools and sub-skills the agent invoked during the "
            "run; a listed Skill(name) dispatch is evidence the agent used that sub-skill, "
            "even if the final message does not mention it):\n"
            f"{process_facts}\n"
        )
    # dedent the template (uniform indentation) before .format substitutes the
    # multi-line blocks in — an f-string here would force the blocks to column 0.
    template = textwrap.dedent(
        """\
        Grade an agent task against assertions. Judge ONLY from the evidence below
        — do not assume work that is not shown.

        ASSERTIONS:
        {numbered}

        RESULTING FILE TREE:
        {tree}
        {orig_block}
        RELEVANT FILE CONTENTS (with precomputed SHA-256):
        {files_block}
        {process_block}
        AGENT'S FINAL MESSAGE:
        {final_message}

        For each assertion, decide pass/fail from the evidence and quote the specific evidence.
        Output ONLY a JSON object, no prose:
        {{"assertions": [
          {{"text": "<assertion verbatim>", "passed": true, "evidence": "<quote>"}}
        ]}}
        """
    )
    return template.format(
        numbered=numbered,
        tree=tree,
        orig_block=orig_block,
        files_block=files_block,
        process_block=process_block,
        final_message=final_message,
    )


def _balanced_objects(text: str) -> object:
    """Yield each top-level `{...}` substring, respecting strings/escapes.

    `find("{")`/`rfind("}")` breaks when the judge's prose contains stray braces (e.g.
    it echoes a `{...}` placeholder before the real object): the slice then spans the
    wrong range. Scanning for balanced objects lets the caller try each candidate.
    """
    depth = 0
    start = None
    in_str = False
    esc = False
    for index, char in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif char == "\\":
                esc = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                yield text[start : index + 1]


def _coerce_passed(value: object) -> bool:
    """Normalize judge pass values to booleans."""
    # The judge should emit a JSON bool, but LLMs sometimes quote it ("false").
    # bool("false") is True, so handle strings explicitly rather than coercing.
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "pass", "passed"}
    return bool(value)


def parse_judge_json(raw: str, eval_id: str, config: str) -> dict:
    """Parse the judge model JSON response into grading data."""
    # The judge often wraps its JSON in a ```json fence or adds prose. Try each
    # balanced {...} candidate and take the first that parses and carries assertions.
    data = None
    for candidate in _balanced_objects(raw):
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "assertions" in obj:
            data = obj
            break
    if data is None:
        raise ValueError("no JSON object with assertions found in judge output")
    return {
        "eval_id": eval_id,
        "arm": config,
        "assertions": [
            {
                "text": assertion["text"],
                "passed": _coerce_passed(assertion["passed"]),
                "evidence": assertion.get("evidence", ""),
            }
            for assertion in data["assertions"]
        ],
    }


def grade_run(
    assertions: object,
    tree: object,
    file_contents: object,
    shas: object,
    final_message: object,
    eval_id: object,
    config: object,
    *,
    agent: object,
    model: object = "sonnet",
    timeout: object = 300,
    original_shas: object = None,
    process_facts: object = "",
) -> dict:
    """Grade one completed agent run against assertions."""
    prompt = build_judge_prompt(
        assertions,
        tree,
        file_contents,
        shas,
        final_message,
        original_shas,
        process_facts=process_facts,
    )
    for attempt in (1, 2):
        try:
            raw = agent.judge(prompt, model=model, timeout=timeout)
            outer = json.loads(raw)
            graded = parse_judge_json(outer.get("result", ""), eval_id, config)
            if len(graded["assertions"]) != len(assertions):
                # A judge that drops/merges assertions would silently leave some
                # ungraded — treat the misaligned response as unparseable.
                raise ValueError("judge returned a different assertion count")
            return graded
        except (
            # An infra-level judge failure (missing host CLI, nonzero exit, auth /
            # rate-limit) reaches us as RuntimeError from agent.judge — deliberately
            # NOT caught here, so it propagates to run_eval_arm and marks the arm
            # errored rather than being laundered into fake JUDGE ERROR assertions.
            # These remaining cases are genuine "judge ran but the output is
            # unusable" shapes, which do warrant the one-retry-then-error mask.
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
            KeyError,
            ValueError,
        ):
            if attempt == 1:
                continue
            return {
                "eval_id": eval_id,
                "arm": config,
                "assertions": [
                    {
                        "text": a,
                        "passed": False,
                        "evidence": "JUDGE ERROR: unparseable output",
                    }
                    for a in assertions
                ],
            }
    raise AssertionError("unreachable: loop exits via return in both branches")
