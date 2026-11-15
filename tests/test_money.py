"""Tests for exact money parsing, conversion, and formatting."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tieout.errors import MoneyError
from tieout.money import (
    format_amount,
    minor_unit_exponent,
    normalize_currency,
    parse_amount,
    to_minor_units,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("usd", "USD"),
        (" INR ", "INR"),
        ("Eur", "EUR"),
    ],
)
def test_normalize_currency_valid(raw: str, expected: str) -> None:
    assert normalize_currency(raw) == expected


@pytest.mark.parametrize("bad", ["", "US", "USDA", "US1", "123", "$US"])
def test_normalize_currency_invalid(bad: str) -> None:
    with pytest.raises(MoneyError):
        normalize_currency(bad)


@pytest.mark.parametrize(
    ("currency", "expected"),
    [
        ("JPY", 0),
        ("KRW", 0),
        ("USD", 2),
        ("INR", 2),
        ("EUR", 2),
        ("KWD", 3),
        ("BHD", 3),
        ("CLF", 4),
    ],
)
def test_minor_unit_exponent(currency: str, expected: int) -> None:
    assert minor_unit_exponent(currency) == expected


@pytest.mark.parametrize(
    ("value", "currency", "expected"),
    [
        (Decimal("100"), "JPY", 100),
        (Decimal("10.50"), "USD", 1050),
        (Decimal("-0.01"), "EUR", -1),
        (Decimal("1.234"), "KWD", 1234),
        (Decimal("2.5000"), "CLF", 25000),
    ],
)
def test_to_minor_units_exact(value: Decimal, currency: str, expected: int) -> None:
    assert to_minor_units(value, currency) == expected


@pytest.mark.parametrize(
    ("value", "currency"),
    [
        (Decimal("10.005"), "USD"),
        (Decimal("10.5"), "JPY"),
        (Decimal("1.2345"), "KWD"),
        (Decimal("1.23456"), "CLF"),
        (Decimal("NaN"), "USD"),
        (Decimal("Infinity"), "USD"),
        (Decimal("-Infinity"), "USD"),
    ],
)
def test_to_minor_units_rejects_rounding_and_non_finite(value: Decimal, currency: str) -> None:
    with pytest.raises(MoneyError):
        to_minor_units(value, currency)


@pytest.mark.parametrize(
    ("text", "currency", "expected"),
    [
        ("12.34", "USD", 1234),
        ("+12.34", "USD", 1234),
        ("-12.34", "USD", -1234),
        ("12.34-", "USD", -1234),
        ("(12.34)", "USD", -1234),
        ("( 12.34 )", "USD", -1234),
        (".50", "USD", 50),
        ("0.00", "USD", 0),
        ("-0.00", "USD", 0),
        ("1500", "JPY", 1500),
        ("-1500", "JPY", -1500),
        ("1.234", "KWD", 1234),
        ("1.2345", "CLF", 12345),
    ],
)
def test_parse_amount_standard(text: str, currency: str, expected: int) -> None:
    assert parse_amount(text, currency) == expected


def test_parse_amount_separators() -> None:
    assert parse_amount("1,234.56", "USD", thousands_separator=",") == 123456
    assert (
        parse_amount(
            "1.234,56",
            "EUR",
            decimal_separator=",",
            thousands_separator=".",
        )
        == 123456
    )
    assert (
        parse_amount(
            "(1.234,500)",
            "KWD",
            decimal_separator=",",
            thousands_separator=".",
        )
        == -1234500
    )


@pytest.mark.parametrize(
    ("text", "currency"),
    [
        ("", "USD"),
        ("   ", "USD"),
        ("$10.00", "USD"),
        ("USD 10.00", "USD"),
        ("1,000", "USD"),
        ("10.005", "USD"),
        ("10.5", "JPY"),
        ("--5.00", "USD"),
        ("-(5.00)", "USD"),
        ("-5.00-", "USD"),
        ("(+5.00)", "USD"),
        ("1e2", "USD"),
    ],
)
def test_parse_amount_invalid(text: str, currency: str) -> None:
    with pytest.raises(MoneyError):
        parse_amount(text, currency)


def test_parse_amount_rejects_identical_separators_and_stray_dot() -> None:
    with pytest.raises(MoneyError):
        parse_amount("10,50", "EUR", decimal_separator=",", thousands_separator=",")
    with pytest.raises(MoneyError):
        parse_amount("10.50", "EUR", decimal_separator=",")


@pytest.mark.parametrize(
    ("minor", "currency", "expected"),
    [
        (0, "USD", "0.00"),
        (5, "USD", "0.05"),
        (-123456, "USD", "-1234.56"),
        (1500, "JPY", "1500"),
        (-42, "JPY", "-42"),
        (5, "KWD", "0.005"),
        (-1234, "KWD", "-1.234"),
        (12345, "CLF", "1.2345"),
    ],
)
def test_format_amount(minor: int, currency: str, expected: str) -> None:
    assert format_amount(minor, currency) == expected
