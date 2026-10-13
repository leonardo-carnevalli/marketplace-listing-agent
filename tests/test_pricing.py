from decimal import Decimal

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from pydantic import ValidationError

from listing_agent.money import CENT
from listing_agent.pricing import (
    CostInputs,
    FeeSchedule,
    FeeTier,
    UnreachableMarginError,
    breakdown,
    breakeven_price,
    breakeven_units,
    margin_ladder,
    price_for_margin,
    quote,
    scenarios,
)

D = Decimal


def flat(percent: str, fixed: str = "0") -> FeeSchedule:
    return FeeSchedule(name="flat", tiers=(FeeTier(percent=D(percent), fixed_fee=D(fixed)),))


# ---------------------------------------------------------------- breakdown


def test_breakdown_matches_hand_calculation(classic: FeeSchedule, mug_costs: CostInputs) -> None:
    b = breakdown(D("60.90"), mug_costs, classic)
    assert b.commission == D("7.3080")
    assert b.fixed_fee == D("6.75")
    assert b.shipping == 0  # below the threshold the buyer pays shipping
    assert b.tax == D("3.6540")
    assert b.profit == D("60.90") - D("7.308") - D("6.75") - D("3.654") - D("31")
    assert b.margin == b.profit / D("60.90")


def test_seller_pays_shipping_from_threshold_inclusive(
    classic: FeeSchedule, mug_costs: CostInputs
) -> None:
    assert breakdown(D("78.99"), mug_costs, classic).shipping == 0
    at_threshold = breakdown(D("79.00"), mug_costs, classic)
    assert at_threshold.shipping == D("21.90")
    assert at_threshold.fixed_fee == 0


def test_free_shipping_threshold_creates_a_dead_zone(
    classic: FeeSchedule, mug_costs: CostInputs
) -> None:
    """Raising the price past the threshold lowers profit: shipping costs more than the fee."""
    below = breakdown(D("78.90"), mug_costs, classic)
    above = breakdown(D("80.90"), mug_costs, classic)
    assert above.profit < below.profit


def test_no_threshold_means_seller_always_pays_shipping() -> None:
    costs = CostInputs(unit_cost=D("10"), shipping_cost=D("5"))
    assert breakdown(D("20"), costs, flat("0.1")).shipping == D("5")


@pytest.mark.parametrize("price", ["0", "-1"])
def test_breakdown_rejects_non_positive_price(price: str, classic: FeeSchedule) -> None:
    with pytest.raises(ValueError, match="positive"):
        breakdown(D(price), CostInputs(unit_cost=D("1")), classic)


# ---------------------------------------------------------------- price for margin


def test_price_for_margin_closed_form() -> None:
    # p * (1 - 0.10 - 0.20) = 14 -> p = 20
    costs = CostInputs(unit_cost=D("14"))
    assert price_for_margin(D("0.20"), costs, flat("0.10")) == D("20.00")


def test_price_for_margin_skips_the_dead_zone(classic: FeeSchedule) -> None:
    # Under 79: 0.62 p = 51.75 -> 83.47, outside the band. From 79: 0.62 p = 66.90 -> 107.91.
    costs = CostInputs(unit_cost=D("45"), shipping_cost=D("21.90"), tax_rate=D("0.06"))
    price = price_for_margin(D("0.20"), costs, classic)
    assert price == D("107.91")
    assert breakdown(price, costs, classic).meets(D("0.20"))


def test_price_for_margin_prefers_the_lower_band_when_it_fits(
    classic: FeeSchedule, mug_costs: CostInputs
) -> None:
    assert price_for_margin(D("0.20"), mug_costs, classic) == D("60.89")


def test_boundary_price_counts_when_a_band_starts_cheaper() -> None:
    """A fixed fee that vanishes at 50 makes 50.00 the cheapest price for the target."""
    schedule = FeeSchedule(
        name="banded",
        tiers=(
            FeeTier(up_to=D("50"), percent=D("0.10"), fixed_fee=D("20")),
            FeeTier(percent=D("0.10")),
        ),
    )
    costs = CostInputs(unit_cost=D("30"))
    assert price_for_margin(D("0.2"), costs, schedule) == D("50.00")


def test_unreachable_margin_raises() -> None:
    with pytest.raises(UnreachableMarginError, match="unreachable"):
        price_for_margin(D("0.5"), CostInputs(unit_cost=D("1"), tax_rate=D("0.3")), flat("0.25"))


def test_zero_cost_and_exact_keep_is_reachable_at_one_cent() -> None:
    # keep = 1 - 0.5 - 0 - 0.5 = 0 and no fixed costs: any price gives exactly 50%.
    assert price_for_margin(D("0.5"), CostInputs(unit_cost=D("0")), flat("0.5")) == CENT


@pytest.mark.parametrize("target", ["-0.01", "1", "1.5"])
def test_target_margin_out_of_range(target: str) -> None:
    with pytest.raises(ValueError, match="target margin"):
        price_for_margin(D(target), CostInputs(unit_cost=D("1")), flat("0.1"))


def test_breakeven_price_is_the_first_cent_without_loss(
    classic: FeeSchedule, mug_costs: CostInputs
) -> None:
    price = breakeven_price(mug_costs, classic)
    assert breakdown(price, mug_costs, classic).profit >= 0
    assert breakdown(price - CENT, mug_costs, classic).profit < 0


# ---------------------------------------------------------------- quote, scenarios, ladder


def test_quote_recommended_uses_charm_ending(classic: FeeSchedule, mug_costs: CostInputs) -> None:
    q = quote(mug_costs, classic, D("0.20"))
    assert q.target.price == D("60.89")
    assert q.recommended.price == D("60.90")
    assert q.breakeven.price < q.target.price <= q.recommended.price


