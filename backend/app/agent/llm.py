"""Thin OpenAI wrapper for the agent nodes.

JSON-mode helpers return parsed dicts; a model that returns malformed JSON
falls back to a caller-supplied default rather than raising into the graph,
because a routing hiccup should degrade the answer, not fail the request.
"""

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

_client: AsyncOpenAI | None = None


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=60.0, max_retries=2)
    return _client


def merge_usage(current: dict[str, int] | None, response: Any) -> dict[str, int]:
    totals = dict(current or {})
    usage = getattr(response, "usage", None)
    if usage is None:
        return totals
    totals["prompt_tokens"] = totals.get("prompt_tokens", 0) + (usage.prompt_tokens or 0)
    totals["completion_tokens"] = (
        totals.get("completion_tokens", 0) + (usage.completion_tokens or 0)
    )
    totals["total_tokens"] = totals.get("total_tokens", 0) + (usage.total_tokens or 0)
    totals["calls"] = totals.get("calls", 0) + 1
    return totals


async def complete_text(
    system: str,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.1,
    max_tokens: int = 1200,
) -> tuple[str, Any]:
    response = await client().chat.completions.create(
        model=settings.chat_model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, *messages],  # type: ignore[arg-type]
    )
    return (response.choices[0].message.content or "").strip(), response


async def complete_json(
    system: str,
    messages: list[dict[str, str]],
    *,
    default: dict[str, Any],
    temperature: float = 0.0,
    max_tokens: int = 500,
) -> tuple[dict[str, Any], Any]:
    response = await client().chat.completions.create(
        model=settings.chat_model,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, *messages],  # type: ignore[arg-type]
    )
    raw = (response.choices[0].message.content or "").strip()
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("expected a JSON object")
        return parsed, response
    except (json.JSONDecodeError, ValueError):
        log.warning("llm_json_parse_failed", extra={"preview": raw[:200]})
        return dict(default), response
