"""Known models, what they can do, and what they cost.

This is the only place that knows vendor model names. Everything else asks the
registry for a model by *purpose* (vision, synthesis, comparison) and gets back
a profile.

Pricing is transcribed from the providers' own pricing pages on the date noted,
and is used only to estimate the cost of a run. Where a provider publishes time
varying rates, the **higher** rate is recorded so an estimate never understates
the bill. Read the provider page before trusting a number for billing.

- OpenAI: https://developers.openai.com/api/docs/pricing (checked 7 Oct 2026)
- DeepSeek: https://api-docs.deepseek.com/quick_start/pricing (checked 7 Oct 2026)
- Anthropic: https://platform.claude.com/docs/en/models/sonnet-5-5/overview (checked 7 Oct 2026)
"""

from __future__ import annotations

from dataclasses import dataclass

from app.llm.types import ModelPricing, StructuredStrategy

# Providers we can speak to. `openai_compatible` is the escape hatch: any
# vendor (or self-hosted server) that implements the OpenAI chat-completions
# wire format can be pointed at with a base URL and a key.
PROVIDERS = ("anthropic", "openai", "deepseek", "openai_compatible")

DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "openai_compatible": "",
}

# Keys are matched as prefixes against the requested model id, longest first,
# so "gpt-5.4-mini" wins over "gpt-5.4". Provider is implied by the table.
_OPENAI_PRICING: tuple[tuple[str, float, float, float], ...] = (
    # (model prefix, input, cached input, output)
    ("gpt-6-astra", 10.00, 1.00, 50.00),
    ("gpt-6.1-sol", 2.00, 0.10, 10.00),
    ("gpt-6-luna", 0.10, 0.01, 0.50),
    ("gpt-6-sol", 2.00, 0.20, 10.00),
    ("gpt-5.6-sol", 4.00, 0.40, 20.00),
    ("gpt-5.6-terra", 2.00, 0.20, 12.00),
    ("gpt-5.6-luna", 0.20, 0.02, 1.20),
    ("gpt-5.5-pro", 30.00, 0.0, 180.00),
    ("gpt-5.5", 5.00, 0.50, 30.00),
    ("gpt-5.4-pro", 30.00, 0.0, 180.00),
    ("gpt-5.4-mini", 0.75, 0.075, 4.50),
    ("gpt-5.4-nano", 0.20, 0.02, 1.25),
    ("gpt-5.4", 2.50, 0.25, 15.00),
    ("gpt-5-mini", 0.25, 0.025, 2.00),
    ("gpt-5-nano", 0.05, 0.005, 0.40),
    ("gpt-5-pro", 15.00, 0.0, 120.00),
    ("gpt-5", 1.25, 0.125, 10.00),
    ("gpt-4.1-mini", 0.40, 0.10, 1.60),
    ("gpt-4.1-nano", 0.10, 0.025, 0.40),
    ("gpt-4.1", 2.00, 0.50, 8.00),
    ("gpt-4o-mini", 0.15, 0.075, 0.60),
    ("gpt-4o", 2.50, 1.25, 10.00),
    ("o4-mini", 1.10, 0.275, 4.40),
    ("o3-mini", 1.10, 0.55, 4.40),
    ("o3", 2.00, 0.50, 8.00),
)

# DeepSeek publishes peak and off-peak rates; peak is recorded here.
_DEEPSEEK_PRICING: tuple[tuple[str, float, float, float], ...] = (
    ("deepseek-v4-pro", 1.32, 0.044, 3.96),
    ("deepseek-flash", 0.30, 0.006, 1.20),
    ("deepseek-v4-flash", 0.30, 0.006, 1.20),
    ("deepseek-chat", 0.30, 0.006, 1.20),
    ("deepseek-reasoner", 0.66, 0.022, 1.98),
)

_ANTHROPIC_PRICING: tuple[tuple[str, float, float, float, float], ...] = (
    # (model prefix, input, output, cache write, cache read)
    ("claude-sonnet-5-5", 2.0, 10.0, 2.5, 0.20),
    ("claude-sonnet-5", 2.0, 10.0, 2.5, 0.20),
    ("claude-opus-5", 4.0, 20.0, 5.0, 0.40),
    ("claude-sonnet-4-6", 3.0, 15.0, 3.75, 0.30),
    ("claude-sonnet-4-5", 3.0, 15.0, 3.75, 0.30),
    ("claude-haiku-4-5", 1.0, 5.0, 1.25, 0.10),
    ("claude-opus-4-6", 5.0, 25.0, 6.25, 0.50),
)


