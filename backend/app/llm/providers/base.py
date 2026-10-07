"""Shared provider contract and helpers.

An adapter's only job is wire-format translation: canonical messages and a
`ToolSpec` in, a parsed object and normalized `Usage` out. Retries, concurrency
and usage attribution live in `app/llm/client.py` so they are implemented once.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

from app.llm.catalog import ModelProfile
from app.llm.types import (
    ModelCannotDoThis,
    ProviderResponseError,
    ToolCallResult,
    ToolSpec,
)

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def has_images(messages: list[dict]) -> bool:
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(
            isinstance(part, dict) and part.get("type") == "image" for part in content
        ):
            return True
    return False


def extract_json_object(text: str) -> dict[str, Any]:
    """Pull a JSON object out of free text.

    Used only as a last resort when a provider ignores every structured-output
    mechanism. Raises rather than returning a half-parsed object.
    """
    if not text:
        raise ProviderResponseError("model returned no content to parse")
    candidates: list[str] = []
    fenced = _JSON_FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate.strip())
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ProviderResponseError(f"could not parse a JSON object from model output: {text[:200]!r}")


class LLMProvider(ABC):
    """One configured vendor endpoint."""

    name: str = "provider"

    def __init__(self, *, api_key: str, base_url: str | None = None, timeout: float = 180.0) -> None:
        self.api_key = api_key
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout

    def require_vision(self, profile: ModelProfile, messages: list[dict]) -> None:
        if has_images(messages) and not profile.vision:
            raise ModelCannotDoThis(
                f"{profile.provider}/{profile.model} cannot accept images"
                + (f" ({profile.note})" if profile.note else "")
                + ". Choose a vision-capable model for the vision purpose."
            )

    @abstractmethod
    async def call_tool(
        self,
        *,
        profile: ModelProfile,
        system: str,
        messages: list[dict],
        tool: ToolSpec,
        max_tokens: int,
    ) -> ToolCallResult:
        """Return the parsed tool input, or raise."""
