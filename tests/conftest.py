from decimal import Decimal

import pytest

from listing_agent.models import Attribute, ListingDraft
from listing_agent.pricing import CostInputs, FeeSchedule, FeeTier

@pytest.fixture
def classic() -> FeeSchedule:
    """12% commission, 6.75 fixed fee under 79, seller pays shipping from 79."""
    return FeeSchedule(
        name="classic",
        seller_pays_shipping_from=Decimal("79"),
        tiers=(
            FeeTier(up_to=Decimal("79"), percent=Decimal("0.12"), fixed_fee=Decimal("6.75")),
            FeeTier(percent=Decimal("0.12")),
        ),
    )


@pytest.fixture
def mug_costs() -> CostInputs:
    return CostInputs(
        unit_cost=Decimal("31"), shipping_cost=Decimal("21.90"), tax_rate=Decimal("0.06")
    )


@pytest.fixture
def good_listing() -> ListingDraft:
    return ListingDraft(
        titles=["Ceramic mug 350 ml handmade stoneware coffee mug"],
        category="Home > Kitchen > Mugs",
        attributes=[
            Attribute(name="Brand", value="Studio Example"),
            Attribute(name="Model", value="Daily 350"),
            Attribute(name="Material", value="Stoneware"),
            Attribute(name="Capacity", value="350 ml"),
            Attribute(name="Color", value="Sand"),
        ],
        photo_plan=["cover"],
        description="A handmade stoneware ceramic mug that holds 350 ml of coffee. " * 6,
        faq=[],
    )
