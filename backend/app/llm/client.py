"""Provider-agnostic structured LLM calls.

One entry point — `call_tool` — used by every stage. It resolves the configured
provider for the caller's *purpose*, applies a single retry authority, bounds
concurrency across the whole process, and records token usage with the provider
and model that actually served the request.
"""

from __future__ import annotations

import asyncio
import logging
import time

import anthropic
import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import settings
from app.llm.registry import ResolvedModel, get_registry
from app.llm.types import ToolCallResult, ToolSpec
from app.llm.usage import AIUsageContext, record_ai_usage

logger = logging.getLogger("flowscope.llm")

_semaphore: asyncio.Semaphore | None = None

# HTTP statuses worth another attempt: rate limits and transient server errors.
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    """One retry authority for every provider. Adapters deliberately do not
    retry: they surface retryable transport failures unchanged."""
    if isinstance(
        exc,
        (
            anthropic.APIConnectionError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
            httpx.TransportError,
        ),
    ):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    return False


def get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.max_concurrent_llm_calls)
    return _semaphore


@retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    retry=retry_if_exception(_is_retryable),
)
async def _call_once(
    resolved: ResolvedModel,
    system: str,
    messages: list[dict],
    tool: ToolSpec,
    max_tokens: int,
) -> ToolCallResult:
    return await resolved.provider.call_tool(
        profile=resolved.profile,
        system=system,
        messages=messages,
        tool=tool,
        max_tokens=max_tokens,
    )


async def call_tool(
    *,
    system: str,
    messages: list[dict],
    tool_name: str,
    tool_schema: dict,
    usage_context: AIUsageContext,
    max_tokens: int = 1500,
    tool_description: str | None = None,
    purpose: str = "default",
) -> ToolCallResult:
    """Ask the configured model for one structured object.

    `purpose` selects which provider/model pair to use (vision, synthesis,
    comparison, default) -- see app/llm/registry.py.
    """
    resolved = get_registry().resolve(purpose)
    tool = ToolSpec(
        name=tool_name,
        description=tool_description or f"Record the structured {tool_name} result.",
        input_schema=tool_schema,
    )

    started = time.monotonic()
    async with get_semaphore():
        result = await _call_once(resolved, system, messages, tool, max_tokens)

    await record_ai_usage(
        context=usage_context,
        provider=result.provider,
        model=result.model,
        request_id=result.request_id,
        usage=result.usage,
        duration_ms=round((time.monotonic() - started) * 1000),
    )
    return result
