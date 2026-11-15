"""Exact money handling.

An amount is an ``int`` in the currency's minor unit: cents for USD, yen for JPY,
fils for KWD. Text from source files is parsed through ``Decimal`` and must land
exactly on a minor unit. ``"10.005"`` in USD is an error, never a rounding, because
a silent rounding is exactly the kind of difference a reconciliation must surface.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from tieout.errors import MoneyError

# ISO 4217 currencies whose minor unit is not two decimal places.
_MINOR_UNIT_EXPONENTS: dict[str, int] = {
    **dict.fromkeys(
        [
            "BIF",
            "CLP",
            "DJF",
            "GNF",
            "ISK",
            "JPY",
            "KMF",
            "KRW",
            "PYG",
            "RWF",
            "UGX",
            "UYI",
            "VND",
            "VUV",
            "XAF",
            "XOF",
            "XPF",
        ],
        0,
    ),
    **dict.fromkeys(["BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"], 3),
    **dict.fromkeys(["CLF", "UYW"], 4),
}
_DEFAULT_EXPONENT = 2

_CURRENCY_PATTERN = re.compile(r"[A-Z]{3}")
_PLAIN_NUMBER = re.compile(r"\d+(\.\d+)?|\.\d+")


def normalize_currency(code: str) -> str:
    """Return the upper-case ISO 4217 code, or raise if it isn't three letters."""
    normalized = code.strip().upper()
    if not _CURRENCY_PATTERN.fullmatch(normalized):
        raise MoneyError(f"invalid currency code {code!r}: expected three letters, e.g. 'USD'")
    return normalized


def minor_unit_exponent(currency: str) -> int:
    """Number of decimal places in the currency's minor unit (2 for USD, 0 for JPY)."""
    return _MINOR_UNIT_EXPONENTS.get(currency, _DEFAULT_EXPONENT)


def to_minor_units(value: Decimal, currency: str) -> int:
    """Convert a major-unit decimal to minor units, refusing to round."""
    if not value.is_finite():
        raise MoneyError(f"amount {value} is not a finite number")
    scaled = value.scaleb(minor_unit_exponent(currency))
    if scaled != scaled.to_integral_value():
        places = minor_unit_exponent(currency)
        raise MoneyError(
            f"amount {value} has more precision than {currency} allows ({places} decimal places)"
        )
    return int(scaled)


def parse_amount(
    text: str,
    currency: str,
    *,
    decimal_separator: str = ".",
    thousands_separator: str | None = None,
) -> int:
    """Parse a human-formatted amount into signed minor units.

    Accepts a leading ``+``/``-``, a trailing ``-`` (common in bank exports) and
    accounting parentheses ``(12.34)``. Rejects currency symbols, exponents and
    anything ambiguous: ``"1,234"`` without a declared thousands separator is an
    error, because it could mean 1234 or 1.234.
    """
    if decimal_separator == thousands_separator:
        raise MoneyError("decimal and thousands separators must differ")

    body = text.strip()
    if not body:
        raise MoneyError("amount is empty")

    sign_markers = 0
    negative = False
    if body.startswith("(") and body.endswith(")"):
        sign_markers, negative, body = sign_markers + 1, True, body[1:-1].strip()
    if body.endswith("-"):
        sign_markers, negative, body = sign_markers + 1, True, body[:-1].strip()
    if body[:1] in ("+", "-"):
        sign_markers, negative, body = sign_markers + 1, negative or body[0] == "-", body[1:]
        body = body.strip()
    if sign_markers > 1:
        raise MoneyError(f"amount {text!r} has more than one sign marker")

    if thousands_separator:
        body = body.replace(thousands_separator, "")
    if decimal_separator != ".":
        if "." in body:
            raise MoneyError(f"unexpected '.' in amount {text!r}")
        body = body.replace(decimal_separator, ".")

    if not _PLAIN_NUMBER.fullmatch(body):
        raise MoneyError(f"cannot parse amount {text!r}")

    try:
        value = Decimal(body)
    except InvalidOperation as exc:  # pragma: no cover - the pattern above guards this
        raise MoneyError(f"cannot parse amount {text!r}") from exc

    minor = to_minor_units(value, currency)
    return -minor if negative else minor


def format_amount(minor: int, currency: str) -> str:
    """Render minor units as a plain decimal string: ``-123456`` USD -> ``"-1234.56"``."""
    exponent = minor_unit_exponent(currency)
    sign = "-" if minor < 0 else ""
    digits = str(abs(minor))
    if exponent == 0:
        return f"{sign}{digits}"
    digits = digits.rjust(exponent + 1, "0")
    return f"{sign}{digits[:-exponent]}.{digits[-exponent:]}"
