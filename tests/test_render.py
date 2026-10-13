from decimal import Decimal

from listing_agent.config import MarketplaceConfig
from listing_agent.models import ProductBrief
from listing_agent.pipeline import run
from listing_agent.providers import FakeProvider
from listing_agent.render import listing_markdown, run_summary, table


def test_table_aligns_numbers_right_and_text_left() -> None:
    rendered = table(("name", "value"), [["a", "1.50"], ["long name", "350 ml"], ["b", "-2"]])
    assert rendered.splitlines() == [
        "| name      | value  |",
        "|-----------|--------|",
        "| a         |   1.50 |",
        "| long name | 350 ml |",
        "| b         |     -2 |",
    ]


def test_outputs_explain_missing_price(brief: ProductBrief, config: MarketplaceConfig) -> None:
    greedy = brief.model_copy(update={"target_margin": Decimal("0.85")})
    result = run(greedy, config, FakeProvider(), clock=lambda: 0.0)
    summary = run_summary(result)
    markdown = listing_markdown(result)
    assert "Pricing issue: a 85% margin is unreachable for 'classic'" in summary
    assert "Suggested:" not in markdown
    assert "> a 85% margin is unreachable" in markdown


def test_summary_shows_market_note(brief: ProductBrief, config: MarketplaceConfig) -> None:
    pricey = brief.model_copy(update={"target_margin": Decimal("0.45")})
    result = run(pricey, config, FakeProvider(), clock=lambda: 0.0)
    assert result.market_note is not None
    assert f"Market: {result.market_note}" in run_summary(result)
