import sys
from types import SimpleNamespace
from typing import Any, cast

import anthropic
import httpx2
import openai
import pytest

from listing_agent.config import MarketplaceConfig
from listing_agent.models import ListingDraft, MarketResearch, ProductBrief
from listing_agent.prompts import draft_request, research_request, revise_request
from listing_agent.providers import FakeProvider, LLMRequest, ProviderError, get_provider
from listing_agent.providers.anthropic_provider import AnthropicProvider
from listing_agent.providers.openai_provider import OpenAIProvider
from listing_agent.rules import check_listing

REQUEST = LLMRequest(task="research", system="s", user="u", payload={})
RESEARCH = MarketResearch(keywords=["mug"])
CONNECTION_ERROR_REQUEST = httpx2.Request("POST", "https://api.example.test")


# ---------------------------------------------------------------- fake provider


def test_fake_research_extracts_keywords_and_prices(brief: ProductBrief) -> None:
    research = FakeProvider().complete(research_request(brief), MarketResearch)
    assert research.keywords == brief.seed_keywords
    assert research.observed_price_low == pytest.approx(52.90)
    assert research.observed_price_high == pytest.approx(89.90)
    assert len(research.buyer_concerns) == len(brief.limitations)


def test_fake_research_without_seed_keywords_uses_the_name(brief: ProductBrief) -> None:
    plain = brief.model_copy(update={"seed_keywords": [], "research_notes": ""})
    research = FakeProvider().complete(research_request(plain), MarketResearch)
    assert research.keywords[0] == "handmade"
    assert research.observed_price_high is None


def test_fake_first_draft_overshoots_the_title_limit(
    brief: ProductBrief, config: MarketplaceConfig
) -> None:
    research = FakeProvider().complete(research_request(brief), MarketResearch)
    draft = FakeProvider().complete(draft_request(brief, research, config.rules), ListingDraft)
    assert len(draft.titles[0]) > config.rules.max_title_length
    assert "What it does not do:" in draft.description


def test_fake_validates_against_the_requested_schema(brief: ProductBrief) -> None:
    with pytest.raises(ValueError, match="validation error"):
        FakeProvider().complete(research_request(brief), ListingDraft)


# ---------------------------------------------------------------- factory


def test_get_provider_fake() -> None:
    assert get_provider("fake").name == "fake"


def test_get_provider_unknown() -> None:
    with pytest.raises(ProviderError, match="unknown provider 'llama'"):
        get_provider("llama")


@pytest.mark.parametrize(
    ("name", "env"), [("anthropic", "ANTHROPIC_API_KEY"), ("openai", "OPENAI_API_KEY")]
)
def test_get_provider_real(name: str, env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(env, "test-key")
    provider = get_provider(name, model="some-model")
    assert provider.name == name
    assert isinstance(provider, AnthropicProvider | OpenAIProvider)
    assert provider.model == "some-model"


def test_get_provider_missing_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "listing_agent.providers.anthropic_provider")
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(
        ProviderError, match=r"pip install 'marketplace-listing-agent\[anthropic\]'"
    ):
        get_provider("anthropic")


def test_model_defaults_come_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_MODEL", "env-model")
    provider = AnthropicProvider(client=cast(anthropic.Anthropic, object()))
    assert provider.model == "env-model"


# ---------------------------------------------------------------- anthropic (stub client)


class StubAnthropicMessages:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response, self.error = response, error
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def anthropic_with(messages: StubAnthropicMessages) -> AnthropicProvider:
    client = cast(anthropic.Anthropic, SimpleNamespace(messages=messages))
    return AnthropicProvider(model="claude-test", client=client)


def test_anthropic_returns_parsed_output() -> None:
    messages = StubAnthropicMessages(
        SimpleNamespace(stop_reason="end_turn", parsed_output=RESEARCH)
    )
    assert anthropic_with(messages).complete(REQUEST, MarketResearch) == RESEARCH
    call = messages.calls[0]
    assert call["model"] == "claude-test"
    assert call["system"] == "s"
    assert call["messages"] == [{"role": "user", "content": "u"}]
    assert call["output_format"] is MarketResearch


@pytest.mark.parametrize(
    ("stop_reason", "parsed", "message"),
    [
        ("refusal", None, "declined"),
        ("max_tokens", None, "max_tokens"),
        ("end_turn", None, "no structured output"),
    ],
)
def test_anthropic_unusable_answers(stop_reason: str, parsed: Any, message: str) -> None:
    messages = StubAnthropicMessages(SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed))
    with pytest.raises(ProviderError, match=message):
        anthropic_with(messages).complete(REQUEST, MarketResearch)


def test_anthropic_api_error_becomes_provider_error() -> None:
    error = anthropic.APIConnectionError(request=CONNECTION_ERROR_REQUEST)
    with pytest.raises(ProviderError, match="Anthropic API error"):
        anthropic_with(StubAnthropicMessages(error=error)).complete(REQUEST, MarketResearch)


# ---------------------------------------------------------------- openai (stub client)


class StubResponses:
    def __init__(self, parsed: Any = None, error: Exception | None = None) -> None:
        self.parsed, self.error = parsed, error
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(output_parsed=self.parsed)


def openai_with(responses: StubResponses) -> OpenAIProvider:
    return OpenAIProvider(
        model="gpt-test", client=cast(openai.OpenAI, SimpleNamespace(responses=responses))
    )


def test_openai_returns_parsed_output() -> None:
    responses = StubResponses(RESEARCH)
    assert openai_with(responses).complete(REQUEST, MarketResearch) == RESEARCH
    assert responses.calls[0]["text_format"] is MarketResearch
    assert responses.calls[0]["instructions"] == "s"


def test_openai_empty_output() -> None:
    with pytest.raises(ProviderError, match="no structured output"):
        openai_with(StubResponses(None)).complete(REQUEST, MarketResearch)


def test_openai_api_error_becomes_provider_error() -> None:
    error = openai.APIConnectionError(request=CONNECTION_ERROR_REQUEST)
    with pytest.raises(ProviderError, match="OpenAI API error"):
        openai_with(StubResponses(error=error)).complete(REQUEST, MarketResearch)


def test_with_feedback_appends_the_error() -> None:
    retried = REQUEST.with_feedback("titles: too long")
    assert retried.user.startswith("u")
    assert "rejected: titles: too long" in retried.user
    assert REQUEST.user == "u"


def test_fake_revise_handles_duplicates_long_keywords_and_no_limitations(
    brief: ProductBrief, config: MarketplaceConfig
) -> None:
    long_keyword = "extra large handmade stoneware ceramic coffee mug for the office"
    sparse = brief.model_copy(
        update={
            "limitations": [],
            "attributes": [],
            "seed_keywords": ["mug", "Mug", long_keyword, "kiln fired"],
        }
    )
    provider = FakeProvider()
    research = provider.complete(research_request(sparse), MarketResearch)
    assert research.keywords == ["mug", long_keyword, "kiln fired"]
    draft = provider.complete(draft_request(sparse, research, config.rules), ListingDraft)
    assert "What it does not do:" not in draft.description
    assert len(draft.faq) == 1

    report = check_listing(draft, config.rules, research.keywords)
    revised = provider.complete(revise_request(draft, report, research, config.rules), ListingDraft)
    assert all(len(t) <= config.rules.max_title_length for t in revised.titles)
    assert revised.description.endswith(f"Also fits searches for: {long_keyword}.")
