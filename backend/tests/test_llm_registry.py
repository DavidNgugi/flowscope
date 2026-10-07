"""Multi-provider LLM layer: resolution, capability gating and pricing.

No network calls. These assert the parts that are easy to get subtly wrong and
expensive to discover live: which provider a purpose resolves to, whether a
model is allowed to receive images, how unknown models are priced, and that a
provider without a credential fails with an actionable message instead of at
import time.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.llm.catalog import profile_for
from app.llm.registry import ModelRegistry
from app.llm.types import ModelCannotDoThis, ProviderNotConfigured


def make_settings(**overrides) -> Settings:
    base = {
        "llm_provider": "openai",
        "llm_model": "gpt-5-mini",
        "openai_api_key": "test-openai",
        "anthropic_api_key": "",
        "deepseek_api_key": "",
        "openai_compatible_api_key": "",
        "openai_compatible_base_url": "",
        "anthropic_model": "claude-sonnet-5",
    }
    base.update(overrides)
    return Settings(**base)


# ---- purpose resolution --------------------------------------------------


def test_purpose_overrides_fall_back_to_the_default() -> None:
    registry = ModelRegistry(make_settings(
        llm_synthesis_provider="deepseek",
        llm_synthesis_model="deepseek-flash",
        deepseek_api_key="test-deepseek",
    ))
    assert registry.resolve("default").describe() == "openai/gpt-5-mini"
    assert registry.resolve("vision").describe() == "openai/gpt-5-mini"
    assert registry.resolve("synthesis").describe() == "deepseek/deepseek-flash"
    # comparison falls back to synthesis, then to default.
    assert registry.resolve("comparison").describe() == "deepseek/deepseek-flash"


def test_anthropic_model_is_used_when_only_that_is_configured() -> None:
    registry = ModelRegistry(make_settings(
        llm_provider="anthropic", llm_model="", anthropic_model="claude-sonnet-5",
        anthropic_api_key="test-anthropic",
    ))
    assert registry.resolve("default").describe() == "anthropic/claude-sonnet-5"


# ---- credentials ---------------------------------------------------------


def test_missing_key_names_the_env_var() -> None:
    registry = ModelRegistry(make_settings(llm_provider="deepseek", llm_model="deepseek-flash"))
    with pytest.raises(ProviderNotConfigured) as excinfo:
        registry.resolve("default")
    assert "DEEPSEEK_API_KEY" in str(excinfo.value)


def test_available_lists_only_configured_providers() -> None:
    registry = ModelRegistry(make_settings(deepseek_api_key="test-deepseek"))
    assert registry.available() == ["deepseek", "openai"]


def test_custom_endpoint_needs_a_base_url() -> None:
    registry = ModelRegistry(make_settings(
        llm_provider="openai_compatible", llm_model="local-model",
        openai_compatible_api_key="test", openai_compatible_base_url="",
    ))
    with pytest.raises(ProviderNotConfigured) as excinfo:
        registry.resolve("default")
    assert "OPENAI_COMPATIBLE_BASE_URL" in str(excinfo.value)


def test_unknown_provider_is_rejected() -> None:
    registry = ModelRegistry(make_settings(llm_provider="not-a-provider"))
    with pytest.raises(ProviderNotConfigured):
        registry.resolve("default")


# ---- capability gating ---------------------------------------------------


def test_vision_capability_differs_within_one_provider() -> None:
    assert profile_for("deepseek", "deepseek-flash").vision is True
    assert profile_for("deepseek", "deepseek-v4-pro").vision is False


def test_text_only_model_refuses_frame_analyses() -> None:
    from app.llm.providers.openai_provider import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(
        name="deepseek", api_key="test", base_url="https://api.deepseek.com/v1"
    )
    messages = [{"role": "user", "content": [{"type": "image", "media_type": "image/jpeg", "data": "x"}]}]
    with pytest.raises(ModelCannotDoThis) as excinfo:
        provider.require_vision(profile_for("deepseek", "deepseek-v4-pro"), messages)
    assert "cannot accept images" in str(excinfo.value)
    # The vision-capable sibling is allowed through.
    provider.require_vision(profile_for("deepseek", "deepseek-flash"), messages)


# ---- structured output strategies ---------------------------------------


def test_sonnet_5_5_drops_forced_tool_choice() -> None:
    # Sonnet 5.5 returns an error on forced tool use; the profile must not
    # ask for it, or every structured call fails.
    assert "forced_tool" not in profile_for("anthropic", "claude-sonnet-5-5").structured
    assert "forced_tool" in profile_for("anthropic", "claude-sonnet-5").structured


def test_reasoning_models_use_the_completion_token_field() -> None:
    assert profile_for("openai", "gpt-5-mini").uses_max_completion_tokens is True
    assert profile_for("openai", "gpt-4o").uses_max_completion_tokens is False


# ---- pricing -------------------------------------------------------------


def test_known_models_have_pricing_and_unknown_models_do_not() -> None:
    assert profile_for("openai", "gpt-5-mini").pricing is not None
    assert profile_for("deepseek", "deepseek-flash").pricing is not None
    assert profile_for("openai", "some-future-model").pricing is None


def test_deepseek_pricing_uses_the_higher_peak_rate() -> None:
    pricing = profile_for("deepseek", "deepseek-flash").pricing
    assert pricing is not None
    # DeepSeek publishes peak/off-peak; peak is recorded so an estimate never
    # understates the bill.
    assert pricing.input_per_mtok == 0.30
    assert pricing.output_per_mtok == 1.20


def test_longest_prefix_wins_for_similar_model_names() -> None:
    mini = profile_for("openai", "gpt-5.4-mini").pricing
    full = profile_for("openai", "gpt-5.4").pricing
    assert mini is not None and full is not None
    assert mini.input_per_mtok == 0.75 and full.input_per_mtok == 2.50


# ---- usage attribution ---------------------------------------------------


def test_pricing_lookup_is_provider_aware() -> None:
    from app.llm.usage import pricing_for_model

    # A model id that exists at two providers must not borrow the wrong rate.
    assert pricing_for_model("deepseek-flash", "deepseek") is not None
    assert pricing_for_model("deepseek-flash", "openai") is None
