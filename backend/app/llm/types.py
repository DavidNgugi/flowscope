"""Provider-neutral types for the LLM layer.

Everything above this module (stages, prompts) speaks this canonical shape and
never sees a provider SDK. Each adapter in `app/llm/providers/` is responsible
for translating to and from its vendor's wire format.

Message content is a list of parts:

    {"role": "user", "content": [{"type": "text", "text": "..."}]}
    {"role": "user", "content": [{"type": "image", "media_type": "image/jpeg", "data": "<base64>"}]}

A plain string is also accepted as `content` and is treated as a single text
part, so callers that only send text stay readable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "assistant"]
StructuredStrategy = Literal["forced_tool", "auto_tool", "json_schema", "json_object"]

# Canonical content part. Only these two kinds exist today; adding a third
# (e.g. documents) means adding a branch to every adapter, which is the point.
TextPart = dict[str, Any]  # {"type": "text", "text": str}
ImagePart = dict[str, Any]  # {"type": "image", "media_type": str, "data": str}
ContentPart = TextPart | ImagePart


def text_part(text: str) -> TextPart:
    return {"type": "text", "text": text}


def image_part(media_type: str, data_b64: str) -> ImagePart:
    return {"type": "image", "media_type": media_type, "data": data_b64}


@dataclass(frozen=True)
class ToolSpec:
    """A single structured-output tool. FlowScope uses one tool per call and
    requires the model to fill it, so the schema is the contract."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    """Normalized token accounting. Cache fields are Anthropic-specific and
    stay zero for providers without prompt caching."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    service_tier: str | None = None
    inference_geo: str | None = None


@dataclass(frozen=True)
class ToolCallResult:
    """A completed structured call.

    `data` is the parsed object the caller asked for; `model` and `provider`
    are the ones that actually served it, which is what gets stored against
    the analysis row rather than whatever happened to be configured later.
    """

    data: dict[str, Any]
    provider: str
    model: str
    request_id: str | None = None
    usage: Usage = field(default_factory=Usage)


class LLMError(RuntimeError):
    """Base class for LLM failures that should surface to the caller."""


@dataclass(frozen=True)
class ModelPricing:
    """USD per million tokens, as published by the provider. Cache fields are
    zero where the provider has no equivalent."""

    input_per_mtok: float
    output_per_mtok: float
    cache_creation_per_mtok: float = 0.0
    cache_read_per_mtok: float = 0.0


class ProviderNotConfigured(LLMError):
    """The selected provider has no usable credential or base URL."""


class ModelCannotDoThis(LLMError):
    """The selected model lacks a capability the call requires (e.g. vision)."""


class ProviderResponseError(LLMError):
    """The provider answered, but not with the structured object we required."""
