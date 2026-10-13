"""Decimal helpers for money. Prices never touch floats."""

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def ceil_to_cent(value: Decimal) -> Decimal:
    """Round up to the next cent, so a computed minimum is never undercut."""
    return value.quantize(CENT, rounding=ROUND_CEILING)


def to_cents(value: Decimal) -> Decimal:
    """Round half up to cents, for display and reporting."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def charm(value: Decimal, ending: Decimal) -> Decimal:
    """Smallest price >= value whose cents equal `ending` (e.g. 0.90 turns 12.10 into 12.90).

    `ending` must be in [0, 1). An ending of 0 rounds up to a whole number.
    """
    if not Decimal(0) <= ending < Decimal(1):
        raise ValueError(f"charm ending must be in [0, 1), got {ending}")
    candidate = value.to_integral_value(rounding=ROUND_FLOOR) + ending
    if candidate < value:
        candidate += 1
    return candidate.quantize(CENT)


def format_money(value: Decimal) -> str:
    return f"{to_cents(value):,.2f}"


def format_percent(value: Decimal) -> str:
    return f"{(value * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"
