"""Exact money handling.

An amount is an ``int`` in the currency's minor unit: cents for USD, yen for JPY,
fils for KWD. Text from source files is parsed through ``Decimal`` and must land
exactly on a minor unit. ``"10.005"`` in USD is an error, never a rounding, because
a silent rounding is exactly the kind of difference a reconciliation must surface.

Currencies are checked against the ISO 4217 list. An unknown code is an error, not
a currency with two decimals by assumption: a typo such as ``USX`` or a code with
no minor unit such as ``XAU`` would otherwise be scaled wrongly without a trace.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from tieout.errors import MoneyError

# ISO 4217 List One (active codes), as published by the maintenance agency (SIX)
# on 2026-09-17, keyed by minor-unit exponent. Codes whose minor unit is "N.A."
# (precious metals, SDR, test and fund codes: XAU, XDR, XTS, XXX...) are left out
# on purpose: they have no minor unit to count in.
_ISO_4217_ACTIVE = {
    0: """
        BIF CLP DJF GNF ISK JPY KMF KRW PYG RWF UGX UYI VND VUV XAF XOF XPF
    """,
    2: """
        AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BMD BND BOB BOV BRL BSD BTN BWP
        BYN BZD CAD CDF CHE CHF CHW CNY COP COU CRC CUP CVE CZK DKK DOP DZD EGP ERN ETB
        EUR FJD FKP GBP GEL GHS GIP GMD GTQ GYD HKD HNL HTG HUF IDR ILS INR IRR JMD KES
        KGS KHR KPW KYD KZT LAK LBP LKR LRD LSL MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR
        MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD PAB PEN PGK PHP PKR PLN QAR RON RSD
        RUB SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TOP
        TRY TTD TWD TZS UAH USD USN UYU UZS VED VES WST XAD XCD XCG YER ZAR ZMW ZWG
    """,
    3: """
        BHD IQD JOD KWD LYD OMR TND
    """,
    4: """
        CLF UYW
    """,
}

# Withdrawn since 2021 but still found in historical statements and ledgers.
_ISO_4217_RECENTLY_WITHDRAWN = {
    "ANG": 2,  # Netherlands Antillean guilder, replaced by XCG (2025)
    "CUC": 2,  # Cuban convertible peso (2021)
    "HRK": 2,  # Croatian kuna, replaced by EUR (2023)
    "SLL": 2,  # Sierra Leonean leone, redenominated to SLE (2023)
    "ZWL": 2,  # Zimbabwe dollar, replaced by ZWG (2024)
}

_EXPONENTS: dict[str, int] = {
    **{code: exponent for exponent, codes in _ISO_4217_ACTIVE.items() for code in codes.split()},
    **_ISO_4217_RECENTLY_WITHDRAWN,
}

_CURRENCY_PATTERN = re.compile(r"[A-Z]{3}")
# ASCII digits only: ``\d`` would also accept Arabic-Indic or full-width digits.
_PLAIN_NUMBER = re.compile(r"[0-9]+(\.[0-9]+)?|\.[0-9]+")
_DIGITS = re.compile(r"[0-9]+")


def normalize_currency(code: str) -> str:
    """Return the upper-case ISO 4217 code, or raise if it isn't a known currency."""
    normalized = code.strip().upper()
    if not _CURRENCY_PATTERN.fullmatch(normalized):
        raise MoneyError(f"invalid currency code {code!r}: expected three letters, e.g. 'USD'")
    if normalized not in _EXPONENTS:
        raise MoneyError(
            f"unknown currency {normalized!r}: not an ISO 4217 currency with a minor unit "
            "(use tieout.money.register_currency for private or crypto codes)"
        )
    return normalized


def minor_unit_exponent(currency: str) -> int:
    """Number of decimal places in the currency's minor unit (2 for USD, 0 for JPY)."""
    try:
        return _EXPONENTS[currency]
    except KeyError:
        raise MoneyError(f"unknown currency {currency!r}") from None


def register_currency(code: str, exponent: int) -> None:
    """Declare a non-ISO currency (a private token, a stablecoin) and its decimal places.

    Registering an ISO code with a different exponent is refused: that would
    silently rescale every amount in that currency.
    """
    if not _CURRENCY_PATTERN.fullmatch(code):
        raise MoneyError(f"invalid currency code {code!r}: expected three upper-case letters")
    if isinstance(exponent, bool) or not isinstance(exponent, int) or not 0 <= exponent <= 18:
        raise MoneyError(f"invalid exponent {exponent!r} for {code}: expected 0 to 18")
    existing = _EXPONENTS.get(code)
    if existing is not None and existing != exponent:
        raise MoneyError(f"{code} already has {existing} decimal places; refusing {exponent}")
    _EXPONENTS[code] = exponent


def to_minor_units(value: Decimal, currency: str) -> int:
    """Convert a major-unit decimal to minor units, refusing to round."""
    if not value.is_finite():
        raise MoneyError(f"amount {value} is not a finite number")
    places = minor_unit_exponent(currency)
    scaled = value.scaleb(places)
    if scaled != scaled.to_integral_value():
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
    error, because it could mean 1234 or 1.234. With a thousands separator the
    digit grouping must be well formed (``1,234,567`` or Indian ``12,34,567``), so
    a decimal comma read under the wrong settings (``"1,23"``) fails instead of
    becoming 123.
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

    integer, has_decimal, fraction = body.partition(decimal_separator)
    if has_decimal and decimal_separator in fraction:
        raise MoneyError(f"amount {text!r} has more than one decimal separator")
    if thousands_separator:
        if thousands_separator in fraction:
            raise MoneyError(f"thousands separator after the decimal separator in {text!r}")
        integer = _ungroup(integer, thousands_separator, text)
    if "." in integer or "." in fraction:
        raise MoneyError(f"unexpected '.' in amount {text!r}")

    number = f"{integer}.{fraction}" if has_decimal else integer
    if not _PLAIN_NUMBER.fullmatch(number):
        raise MoneyError(f"cannot parse amount {text!r}")

    try:
        value = Decimal(number)
    except InvalidOperation as exc:  # pragma: no cover - the pattern above guards this
        raise MoneyError(f"cannot parse amount {text!r}") from exc

    minor = to_minor_units(value, currency)
    return -minor if negative else minor


def _ungroup(integer: str, separator: str, text: str) -> str:
    """Remove thousands separators after checking they sit where grouping puts them."""
    if separator not in integer:
        return integer
    head, *rest = integer.split(separator)
    if not all(_DIGITS.fullmatch(group) for group in (head, *rest)):
        raise MoneyError(f"malformed digit grouping in amount {text!r}")
    western = 1 <= len(head) <= 3 and all(len(group) == 3 for group in rest)
    indian = (
        len(rest) >= 2
        and 1 <= len(head) <= 2
        and len(rest[-1]) == 3
        and all(len(group) == 2 for group in rest[:-1])
    )
    if not (western or indian):
        raise MoneyError(
            f"malformed digit grouping in amount {text!r}: check the decimal and "
            "thousands separator settings"
        )
    return "".join((head, *rest))


def format_amount(minor: int, currency: str) -> str:
    """Render minor units as a plain decimal string: ``-123456`` USD -> ``"-1234.56"``."""
    exponent = minor_unit_exponent(currency)
    sign = "-" if minor < 0 else ""
    digits = str(abs(minor))
    if exponent == 0:
        return f"{sign}{digits}"
    digits = digits.rjust(exponent + 1, "0")
    return f"{sign}{digits[:-exponent]}.{digits[-exponent:]}"
