"""Stripe itemized payout reconciliation and balance transaction CSV connector."""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator

from tieout.errors import ConnectorError, MoneyError
from tieout.money import normalize_currency, parse_amount
from tieout.records import Record

_DATE_COLUMNS = (
    "automatic_payout_effective_at",
    "effective_at",
    "available_on",
    "created",
)


class StripeOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    amount_field: Literal["net", "gross"] = "net"
    timezone: str = "UTC"
    exclude_categories: tuple[str, ...] = ("payout",)

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value


def parse_stripe(
    name: str, path: Path, raw_bytes: bytes, raw_options: dict[str, Any]
) -> list[Record]:
    """Parse a Stripe payout reconciliation or balance transaction CSV into records."""
    opts = StripeOptions.model_validate(raw_options)
    path_str = str(path)
    tz = ZoneInfo(opts.timezone)
    excluded = {cat.strip().lower() for cat in opts.exclude_categories}

    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ConnectorError(f"cannot decode Stripe CSV as UTF-8: {exc}", path=path_str) from exc

    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames is None:
        raise ConnectorError(
            "Stripe CSV is empty or missing header", path=path_str, location="header"
        )

    fields = set(reader.fieldnames)
    id_col = _pick_column(fields, ("balance_transaction_id", "id"))
    date_cols = [col for col in _DATE_COLUMNS if col in fields]
    category_col = _pick_column(fields, ("reporting_category", "type"))
    payout_col = _pick_column(fields, ("automatic_payout_id", "payout_id"))

    missing: list[str] = []
    if id_col is None:
        missing.append("balance_transaction_id/id")
    if "currency" not in fields:
        missing.append("currency")
    if opts.amount_field not in fields:
        missing.append(opts.amount_field)
    if not date_cols:
        missing.append("automatic_payout_effective_at/effective_at/available_on/created")
    if missing:
        raise ConnectorError(
            f"missing Stripe column(s): {', '.join(missing)}", path=path_str, location="header"
        )
    assert id_col is not None

    records: list[Record] = []
    seen_ids: set[str] = set()
    for row in reader:
        location = f"line {reader.line_num}"
        if None in row:
            raise ConnectorError(
                "row has more fields than the header", path=path_str, location=location
            )
        if all(not (value or "").strip() for value in row.values()):
            continue

        category = _cell(row, category_col) if category_col else ""
        if category and category.lower() in excluded:
            continue

        record = _parse_row(
            name=name,
            row=row,
            opts=opts,
            tz=tz,
            id_col=id_col,
            date_cols=date_cols,
            category=category,
            payout_col=payout_col,
            seen_ids=seen_ids,
            line_num=reader.line_num,
            path_str=path_str,
            location=location,
        )
        seen_ids.add(record.id)
        records.append(record)

    return records


def _parse_row(
    *,
    name: str,
    row: dict[str, str | None],
    opts: StripeOptions,
    tz: ZoneInfo,
    id_col: str,
    date_cols: list[str],
    category: str,
    payout_col: str | None,
    seen_ids: set[str],
    line_num: int,
    path_str: str,
    location: str,
) -> Record:
    tx_id = _cell(row, id_col)
    if not tx_id:
        raise ConnectorError(f"empty {id_col}", path=path_str, location=location)

    try:
        currency = normalize_currency(_cell(row, "currency"))
        amount = parse_amount(_cell(row, opts.amount_field), currency)
        value_date = _parse_stripe_date(row, date_cols, tz)
    except (MoneyError, ValueError) as exc:
        raise ConnectorError(str(exc), path=path_str, location=location) from exc

    description = _cell(row, "description") or category
    attributes: dict[str, str] = {}
    if payout_col and (payout_id := _cell(row, payout_col)):
        attributes["payout_id"] = payout_id
    if category:
        attributes["reporting_category"] = category
    for attr in ("gross", "fee", "net", "charge_id", "customer_id", "invoice_id"):
        if val := _cell(row, attr):
            attributes[attr] = val

    record_id = f"{name}:{tx_id}"
    if record_id in seen_ids:
        # Keep duplicate rows distinct so the engine can classify them via external_id.
        record_id = f"{record_id}@{line_num}"

    return Record(
        id=record_id,
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


def _parse_stripe_date(row: dict[str, str | None], date_cols: list[str], tz: ZoneInfo) -> date:
    for col in date_cols:
        raw = _cell(row, col)
        if not raw:
            continue
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            return date.fromisoformat(raw)
        normalized = raw[:-4].strip() if raw.endswith(" UTC") else raw
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(tz).date()
    raise ValueError(f"no non-empty date in {', '.join(date_cols)}")
