"""Stripe itemized payout reconciliation and balance transaction CSV connector.

Dates. Stripe report columns come in pairs: ``created`` is written in the time zone
the report was *requested* in (the Dashboard uses the account's time zone), while
``created_utc`` is always UTC. A naive ``created`` value therefore cannot be
assumed to be UTC. The connector:

- picks one date column for the whole file (``date_column``, or the first of
  ``automatic_payout_effective_at``, ``effective_at``, ``available_on``,
  ``created`` present), and never falls back to another column row by row;
- reads its ``_utc`` twin when the file has one;
- otherwise reads the column in ``report_timezone``, and refuses to guess it when
  the calendar date is wanted in a different ``timezone``;
- converts the instant to ``timezone`` (the merchant's or bank's) and takes the
  calendar date there, so a payout at 02:00 UTC is dated the previous day in
  New York.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator

from tieout.connectors.csv import check_header, iter_rows
from tieout.errors import ConnectorError, MoneyError
from tieout.money import normalize_currency, parse_amount
from tieout.records import BUILTIN_FIELDS, Exclusion, Record, assign_stable_ids

DATE_COLUMNS = (
    "automatic_payout_effective_at",
    "effective_at",
    "available_on",
    "created",
)
_ATTRIBUTE_COLUMNS = ("gross", "fee", "net", "charge_id", "customer_id", "invoice_id")


def _check_timezone(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone {value!r}") from exc
    return value


class StripeOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    amount_field: Literal["net", "gross"] = "net"
    timezone: str = "UTC"
    """Time zone in which a record's calendar date is taken."""
    report_timezone: str | None = None
    """Time zone the report was requested in; needed only for non-``_utc`` columns."""
    date_column: (
        Literal["automatic_payout_effective_at", "effective_at", "available_on", "created"] | None
    ) = None
    exclude_categories: tuple[str, ...] = ("payout",)

    _valid_timezone = field_validator("timezone", "report_timezone")(_check_timezone)

    def fields(self) -> frozenset[str]:
        """Names rules can refer to on records from this source."""
        return frozenset(
            {*BUILTIN_FIELDS, "value_date", "payout_id", "reporting_category", *_ATTRIBUTE_COLUMNS}
        )


def parse_stripe(
    name: str, path: Path, raw_bytes: bytes, raw_options: dict[str, Any]
) -> tuple[list[Record], list[Exclusion]]:
    """Parse a Stripe payout reconciliation or balance transaction CSV into records."""
    opts = StripeOptions.model_validate(raw_options)
    path_str = str(path)
    excluded_categories = {cat.strip().lower() for cat in opts.exclude_categories}

    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ConnectorError(f"cannot decode Stripe CSV as UTF-8: {exc}", path=path_str) from exc

    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    try:
        fieldnames = reader.fieldnames
    except csv.Error as exc:
        raise ConnectorError(f"malformed CSV: {exc}", path=path_str, location="header") from exc
    if fieldnames is None:
        raise ConnectorError(
            "Stripe CSV is empty or missing header", path=path_str, location="header"
        )
    check_header(fieldnames, path_str)

    fields = set(fieldnames)
    id_col = _pick_column(fields, ("balance_transaction_id", "id"))
    category_col = _pick_column(fields, ("reporting_category", "type"))
    payout_col = _pick_column(fields, ("automatic_payout_id", "payout_id"))

    missing: list[str] = []
    if id_col is None:
        missing.append("balance_transaction_id/id")
    if "currency" not in fields:
        missing.append("currency")
    if opts.amount_field not in fields:
        missing.append(opts.amount_field)
    if missing:
        raise ConnectorError(
            f"missing Stripe column(s): {', '.join(missing)}", path=path_str, location="header"
        )
    assert id_col is not None
    date_col, source_tz = _date_column(opts, fields, path_str)
    target_tz = ZoneInfo(opts.timezone)

    records: list[Record] = []
    excluded: list[Exclusion] = []
    for row in iter_rows(reader, path_str):
        location = f"line {reader.line_num}"
        if all(not (value or "").strip() for value in row.values()):
            excluded.append(Exclusion(location, "blank row"))
            continue

        category = _cell(row, category_col) if category_col else ""
        if category and category.lower() in excluded_categories:
            excluded.append(Exclusion(location, f"reporting category {category}"))
            continue

        records.append(
            _parse_row(
                name=name,
                row=row,
                opts=opts,
                id_col=id_col,
                date_col=date_col,
                source_tz=source_tz,
                target_tz=target_tz,
                category=category,
                payout_col=payout_col,
                path_str=path_str,
                location=location,
            )
        )

    return assign_stable_ids(name, records, lambda record: record.external_id), excluded


