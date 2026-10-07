"""OpenAI-compatible chat-completions adapter.

Covers OpenAI itself, DeepSeek, and any other vendor or self-hosted server that
implements the same wire format — the point of the multi-provider SDK is that
adding such a vendor is a config entry, not a new adapter.

Uses httpx rather than a vendor SDK so the adapter cannot drift with an SDK's
opinionated request shape, and so no new dependency is needed to add a vendor.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.llm.catalog import ModelProfile
from app.llm.providers.base import LLMProvider, extract_json_object
from app.llm.types import ProviderResponseError, ToolCallResult, ToolSpec, Usage

logger = logging.getLogger("flowscope.llm.openai")

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

# Reasoning models (gpt-5.x, o-series) spend part of the completion budget on
# hidden reasoning tokens before they emit a tool call. A budget sized for the
# visible answer alone gets exhausted mid-reasoning, and the response comes back
# with finish_reason=length and no tool call at all -- so give these models a
# floor.
MIN_REASONING_COMPLETION_TOKENS = 4000


def _to_openai_content(content: Any, image_detail: str) -> Any:
    if isinstance(content, str):
        return content
    parts: list[dict] = []
    for part in content:
        kind = part.get("type")
        if kind == "text":
            parts.append({"type": "text", "text": part["text"]})
        elif kind == "image":
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{part['media_type']};base64,{part['data']}",
                        "detail": image_detail,
                    },
                }
            )
    return parts


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str,
        timeout: float = 180.0,
        image_detail: str = "high",
        reasoning_effort: str = "",
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url, timeout=timeout)
        self.name = name
        self.image_detail = image_detail
        self.reasoning_effort = reasoning_effort

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

        wire_messages: list[dict] = [{"role": "system", "content": system}]
        for message in messages:
            wire_messages.append(
                {
                    "role": message["role"],
                    "content": _to_openai_content(message["content"], self.image_detail),
                }
            )

        last_error: Exception | None = None
        for strategy in profile.structured:
            body = self._build_body(profile, wire_messages, tool, max_tokens, strategy)
            try:
                payload = await self._post(body)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                detail = exc.response.text[:200]
                if status in RETRYABLE_STATUS:
                    # Surface retryable failures to the caller's retry policy
                    # rather than silently burning another strategy.
                    raise
                logger.info(
                    "%s rejected %s for %s (%s %s); trying next strategy",
                    self.name, strategy, profile.model, status, detail,
                )
                last_error = exc
                continue

            message = (payload.get("choices") or [{}])[0].get("message") or {}
            finish_reason = (payload.get("choices") or [{}])[0].get("finish_reason")
            usage = _usage_from(payload.get("usage") or {})
            request_id = payload.get("id")

            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                raw = (tool_calls[0].get("function") or {}).get("arguments") or ""
                try:
                    data = json.loads(raw) if isinstance(raw, str) else dict(raw)
                except (ValueError, TypeError) as exc:
                    last_error = ProviderResponseError(f"unparseable tool arguments: {raw[:200]!r}")
                    last_error.__cause__ = exc
                    continue
                if isinstance(data, dict):
                    return ToolCallResult(
                        data=data, provider=self.name, model=profile.model,
                        request_id=request_id, usage=usage,
                    )

            content = message.get("content")
            if isinstance(content, list):
                # Some gateways return content as a list of parts.
                content = "".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            if isinstance(content, str) and content.strip():
                try:
                    return ToolCallResult(
                        data=extract_json_object(content), provider=self.name,
                        model=profile.model, request_id=request_id, usage=usage,
                    )
                except ProviderResponseError as exc:
                    last_error = exc
            elif finish_reason == "length":
                last_error = ProviderResponseError(
                    f"{profile.model} hit the completion budget before returning a tool call "
                    f"(finish_reason=length, reasoning is billed against the budget). "
                    f"Raise max_tokens or lower LLM_REASONING_EFFORT."
                )
            elif message.get("refusal"):
                last_error = ProviderResponseError(
                    f"{profile.model} refused the request: {str(message['refusal'])[:160]}"
                )
            else:
                last_error = ProviderResponseError(
                    f"{profile.model} returned neither tool arguments nor content "
                    f"(finish_reason={finish_reason})"
                )

        raise last_error or ProviderResponseError(f"{profile.model} produced no usable output")

    def _build_body(
        self,
        profile: ModelProfile,
        wire_messages: list[dict],
        tool: ToolSpec,
        max_tokens: int,
        strategy: str,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"model": profile.model, "messages": wire_messages}
        if profile.uses_max_completion_tokens:
            body["max_completion_tokens"] = max(max_tokens, MIN_REASONING_COMPLETION_TOKENS)
            if self.reasoning_effort:
                body["reasoning_effort"] = self.reasoning_effort
        else:
            body["max_tokens"] = max_tokens

        if strategy in ("forced_tool", "auto_tool"):
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
            ]
            body["tool_choice"] = (
                {"type": "function", "function": {"name": tool.name}}
                if strategy == "forced_tool"
                else "auto"
            )
        elif strategy == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": tool.name, "schema": tool.input_schema, "strict": False},
            }
        elif strategy == "json_object":
            body["response_format"] = {"type": "json_object"}
            # JSON mode requires the word "json" in the prompt on some vendors.
            body["messages"] = [
                *wire_messages,
                {
                    "role": "system",
                    "content": (
                        "Respond with a single json object matching this schema: "
                        + json.dumps(tool.input_schema)
                    ),
                },
            ]
        return body

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
            return response.json()


def _usage_from(usage: dict[str, Any]) -> Usage:
    details = usage.get("prompt_tokens_details") or {}
    return Usage(
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        cache_read_input_tokens=int(details.get("cached_tokens") or 0),
    )
