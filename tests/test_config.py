from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from listing_agent.config import MarketplaceConfig, load_brief, load_config
from listing_agent.models import ProductBrief


def test_example_config_loads(config: MarketplaceConfig) -> None:
    assert set(config.fees) == {"classic", "premium"}
    classic = config.schedule("classic")
    assert classic.name == "classic"
    assert classic.tier_for(Decimal("10")).fixed_fee == Decimal("6.25")
    assert classic.tier_for(Decimal("79")).fixed_fee == 0
    assert config.rules.max_title_length == 60


def test_unknown_listing_type(config: MarketplaceConfig) -> None:
    with pytest.raises(KeyError, match="unknown listing type 'gold'"):
        config.schedule("gold")


def test_example_brief_reads_notes_next_to_it(brief: ProductBrief) -> None:
    assert brief.costs.unit_cost == Decimal("31.00")
    assert "Seller A" in brief.research_notes


def test_config_without_fees_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.toml"
    path.write_text("[rules]\nmax_title_length = 40\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="fees"):
        load_config(path)


def test_brief_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "brief.toml"
    path.write_text(
        'name = "x"\ncategory = "c"\naudience = "a"\nfeatures = ["f"]\n'
        'target_margin = "0.1"\ncolour = "typo"\n[costs]\nunit_cost = "1"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="colour"):
        load_brief(path)