@dataclass(frozen=True)
class ModelProfile:
    """What a model can do, and how to talk to it."""

    provider: str
    model: str
    vision: bool
    # Preferred order of structured-output strategies. The adapter tries these
    # in turn and only gives up when all of them fail.
    structured: tuple[StructuredStrategy, ...] = ("forced_tool",)
    # Newer OpenAI reasoning models reject `max_tokens` and `temperature`.
    uses_max_completion_tokens: bool = False
    supports_temperature: bool = True
    context_tokens: int | None = None
    pricing: ModelPricing | None = None
    note: str = ""


def _openai_profile(model: str) -> ModelProfile:
    lowered = model.casefold()
    no_vision = any(
        token in lowered
        for token in ("embedding", "tts", "transcribe", "whisper", "image", "moderation", "dall-e")
    )
    # gpt-5.x and the o-series take images; so do 4.1/4o. Search-preview and
    # audio/realtime variants are excluded because they are not chat models.
    search_only = "search" in lowered and "search-api" in lowered
    vision = not no_vision and not search_only
    reasoning = lowered.startswith(("o1", "o3", "o4", "gpt-5", "gpt-6"))
    pricing = None
    for prefix, inp, cached, out in _OPENAI_PRICING:
        if lowered.startswith(prefix):
            pricing = ModelPricing(
                input_per_mtok=inp,
                output_per_mtok=out,
                cache_creation_per_mtok=0.0,
                cache_read_per_mtok=cached,
            )
            break
    return ModelProfile(
        provider="openai",
        model=model,
        vision=vision,
        # Forced function calling is supported across the chat family; JSON
        # schema is the fallback when a model refuses the tool route.
        structured=("forced_tool", "json_schema", "json_object"),
        uses_max_completion_tokens=reasoning,
        supports_temperature=not reasoning,
        context_tokens=272_000 if reasoning else 128_000,
        pricing=pricing,
    )


def _deepseek_profile(model: str) -> ModelProfile:
    lowered = model.casefold()
    # DeepSeek-V4.1-Flash is the vision-capable model; v4-pro is text-only.
    vision = "pro" not in lowered
    pricing = None
    for prefix, inp, cached, out in _DEEPSEEK_PRICING:
        if lowered.startswith(prefix):
            pricing = ModelPricing(
                input_per_mtok=inp,
                output_per_mtok=out,
                cache_creation_per_mtok=0.0,
                cache_read_per_mtok=cached,
            )
            break
    return ModelProfile(
        provider="deepseek",
        model=model,
        vision=vision,
        structured=("forced_tool", "auto_tool", "json_object"),
        uses_max_completion_tokens=False,
        supports_temperature=True,
        context_tokens=1_000_000,
        pricing=pricing,
        note="" if vision else "text-only model: cannot analyse frames",
    )


def _anthropic_profile(model: str) -> ModelProfile:
    lowered = model.casefold()
    pricing = None
    for prefix, inp, out, cache_write, cache_read in _ANTHROPIC_PRICING:
        if lowered.startswith(prefix):
            pricing = ModelPricing(inp, out, cache_write, cache_read)
            break
    # Sonnet 5.5 rejects forced tool use outright, so it must use the
    # auto-tool path; see the migration notes for that model.
    forced = "5-5" not in lowered
    return ModelProfile(
        provider="anthropic",
        model=model,
        vision=True,
        structured=("forced_tool", "auto_tool") if forced else ("auto_tool",),
        uses_max_completion_tokens=False,
        supports_temperature=True,
        context_tokens=1_000_000,
        pricing=pricing,
        note="" if forced else "forced tool use unsupported; uses auto tool choice",
    )


def profile_for(provider: str, model: str) -> ModelProfile:
    """Best-known profile for a model. Unknown models get a conservative
    profile: no pricing (so cost stays `None` rather than guessed), and the
    safest structured-output order for the provider."""
    if provider == "anthropic":
        return _anthropic_profile(model)
    if provider == "deepseek":
        return _deepseek_profile(model)
    if provider == "openai":
        return _openai_profile(model)
    # Custom OpenAI-compatible endpoint: assume vision and tool calling,
    # because those are the OpenAI-compatible features FlowScope uses.
    return ModelProfile(
        provider=provider,
        model=model,
        vision=True,
        structured=("forced_tool", "json_object", "json_schema"),
        uses_max_completion_tokens=False,
        supports_temperature=True,
        pricing=None,
        note="custom endpoint: capabilities and pricing unverified",
    )