def test_quote_without_charm(classic: FeeSchedule, mug_costs: CostInputs) -> None:
    q = quote(mug_costs, classic, D("0.20"), charm_ending=None)
    assert q.recommended == q.target


def test_charm_never_lands_in_the_dead_zone(classic: FeeSchedule) -> None:
    # The exact target sits just below 79, where 80.90 would lose margin to shipping.
    costs = CostInputs(unit_cost=D("41.80"), shipping_cost=D("21.90"), tax_rate=D("0.06"))
    q = quote(costs, classic, D("0.20"))
    assert q.target.price < D("79")
    assert q.recommended.price == D("78.90")
    assert q.recommended.meets(D("0.20"))


def test_scenarios_cover_every_type_and_price(classic: FeeSchedule, mug_costs: CostInputs) -> None:
    rows = scenarios(mug_costs, [classic, flat("0.17")], [D("50"), D("80")])
    assert [(r.listing_type, r.price) for r in rows] == [
        ("classic", D("50")),
        ("classic", D("80")),
        ("flat", D("50")),
        ("flat", D("80")),
    ]


def test_margin_ladder_marks_unreachable_targets() -> None:
    ladder = margin_ladder(CostInputs(unit_cost=D("10")), flat("0.5"), [D("0"), D("0.6")])
    assert ladder[D("0")] == D("20.00")
    assert ladder[D("0.6")] is None


# ---------------------------------------------------------------- breakeven units


def test_breakeven_units_rounds_up(classic: FeeSchedule, mug_costs: CostInputs) -> None:
    sale = breakdown(D("60.90"), mug_costs, classic)  # profit 12.188
    assert breakeven_units(D("100"), sale) == 9
    assert breakeven_units(D("0"), sale) == 0


def test_breakeven_units_none_when_each_sale_loses(
    classic: FeeSchedule, mug_costs: CostInputs
) -> None:
    assert breakeven_units(D("100"), breakdown(D("40"), mug_costs, classic)) is None


def test_breakeven_units_rejects_negative_costs(
    classic: FeeSchedule, mug_costs: CostInputs
) -> None:
    with pytest.raises(ValueError, match="negative"):
        breakeven_units(D("-1"), breakdown(D("60"), mug_costs, classic))


# ---------------------------------------------------------------- schedule validation


@pytest.mark.parametrize(
    ("tiers", "message"),
    [
        ([{"up_to": "10", "percent": "0.1"}], "last tier"),
        ([{"percent": "0.1"}, {"percent": "0.1"}], "only the last tier"),
        (
            [
                {"up_to": "20", "percent": "0.1"},
                {"up_to": "10", "percent": "0.1"},
                {"percent": "0"},
            ],
            "strictly increasing",
        ),
        ([{"percent": "1.2"}], "less than 1"),
    ],
)
def test_invalid_schedules(tiers: list[dict[str, str]], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        FeeSchedule.model_validate({"name": "x", "tiers": tiers})


# ---------------------------------------------------------------- properties

money = st.decimals(min_value=0, max_value=500, places=2)
rates = st.decimals(min_value=0, max_value=D("0.30"), places=3)


@st.composite
def schedules(draw: st.DrawFn) -> FeeSchedule:
    cuts = sorted(draw(st.sets(st.integers(min_value=5, max_value=300), max_size=3)))
    tiers = [
        FeeTier(up_to=D(cut), percent=draw(rates), fixed_fee=draw(money.map(lambda m: m / 50)))
        for cut in cuts
    ]
    tiers.append(FeeTier(percent=draw(rates)))
    threshold = draw(st.none() | st.integers(min_value=10, max_value=200).map(D))
    return FeeSchedule(name="random", tiers=tuple(tiers), seller_pays_shipping_from=threshold)


costs_strategy = st.builds(
    CostInputs,
    unit_cost=money,
    shipping_cost=money.map(lambda m: m / 10),
    tax_rate=st.decimals(min_value=0, max_value=D("0.25"), places=3),
)
targets = st.decimals(min_value=0, max_value=D("0.6"), places=2)


@given(costs_strategy, schedules(), targets)
def test_price_for_margin_meets_target_and_is_the_lowest_cent(
    costs: CostInputs, schedule: FeeSchedule, target: Decimal
) -> None:
    try:
        price = price_for_margin(target, costs, schedule)
    except UnreachableMarginError:
        # Unreachable must mean the last band (the asymptote) never gets there either.
        far = breakdown(D("1e9"), costs, schedule)
        assert not far.meets(target)
        return
    assert price == price.quantize(CENT)
    assert breakdown(price, costs, schedule).meets(target)
    if price > CENT:
        assert not breakdown(price - CENT, costs, schedule).meets(target)


@given(costs_strategy, schedules(), targets, st.integers(min_value=1, max_value=99_999))
def test_no_cheaper_price_meets_the_target(
    costs: CostInputs, schedule: FeeSchedule, target: Decimal, cents: int
) -> None:
    try:
        price = price_for_margin(target, costs, schedule)
    except UnreachableMarginError:
        return
    lower = D(cents) * CENT
    assume(lower < price)
    assert not breakdown(lower, costs, schedule).meets(target)


@given(costs_strategy, schedules(), targets)
def test_recommended_price_ends_in_90_and_meets_target(
    costs: CostInputs, schedule: FeeSchedule, target: Decimal
) -> None:
    try:
        q = quote(costs, schedule, target)
    except UnreachableMarginError:
        return
    assert q.recommended.price % 1 == D("0.90")
    assert q.recommended.price >= q.target.price
    assert q.recommended.meets(target)
    assert q.breakeven.price <= q.target.price
