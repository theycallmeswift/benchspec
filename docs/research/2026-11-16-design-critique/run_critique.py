"""Run a rubric critique across several OpenRouter models in parallel.

Usage: python3 -I run_critique.py <rubric.md> <package_dir> <out_dir> [--runs N] [--cap USD]
[--only model,model]

The package dir holds a manifest.json ([{file, section|frame, theme, description}]),
the PNGs it names, and optionally text files (*.md) sent verbatim as context.
Each model gets: rubric + text context + every image. Raw replies, parsed JSON
and usage/cost land in out_dir as <model-slug>.run<N>.json. Needs OPENROUTER_API_KEY.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

MODELS = [
    "meta/muse-spark-1.3",
    "moonshotai/kimi-k3",
    "openai/gpt-6-astra",
    "anthropic/claude-opus-5.5",
    "google/gemini-4-argon",
    "x-ai/grok-4.7",
]
URL = "https://openrouter.ai/api/v1/chat/completions"
SYSTEM = (
    "You are a senior product designer reviewing a developer tool. Follow the rubric "
    "exactly and reply with the JSON object it specifies and nothing else."
)
DEFAULT_RUNS = 2
DEFAULT_CAP_USD = 10.0
REQUEST_TIMEOUT_SECONDS = 600
RETRY_DELAY_SECONDS = 5
ERROR_DETAIL_CHARS = 500


def build_content(rubric: str, package_dir: pathlib.Path) -> list[dict]:
    """Build the user message: the rubric, every text file, then each labeled screenshot.

    Args:
        rubric: The rubric text, sent first.
        package_dir: Directory with manifest.json, the PNGs it names, and optional *.md files.

    Returns:
        OpenRouter content parts, text and base64 PNG images in manifest order.
    """
    manifest = json.loads((package_dir / "manifest.json").read_text())
    parts: list[dict] = [{"type": "text", "text": rubric}]

    for text_file in sorted(package_dir.glob("*.md")):
        attached = f"\n\n# Attached file: {text_file.name}\n\n{text_file.read_text()}"
        parts.append({"type": "text", "text": attached})

    screenshots_intro = "\n\n# Screenshots\n\nEach image below is labeled by the line before it."
    parts.append({"type": "text", "text": screenshots_intro})

    for item in manifest:
        label = item.get("frame") or item.get("section") or item["file"]
        caption = f"\n{label} ({item.get('theme', 'dark')}): {item.get('description', '')}"
        parts.append({"type": "text", "text": caption})
        image_data = base64.b64encode((package_dir / item["file"]).read_bytes()).decode()
        image_url = f"data:image/png;base64,{image_data}"
        parts.append({"type": "image_url", "image_url": {"url": image_url}})

    return parts


def parse_json(text: str) -> dict | None:
    """Return the outermost JSON object in a model reply, or None if there is none."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None

    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def output_path(out_dir: pathlib.Path, model: str, run: int) -> pathlib.Path:
    """Return the record file for one model run, slashes in the slug replaced."""
    return out_dir / f"{model.replace('/', '__')}.run{run}.json"


def build_request(model: str, content: list[dict]) -> urllib.request.Request:
    """Build the OpenRouter chat-completions request for one model."""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": content},
        ],
        "max_tokens": 32000,
        "reasoning": {"effort": "medium"},
        "temperature": 0.4,
        "usage": {"include": True},
    }

    return urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
            "X-Title": "design-critique",
        },
    )


def describe_error(error: Exception) -> str:
    """Return an HTTP error's response body (truncated), or the exception text."""
    if isinstance(error, urllib.error.HTTPError):
        return error.read().decode()[:ERROR_DETAIL_CHARS]

    return str(error)


def call(model: str, content: list[dict], run: int, out_dir: pathlib.Path) -> dict:
    """Send one critique request, retrying once, and write its record to out_dir.

    Args:
        model: OpenRouter model slug.
        content: The user message parts from build_content.
        run: Run number, used in the record and its file name.
        out_dir: Directory the record is written to.

    Returns:
        The record: model, run, seconds, and either error or cost, tokens, parsed and raw.
    """
    request = build_request(model, content)
    started_at = time.time()
    record: dict = {"model": model, "run": run}
    payload = None

    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read())
            break
        except Exception as error:
            record["error"] = describe_error(error)
            if attempt == 1:
                record["seconds"] = round(time.time() - started_at, 1)
                output_path(out_dir, model, run).write_text(json.dumps(record, indent=2))
                return record
            time.sleep(RETRY_DELAY_SECONDS)

    assert payload is not None
    record.pop("error", None)
    text = payload["choices"][0]["message"].get("content") or ""
    usage = payload.get("usage", {})
    record.update(
        seconds=round(time.time() - started_at, 1),
        cost=usage.get("cost"),
        tokens={"in": usage.get("prompt_tokens"), "out": usage.get("completion_tokens")},
        parsed=parse_json(text),
        raw=text,
    )
    output_path(out_dir, model, run).write_text(json.dumps(record, indent=2))

    return record


def option_value(args: list[str], flag: str) -> str | None:
    """Return the argument after flag, or None when flag is absent."""
    if flag not in args:
        return None

    return args[args.index(flag) + 1]


def describe_status(record: dict) -> str:
    """Summarize one record for the progress line: error, issue count, or unparsed."""
    if "error" in record:
        return "ERROR " + record["error"][:120]

    parsed = record.get("parsed")
    if not parsed:
        return "UNPARSED"

    findings = parsed.get("issues") or parsed.get("observations") or []
    return f"ok {len(findings)} issues"


def main() -> None:
    """Run every selected model --runs times in parallel and report spend against --cap."""
    args = sys.argv[1:]
    runs_option = option_value(args, "--runs")
    cap_option = option_value(args, "--cap")
    only_option = option_value(args, "--only")
    runs = int(runs_option) if runs_option else DEFAULT_RUNS
    cap = float(cap_option) if cap_option else DEFAULT_CAP_USD
    models = only_option.split(",") if only_option else MODELS

    rubric = pathlib.Path(args[0]).read_text()
    package_dir = pathlib.Path(args[1])
    out_dir = pathlib.Path(args[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    content = build_content(rubric, package_dir)

    spent = 0.0
    jobs = [(model, run) for run in range(1, runs + 1) for model in models]
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(call, model, content, run, out_dir) for model, run in jobs]
        for future in as_completed(futures):
            record = future.result()
            spent += record.get("cost") or 0
            print(
                f"{record['model']:28} run{record['run']} {record.get('seconds', '?')}s "
                f"${record.get('cost') or 0:.3f} {describe_status(record)}",
                flush=True,
            )

    print(f"total ${spent:.2f} (cap ${cap:.2f})")
    if spent > cap:
        print("WARNING: spend exceeded cap")


if __name__ == "__main__":
    main()
