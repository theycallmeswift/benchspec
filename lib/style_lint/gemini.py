"""Gemini transport for advisory source linting."""

from __future__ import annotations

import json
import urllib.request

GEMINI_TIMEOUT_SECONDS = 30.0


def call_gemini(
    *,
    prompt: str,
    api_key: str,
    model: str,
    timeout: float = GEMINI_TIMEOUT_SECONDS,
) -> str:
    """Call Gemini REST API and return the model text response.

    Args:
        prompt: Prompt text to send.
        api_key: Gemini API key.
        model: Gemini model name.
        timeout: Request timeout in seconds.

    Returns:
        Concatenated text parts from the first candidate.

    Raises:
        ValueError: If Gemini returns an unexpected payload shape.
        urllib.error.URLError: If transport fails.
        TimeoutError: If the request times out.
    """
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    request_body = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0,
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=request_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not isinstance(payload, dict):
        raise ValueError("Gemini response must be a JSON object")

    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("Gemini response did not include candidates")

    first_candidate = candidates[0]
    if not isinstance(first_candidate, dict):
        raise ValueError("Gemini response candidate must be an object")

    content = first_candidate.get("content")
    if not isinstance(content, dict):
        raise ValueError("Gemini response candidate did not include content")

    parts = content.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("Gemini response content did not include parts")

    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("Gemini response part must be an object")
        text = part.get("text")
        if text is None:
            continue
        if not isinstance(text, str):
            raise ValueError("Gemini response part text must be a string")
        texts.append(text)

    if not texts:
        raise ValueError("Gemini response did not include text content")

    return "".join(texts)
