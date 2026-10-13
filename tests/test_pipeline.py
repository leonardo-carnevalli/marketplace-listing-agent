from collections.abc import Iterator
from decimal import Decimal

import pytest
from pydantic import BaseModel

from listing_agent.config import MarketplaceConfig
from listing_agent.models import MarketResearch, ProductBrief
from listing_agent.pipeline import Settings, StepFailedError, market_note, run
from listing_agent.providers import FakeProvider, LLMRequest, ProviderError


def ticking_clock() -> Iterator[float]:
    t = 0.0
    while True:
        yield t
        t += 0.005


def fixed_clock() -> float:
    return 0.0


class ScriptedProvider:
    """Wraps the fake provider and injects failures per task, in order."""

    name = "scripted"

    def __init__(self, failures: dict[str, list[Exception | None]], revise_echo: bool = False):
        self.failures = failures
        self.revise_echo = revise_echo
        self.requests: list[LLMRequest] = []

    def complete[T: BaseModel](self, request: LLMRequest, schema: type[T]) -> T:
        self.requests.append(request)
        queue = self.failures.get(request.task, [])
        if queue and (error := queue.pop(0)) is not None:
            raise error
        if request.task == "revise" and self.revise_echo:
            return schema.model_validate(request.payload["draft"])
        return FakeProvider().complete(request, schema)


def test_fake_run_revises_then_is_ready(brief: ProductBrief, config: MarketplaceConfig) -> None:
    clock = ticking_clock()
    result = run(brief, config, FakeProvider(), clock=lambda: next(clock))
    assert result.status == "ready_for_review"
    assert [r.score for r in result.revisions] == [70, 100]
    assert result.revisions[0].errors == 1
    assert all(len(t) <= config.rules.max_title_length for t in result.listing.titles)
    assert [s.step for s in result.steps] == ["research", "draft", "revise"]
    assert all(s.ok and s.latency_ms == 5 for s in result.steps)
    assert result.recommended is not None
    assert result.recommended.recommended.price == Decimal("60.90")
    assert set(result.quotes) == {"classic", "premium"}
    assert result.market_note is None
    assert len(result.trace_id) == 32


def test_invalid_output_is_retried_with_feedback(
    brief: ProductBrief, config: MarketplaceConfig
) -> None:
    provider = ScriptedProvider({"research": [ProviderError("empty answer")]})
    result = run(brief, config, provider, clock=fixed_clock)
    assert [(s.step, s.ok) for s in result.steps[:2]] == [("research", False), ("research", True)]
    assert result.steps[0].detail == "empty answer"
    assert "rejected: empty answer" in provider.requests[1].user


def test_step_fails_after_max_attempts(brief: ProductBrief, config: MarketplaceConfig) -> None:
    provider = ScriptedProvider({"draft": [ProviderError("down"), ProviderError("still down")]})
    with pytest.raises(StepFailedError, match="'draft' failed after 2 attempt") as info:
        run(brief, config, provider, Settings(max_attempts=2), clock=fixed_clock)
    assert info.value.step == "draft"


def test_plateau_stops_the_loop_and_flags_a_human(
    brief: ProductBrief, config: MarketplaceConfig
) -> None:
    provider = ScriptedProvider({}, revise_echo=True)  # the "revision" changes nothing
    result = run(brief, config, provider, Settings(max_revisions=3), clock=fixed_clock)
    assert result.status == "needs_attention"
    assert [r.iteration for r in result.revisions] == [0, 1]
    assert not result.quality.passed


def test_zero_revisions_keeps_the_first_draft(
    brief: ProductBrief, config: MarketplaceConfig
) -> None:
    result = run(brief, config, FakeProvider(), Settings(max_revisions=0), clock=fixed_clock)
    assert result.status == "needs_attention"
    assert len(result.revisions) == 1


def test_unreachable_margin_is_reported_not_raised(
    brief: ProductBrief, config: MarketplaceConfig
) -> None:
    greedy = brief.model_copy(update={"target_margin": Decimal("0.85")})
    result = run(greedy, config, FakeProvider(), clock=fixed_clock)
    assert result.recommended is None
    assert result.status == "needs_attention"
    assert len(result.pricing_issues) == 2


def test_unknown_listing_type_fails_before_any_llm_call(
    brief: ProductBrief, config: MarketplaceConfig
) -> None:
    provider = ScriptedProvider({})
    with pytest.raises(KeyError, match="gold"):
        run(brief.model_copy(update={"listing_type": "gold"}), config, provider)
    assert provider.requests == []


def test_untrusted_notes_are_fenced_as_data(brief: ProductBrief, config: MarketplaceConfig) -> None:
    provider = ScriptedProvider({})
    run(brief, config, provider, clock=fixed_clock)
    research = provider.requests[0]
    assert "untrusted data" in research.system
    assert "<research_notes>\n" + brief.research_notes + "\n</research_notes>" in research.user
    assert "costs" not in research.payload["brief"]  # the LLM never sees money inputs


@pytest.mark.parametrize(
    ("price", "low", "high", "expected"),
    [
        ("100", 50.0, 90.0, "above the highest"),
        ("40", 50.0, 90.0, "below the lowest"),
        ("60", 50.0, 90.0, None),
        ("60", None, None, None),
    ],
)
def test_market_note(
    price: str, low: float | None, high: float | None, expected: str | None
) -> None:
    research = MarketResearch(keywords=["x"], observed_price_low=low, observed_price_high=high)
    note = market_note(Decimal(price), research)
    if expected is None:
        assert note is None
    else:
        assert note is not None
        assert expected in note
