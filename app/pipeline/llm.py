"""Local LLM client (Ollama, on-box). Used ONLY to read prose fields the regex
layer cannot (name, titles, skills, education) — never to produce numbers or
identity that we can extract deterministically, and never authoritative on its
own. Thinking is disabled and output is schema-constrained JSON for reliability
(tested with Ollama 0.30.10 and qwen3.6:35b)."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx


class LLMError(RuntimeError):
    pass


def _loads_lenient(content: str) -> dict[str, Any]:
    content = (content or "").strip()
    if not content:
        return {}
    # strip ```json ... ``` fences the model sometimes adds
    if content.startswith("```"):
        content = re.sub(r"^```[A-Za-z]*\s*", "", content)
        content = re.sub(r"\s*```$", "", content).strip()
    try:
        return json.loads(content)
    except ValueError:  # json.JSONDecodeError
        pass
    # grab the outermost object
    m = re.search(r"\{.*\}", content, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            return {}
    return {}


def chat_json(
    settings,
    system: str,
    user: str,
    schema: dict[str, Any] | None = None,
    timeout: int = 180,
    num_ctx: int = 8192,
    task: str | None = None,
) -> dict[str, Any]:
    """Single-shot, non-streaming, no-thinking, JSON-constrained chat call.

    In ``CV_LLM_MODE=mock`` (the public default) this returns a deterministic,
    rule-based result parsed from the input instead of calling any model, so the
    whole pipeline runs with zero dependencies. ``task`` selects the mock parser.
    """
    if getattr(settings, "llm_mode", "ollama") == "mock":
        from .. import mock

        return mock.chat_json(task, system, user, schema)
    url = settings.ollama_url.rstrip("/") + "/api/chat"
    body: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "think": False,
        "format": schema if schema else "json",
        "options": {"temperature": 0, "num_ctx": num_ctx},
    }
    try:
        resp = httpx.post(url, json=body, timeout=timeout)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise LLMError(f"ollama call failed: {e}") from e
    data = resp.json()
    content = (data.get("message") or {}).get("content", "")
    return _loads_lenient(content)


def pick(d: Any, keys: list[str], default: Any = "") -> Any:
    """First non-empty value of ``d`` under any of ``keys`` (case-insensitive).

    Models often ignore the schema's exact keys and answer with a synonym
    (experience / job_title / company ...), so callers list the synonyms they
    accept."""
    if not isinstance(d, dict):
        return default
    low = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        v = low.get(k.lower())
        if v not in (None, ""):
            return v
    return default


def pick_list(d: Any, keys: list[str]) -> list[Any]:
    v = pick(d, keys, default=None)
    return v if isinstance(v, list) else []
