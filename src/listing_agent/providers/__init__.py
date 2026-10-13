"""LLM providers. SDK modules are imported only when their provider is chosen."""

from listing_agent.providers.base import LLMProvider, LLMRequest, ProviderError
from listing_agent.providers.fake import FakeProvider

PROVIDERS = ("fake", "anthropic", "openai")

__all__ = [
    "PROVIDERS",
    "FakeProvider",
    "LLMProvider",
    "LLMRequest",
    "ProviderError",
    "get_provider",
]


def get_provider(name: str, model: str | None = None) -> LLMProvider:
    if name == "fake":
        return FakeProvider()
    try:
        if name == "anthropic":
            from listing_agent.providers.anthropic_provider import AnthropicProvider

            return AnthropicProvider(model=model)
        if name == "openai":
            from listing_agent.providers.openai_provider import OpenAIProvider

            return OpenAIProvider(model=model)
    except ImportError as error:
        raise ProviderError(
            f"the '{name}' provider needs its SDK: pip install 'marketplace-listing-agent[{name}]'"
        ) from error
    raise ProviderError(f"unknown provider '{name}' (choose one of: {', '.join(PROVIDERS)})")
