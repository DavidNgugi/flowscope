"""Anthropic Messages API adapter."""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from app.llm.catalog import ModelProfile
from app.llm.providers.base import LLMProvider, extract_json_object
from app.llm.types import ProviderResponseError, ToolCallResult, ToolSpec, Usage

logger = logging.getLogger("flowscope.llm.anthropic")

# Retryable transport/5xx failures. 400s are not retryable: they mean the
# request itself is unacceptable (for example forced tool use on a model that
# rejects it), and the caller should fall through to another strategy.
RETRYABLE = (
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)


def _to_anthropic_content(content: Any) -> Any:
    """Canonical parts -> Anthropic content blocks. Plain strings pass through."""
    if isinstance(content, str):
        return content
    blocks: list[dict] = []
    for part in content:
        kind = part.get("type")
        if kind == "text":
            blocks.append({"type": "text", "text": part["text"]})
        elif kind == "image":
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": part["media_type"],
                        "data": part["data"],
                    },
                }
            )
    return blocks


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, *, api_key: str, base_url: str | None = None, timeout: float = 180.0) -> None:
        super().__init__(api_key=api_key, base_url=base_url, timeout=timeout)
        # A base URL lets this adapter also serve Anthropic-compatible
        # endpoints, including DeepSeek's Anthropic-format API.
        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        self._client = anthropic.AsyncAnthropic(**kwargs)

    async def call_tool(
        self,
        *,
        profile: ModelProfile,
        system: str,
        messages: list[dict],
        tool: ToolSpec,
        max_tokens: int,
    ) -> ToolCallResult:
        self.require_vision(profile, messages)
        wire_messages = [
            {"role": m["role"], "content": _to_anthropic_content(m["content"])} for m in messages
        ]
        tools = [
            {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
        ]
        last_error: Exception | None = None

        for strategy in profile.structured:
            tool_choice: dict[str, Any] = (
                {"type": "tool", "name": tool.name}
                if strategy == "forced_tool"
                else {"type": "auto"}
            )
            try:
                response = await self._client.messages.create(
                    model=profile.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=wire_messages,
                    tools=tools,
                    tool_choice=tool_choice,
                )
            except anthropic.BadRequestError as exc:
                logger.info(
                    "anthropic rejected %s for %s (%s); trying next strategy",
                    strategy, profile.model, str(exc)[:160],
                )
                last_error = exc
                continue

            for block in response.content:
                if getattr(block, "type", None) == "tool_use" and block.name == tool.name:
                    return ToolCallResult(
                        data=dict(block.input or {}),
                        provider=self.name,
                        model=profile.model,
                        request_id=getattr(response, "id", None),
                        usage=_usage_from(response.usage),
                    )

            # No tool block: either auto tool choice declined, or the model put
            # the object in text. Only the text route is salvageable.
            text = "".join(
                getattr(block, "text", "") for block in response.content
                if getattr(block, "type", None) == "text"
            )
            if text.strip():
                try:
                    return ToolCallResult(
                        data=extract_json_object(text),
                        provider=self.name,
                        model=profile.model,
                        request_id=getattr(response, "id", None),
                        usage=_usage_from(response.usage),
                    )
                except ProviderResponseError as exc:
                    last_error = exc
            else:
                last_error = ProviderResponseError(
                    f"{profile.model} returned neither a {tool.name} tool call nor text"
                )

        raise last_error or ProviderResponseError(f"{profile.model} produced no usable output")


def _usage_from(usage: Any) -> Usage:
    return Usage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_creation_input_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        service_tier=getattr(usage, "service_tier", None),
        inference_geo=getattr(usage, "inference_geo", None),
    )
