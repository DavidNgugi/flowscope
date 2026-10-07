"""Model resolution: purpose in, configured provider + profile out.

A "purpose" is what the caller is trying to do, not which vendor to use:

    vision      analyse a frame (requires a vision-capable model)
    synthesis   reconstruct one video's flow from its frame analyses (text)
    comparison  compare several videos (text)

Each purpose can be pointed at a different provider/model, which is how a cheap
text model can do synthesis while a vision model does the frames. Anything left
unset falls back to the default pair.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import Settings, settings as default_settings
from app.llm.catalog import DEFAULT_BASE_URLS, ModelProfile, profile_for
from app.llm.providers.anthropic_provider import AnthropicProvider
from app.llm.providers.base import LLMProvider
from app.llm.providers.openai_provider import OpenAICompatibleProvider
from app.llm.types import ProviderNotConfigured

logger = logging.getLogger("flowscope.llm.registry")

PURPOSES = ("default", "vision", "synthesis", "comparison")

# Which env var holds each provider's credential, for error messages.
_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
}


@dataclass(frozen=True)
class ResolvedModel:
    provider: LLMProvider
    profile: ModelProfile

    def describe(self) -> str:
        return f"{self.profile.provider}/{self.profile.model}"


class ModelRegistry:
    """Builds provider clients on demand and resolves a model per purpose.

    Holds only immutable configuration and lazily-created HTTP clients; no run
    or tenant state, so one instance per process is safe.
    """

    def __init__(self, config: Settings | None = None) -> None:
        self._settings = config or default_settings
        self._providers: dict[str, LLMProvider] = {}

    # ---- resolution -----------------------------------------------------

    def resolve(self, purpose: str = "default") -> ResolvedModel:
        provider_name, model = self._selection(purpose)
        if not model:
            raise ProviderNotConfigured(
                f"No model configured for purpose '{purpose}'. Set LLM_MODEL "
                f"(and optionally LLM_{purpose.upper()}_MODEL) in backend/.env."
            )
        provider = self._provider(provider_name)
        return ResolvedModel(provider=provider, profile=profile_for(provider_name, model))

    def _selection(self, purpose: str) -> tuple[str, str]:
        s = self._settings
        if purpose == "vision":
            return (
                s.llm_vision_provider or s.llm_provider,
                s.llm_vision_model or s.llm_model,
            )
        if purpose == "synthesis":
            return (
                s.llm_synthesis_provider or s.llm_provider,
                s.llm_synthesis_model or s.llm_model,
            )
        if purpose == "comparison":
            return (
                s.llm_comparison_provider or s.llm_synthesis_provider or s.llm_provider,
                s.llm_comparison_model or s.llm_synthesis_model or s.llm_model,
            )
        return (s.llm_provider, s.llm_model)

    # ---- provider construction -----------------------------------------

    def _provider(self, name: str) -> LLMProvider:
        if name in self._providers:
            return self._providers[name]
        s = self._settings
        provider: LLMProvider

        if name == "anthropic":
            self._require_key(s.anthropic_api_key, name)
            provider = AnthropicProvider(api_key=s.anthropic_api_key, timeout=s.llm_timeout_seconds)
        elif name == "openai":
            self._require_key(s.openai_api_key, name)
            provider = OpenAICompatibleProvider(
                name="openai",
                api_key=s.openai_api_key,
                base_url=s.openai_base_url or DEFAULT_BASE_URLS["openai"],
                timeout=s.llm_timeout_seconds,
                image_detail=s.llm_image_detail,
                reasoning_effort=s.llm_reasoning_effort,
            )
        elif name == "deepseek":
            self._require_key(s.deepseek_api_key, name)
            provider = OpenAICompatibleProvider(
                name="deepseek",
                api_key=s.deepseek_api_key,
                base_url=s.deepseek_base_url or DEFAULT_BASE_URLS["deepseek"],
                timeout=s.llm_timeout_seconds,
                image_detail=s.llm_image_detail,
                reasoning_effort=s.llm_reasoning_effort,
            )
        elif name == "openai_compatible":
            self._require_key(s.openai_compatible_api_key, name)
            if not s.openai_compatible_base_url:
                raise ProviderNotConfigured(
                    "OPENAI_COMPATIBLE_BASE_URL is not set. Point it at the "
                    "OpenAI-compatible endpoint you want to use."
                )
            provider = OpenAICompatibleProvider(
                name="openai_compatible",
                api_key=s.openai_compatible_api_key,
                base_url=s.openai_compatible_base_url,
                timeout=s.llm_timeout_seconds,
                image_detail=s.llm_image_detail,
                reasoning_effort=s.llm_reasoning_effort,
            )
        else:
            raise ProviderNotConfigured(
                f"Unknown provider '{name}'. Known providers: anthropic, openai, "
                f"deepseek, openai_compatible."
            )

        self._providers[name] = provider
        return provider

    @staticmethod
    def _require_key(key: str, provider: str) -> None:
        if not key:
            raise ProviderNotConfigured(
                f"Provider '{provider}' has no credential. Set {_KEY_ENV.get(provider, 'the API key')} "
                f"in backend/.env (or choose a different provider)."
            )

    def available(self) -> list[str]:
        """Providers that have a credential configured, for diagnostics."""
        s = self._settings
        configured = {
            "anthropic": bool(s.anthropic_api_key),
            "openai": bool(s.openai_api_key),
            "deepseek": bool(s.deepseek_api_key),
            "openai_compatible": bool(s.openai_compatible_api_key and s.openai_compatible_base_url),
        }
        return sorted(name for name, ok in configured.items() if ok)


_registry: ModelRegistry | None = None


def get_registry() -> ModelRegistry:
    global _registry
    if _registry is None:
        _registry = ModelRegistry()
    return _registry


def reset_registry() -> None:
    """For tests: drop cached providers so new settings take effect."""
    global _registry
    _registry = None
