from decimal import Decimal

import pytest

from listing_agent.money import ceil_to_cent, charm, format_money, format_percent, to_cents


def test_ceil_to_cent_never_rounds_down() -> None:
    assert ceil_to_cent(Decimal("10.001")) == Decimal("10.01")
    assert ceil_to_cent(Decimal("10.00")) == Decimal("10.00")


def test_to_cents_rounds_half_up() -> None:
    assert to_cents(Decimal("1.005")) == Decimal("1.01")
    assert to_cents(Decimal("1.004")) == Decimal("1.00")


@pytest.mark.parametrize(
    ("value", "ending", "expected"),
    [
        ("12.10", "0.90", "12.90"),
        ("12.90", "0.90", "12.90"),
        ("12.91", "0.90", "13.90"),
        ("12.01", "0", "13.00"),
        ("12.00", "0", "12.00"),
        ("0.50", "0.99", "0.99"),
    ],
)
def test_charm(value: str, ending: str, expected: str) -> None:
    assert charm(Decimal(value), Decimal(ending)) == Decimal(expected)


@pytest.mark.parametrize("ending", ["1", "-0.1", "1.5"])
def test_charm_rejects_endings_outside_unit_interval(ending: str) -> None:
    with pytest.raises(ValueError, match="charm ending"):
        charm(Decimal("10"), Decimal(ending))


def test_formatting() -> None:
    assert format_money(Decimal("1234.5")) == "1,234.50"
    assert format_percent(Decimal("0.2049")) == "20.5%"
