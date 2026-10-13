from decimal import Decimal

import pytest

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
