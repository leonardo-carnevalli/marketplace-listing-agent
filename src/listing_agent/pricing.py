"""Pure pricing math for a marketplace listing.

Model of one sale at price ``p``::

    commission = p * tier.percent
    fixed_fee  = tier.fixed_fee            (per unit, depends on the price band)
    shipping   = costs.shipping_cost       (only when the seller pays it at this price)
    tax        = p * costs.tax_rate        (tax on gross revenue)
    profit     = p - commission - fixed_fee - shipping - tax - costs.unit_cost
    margin     = profit / p

Fee tiers and the free-shipping threshold make profit piecewise linear in ``p``.
Between two breakpoints every term is either constant or proportional to ``p``, so
the margin is ``keep - constant / p`` with ``keep = 1 - percent - tax_rate``. That is
non-decreasing in ``p`` inside a segment, and the lowest price that reaches a target
margin has a closed form per segment. No search, no floats, no iteration limits.
"""

import itertools
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from listing_agent.money import CENT, ceil_to_cent, charm

ZERO = Decimal(0)
ONE = Decimal(1)

Rate = Decimal
Snap = Callable[[Decimal], Decimal]


class UnreachableMarginError(ValueError):
    """No price reaches the target margin under this fee schedule."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FeeTier(_Frozen):
    """Fees for prices below ``up_to`` (exclusive). ``None`` means no upper bound."""

    up_to: Decimal | None = Field(default=None, gt=0)
    percent: Rate = Field(ge=0, lt=1)
    fixed_fee: Decimal = Field(default=ZERO, ge=0)


class FeeSchedule(_Frozen):
    """Fees of one listing type, for example a classic or a premium listing."""

    name: str = Field(min_length=1)
    tiers: tuple[FeeTier, ...] = Field(min_length=1)
    seller_pays_shipping_from: Decimal | None = Field(default=None, gt=0)
    """At or above this price the seller pays shipping. ``None``: always pays it."""

    @model_validator(mode="after")
    def _tiers_are_ordered(self) -> Self:
        *bounded, last = self.tiers
        if last.up_to is not None:
            raise ValueError("the last tier must have no upper bound (omit up_to)")
        limits = [tier.up_to for tier in bounded if tier.up_to is not None]
        if len(limits) != len(bounded):
            raise ValueError("only the last tier may omit up_to")
        if any(later <= earlier for earlier, later in itertools.pairwise(limits)):
            raise ValueError("tier up_to values must be strictly increasing")
        return self

    def tier_for(self, price: Decimal) -> FeeTier:
        return next(t for t in self.tiers if t.up_to is None or price < t.up_to)

    def seller_pays_shipping(self, price: Decimal) -> bool:
        threshold = self.seller_pays_shipping_from
        return threshold is None or price >= threshold


class CostInputs(_Frozen):
    unit_cost: Decimal = Field(ge=0)
    shipping_cost: Decimal = Field(default=ZERO, ge=0)
    tax_rate: Rate = Field(default=ZERO, ge=0, lt=1)


class PriceBreakdown(_Frozen):
    listing_type: str
    price: Decimal
    commission: Decimal
    fixed_fee: Decimal
    shipping: Decimal
    tax: Decimal
    unit_cost: Decimal
    profit: Decimal
    margin: Decimal

    def meets(self, target_margin: Rate) -> bool:
        """Exact check (no division): profit >= price * target."""
        return self.profit >= self.price * target_margin


class PriceQuote(_Frozen):
    listing_type: str
    target_margin: Rate
    breakeven: PriceBreakdown
    target: PriceBreakdown
    recommended: PriceBreakdown


def breakdown(price: Decimal, costs: CostInputs, schedule: FeeSchedule) -> PriceBreakdown:
    if price <= 0:
        raise ValueError(f"price must be positive, got {price}")
    tier = schedule.tier_for(price)
    commission = price * tier.percent
    shipping = costs.shipping_cost if schedule.seller_pays_shipping(price) else ZERO
    tax = price * costs.tax_rate
    profit = price - commission - tier.fixed_fee - shipping - tax - costs.unit_cost
    return PriceBreakdown(
        listing_type=schedule.name,
        price=price,
        commission=commission,
        fixed_fee=tier.fixed_fee,
        shipping=shipping,
        tax=tax,
        unit_cost=costs.unit_cost,
        profit=profit,
        margin=profit / price,
    )


@dataclass(frozen=True)
class _Segment:
    """A price range where percent, fixed fee and shipping are all constant."""

    low: Decimal
    high: Decimal | None
    percent: Rate
    constant_cost: Decimal


def _segments(costs: CostInputs, schedule: FeeSchedule) -> list[_Segment]:
    cuts = {t.up_to for t in schedule.tiers if t.up_to is not None}
    if schedule.seller_pays_shipping_from is not None:
        cuts.add(schedule.seller_pays_shipping_from)
    lows = [ZERO, *sorted(cuts)]
    highs: list[Decimal | None] = [*sorted(cuts), None]
    segments = []
    for low, high in zip(lows, highs, strict=True):
        tier = schedule.tier_for(low)
        shipping = costs.shipping_cost if schedule.seller_pays_shipping(low) else ZERO
        segments.append(
            _Segment(low, high, tier.percent, tier.fixed_fee + shipping + costs.unit_cost)
        )
    return segments


def _lowest_in_segment(
    segment: _Segment, costs: CostInputs, target: Rate, snap: Snap
) -> Decimal | None:
    # margin(p) >= target  <=>  keep * p >= constant_cost, with keep = 1 - percent - tax - target
    keep = ONE - segment.percent - costs.tax_rate - target
    if keep > 0:
        start = segment.constant_cost / keep
    elif keep == 0 and segment.constant_cost == 0:
        start = ZERO
    else:
        return None
    candidate = snap(max(start, segment.low, CENT))
    if segment.high is not None and candidate >= segment.high:
        return None
    return candidate


def price_for_margin(
    target_margin: Rate,
    costs: CostInputs,
    schedule: FeeSchedule,
    snap: Snap = ceil_to_cent,
) -> Decimal:
    """Lowest price (after ``snap``) whose margin is at least ``target_margin``.

    ``snap`` must round up (never down). The default rounds up to the cent.
    Raises :class:`UnreachableMarginError` if fees and taxes leave no room for it.
    """
    if not ZERO <= target_margin < ONE:
        raise ValueError(f"target margin must be in [0, 1), got {target_margin}")
    candidates = [
        price
        for segment in _segments(costs, schedule)
        if (price := _lowest_in_segment(segment, costs, target_margin, snap)) is not None
    ]
    if not candidates:
        raise UnreachableMarginError(
            f"a {target_margin:.0%} margin is unreachable for '{schedule.name}': "
            "fees plus taxes take too large a share of the price"
        )
    return min(candidates)


def breakeven_price(costs: CostInputs, schedule: FeeSchedule) -> Decimal:
    """Lowest price, to the cent, where profit is zero or positive."""
    return price_for_margin(ZERO, costs, schedule)


def breakeven_units(fixed_costs: Decimal, sale: PriceBreakdown) -> int | None:
    """Units to sell at this price to cover monthly fixed costs (ads, tools, rent).

    ``None`` when each sale loses money, so no volume ever breaks even.
    """
    if fixed_costs < 0:
        raise ValueError("fixed costs cannot be negative")
    if fixed_costs == 0:
        return 0
    if sale.profit <= 0:
        return None
    return math.ceil(fixed_costs / sale.profit)


def quote(
    costs: CostInputs,
    schedule: FeeSchedule,
    target_margin: Rate,
    charm_ending: Decimal | None = Decimal("0.90"),
) -> PriceQuote:
    """Breakeven, exact target price and a rounded "shelf" price that still meets the target."""
    target_price = price_for_margin(target_margin, costs, schedule)
    if charm_ending is None:
        recommended_price = target_price
    else:
        snap = partial(_charm_up, ending=charm_ending)
        recommended_price = price_for_margin(target_margin, costs, schedule, snap)
    return PriceQuote(
        listing_type=schedule.name,
        target_margin=target_margin,
        breakeven=breakdown(breakeven_price(costs, schedule), costs, schedule),
        target=breakdown(target_price, costs, schedule),
        recommended=breakdown(recommended_price, costs, schedule),
    )


def _charm_up(value: Decimal, ending: Decimal) -> Decimal:
    return charm(ceil_to_cent(value), ending)


def scenarios(
    costs: CostInputs,
    schedules: Iterable[FeeSchedule],
    prices: Sequence[Decimal],
) -> list[PriceBreakdown]:
    """One breakdown per (listing type, price), to compare options side by side."""
    return [breakdown(p, costs, s) for s in schedules for p in prices]


def margin_ladder(
    costs: CostInputs, schedule: FeeSchedule, targets: Sequence[Rate]
) -> Mapping[Rate, Decimal | None]:
    """Required price for each target margin; ``None`` where it is unreachable."""
    ladder: dict[Rate, Decimal | None] = {}
    for target in targets:
        try:
            ladder[target] = price_for_margin(target, costs, schedule)
        except UnreachableMarginError:
            ladder[target] = None
    return ladder