def _date_column(opts: StripeOptions, fields: set[str], path_str: str) -> tuple[str, tzinfo]:
    """The one column dates are read from, and the time zone its naive values are in."""
    if opts.date_column is not None:
        base: str = opts.date_column
        if base not in fields and f"{base}_utc" not in fields:
            raise ConnectorError(
                f"date_column {base!r} (or {base}_utc) is not in the file",
                path=path_str,
                location="header",
            )
    else:
        present = [col for col in DATE_COLUMNS if col in fields or f"{col}_utc" in fields]
        if not present:
            raise ConnectorError(
                "missing Stripe date column: one of "
                + ", ".join(f"{col}[_utc]" for col in DATE_COLUMNS),
                path=path_str,
                location="header",
            )
        base = present[0]

    if f"{base}_utc" in fields:
        return f"{base}_utc", UTC
    if opts.report_timezone is not None:
        return base, ZoneInfo(opts.report_timezone)
    if opts.timezone == "UTC":
        # Reporting API default; the date is taken as printed either way.
        return base, UTC
    raise ConnectorError(
        f"column {base!r} is written in the time zone the report was requested in, which "
        f"this file does not record; set report_timezone, or export {base}_utc",
        path=path_str,
        location="header",
    )


def _parse_row(
    *,
    name: str,
    row: dict[str, str | None],
    opts: StripeOptions,
    id_col: str,
    date_col: str,
    source_tz: tzinfo,
    target_tz: tzinfo,
    category: str,
    payout_col: str | None,
    path_str: str,
    location: str,
) -> Record:
    tx_id = _cell(row, id_col)
    if not tx_id:
        raise ConnectorError(f"empty {id_col}", path=path_str, location=location)

    try:
        currency = normalize_currency(_cell(row, "currency"))
        amount = parse_amount(_cell(row, opts.amount_field), currency)
        value_date = _parse_stripe_date(_cell(row, date_col), date_col, source_tz, target_tz)
    except (MoneyError, ValueError) as exc:
        raise ConnectorError(str(exc), path=path_str, location=location) from exc

    description = _cell(row, "description") or category
    attributes: dict[str, str] = {}
    if payout_col and (payout_id := _cell(row, payout_col)):
        attributes["payout_id"] = payout_id
    if category:
        attributes["reporting_category"] = category
    for attr in _ATTRIBUTE_COLUMNS:
        if val := _cell(row, attr):
            attributes[attr] = val

    return Record(
        id=f"{name}:{tx_id}",
        source=name,
        amount=amount,
        currency=currency,
        value_date=value_date,
        external_id=tx_id,
        description=description,
        attributes=attributes,
        location=location,
    )


def _pick_column(fields: set[str], candidates: tuple[str, ...]) -> str | None:
    for col in candidates:
        if col in fields:
            return col
    return None


def _cell(row: dict[str, str | None], column: str | None) -> str:
    if column is None:
        return ""
    return (row.get(column) or "").strip()


def _parse_stripe_date(raw: str, column: str, source_tz: tzinfo, target_tz: tzinfo) -> date:
    if not raw:
        raise ValueError(f"empty {column}; set date_column to choose a column every row fills in")
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        day = date.fromisoformat(raw)
        noon = datetime(day.year, day.month, day.day, 12, tzinfo=source_tz)
        if noon.utcoffset() != noon.astimezone(target_tz).utcoffset():
            raise ValueError(
                f"{column} {raw!r} is a bare date in one time zone and cannot be "
                "re-dated in another; export a timestamp column instead"
            )
        return day
    normalized = raw[:-4].strip() if raw.endswith(" UTC") else raw
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=source_tz)
    return parsed.astimezone(target_tz).date()
