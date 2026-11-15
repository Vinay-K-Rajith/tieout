"""Generic CSV connector driven by column-mapping options."""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tieout.errors import ConnectorError, MoneyError
from tieout.money import normalize_currency, parse_amount
from tieout.records import BUILTIN_FIELDS, Record


class CsvOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id_column: str | None = None
    external_id_column: str | None = None
    amount_column: str | None = None
    inflow_column: str | None = None
    outflow_column: str | None = None
    direction_column: str | None = None
    debit_values: tuple[str, ...] = ("debit", "dr", "dbit", "d", "out")
    credit_values: tuple[str, ...] = ("credit", "cr", "crdt", "c", "in")
    currency: str | None = None
    currency_column: str | None = None
    value_date_column: str = "date"
    booking_date_column: str | None = None
    date_format: str = "%Y-%m-%d"
    description_column: str | None = None
    attribute_columns: tuple[str, ...] = ()
    delimiter: str = Field(default=",", min_length=1, max_length=1)
    decimal_separator: str = Field(default=".", min_length=1, max_length=1)
    thousands_separator: str | None = Field(default=None, min_length=1, max_length=1)
    encoding: str = "utf-8-sig"

    @model_validator(mode="after")
    def _validate(self) -> Self:
        has_single = self.amount_column is not None
        has_split = self.inflow_column is not None or self.outflow_column is not None
        if has_single and has_split:
            raise ValueError(
                "specify either amount_column or inflow_column/outflow_column, not both"
            )
        if not has_single and (self.inflow_column is None or self.outflow_column is None):
            raise ValueError("specify amount_column or both inflow_column and outflow_column")
        if not has_single and self.direction_column is not None:
            raise ValueError("direction_column only applies when amount_column is used")
        if self.currency is None and self.currency_column is None:
            raise ValueError("specify currency or currency_column")
        if self.currency is not None:
            try:
                normalized = normalize_currency(self.currency)
            except MoneyError as exc:
                raise ValueError(str(exc)) from exc
            object.__setattr__(self, "currency", normalized)
        if self.decimal_separator == self.thousands_separator:
            raise ValueError("decimal_separator and thousands_separator must differ")
        reserved = BUILTIN_FIELDS & set(self.attribute_columns)
        if reserved:
            raise ValueError(f"attribute_columns cannot include reserved names {sorted(reserved)}")
        return self


def parse_csv(name: str, path: Path, raw_bytes: bytes, raw_options: dict[str, Any]) -> list[Record]:
    """Parse a CSV file into canonical records according to ``CsvOptions``."""
    opts = CsvOptions.model_validate(raw_options)
    path_str = str(path)

    try:
        text = raw_bytes.decode(opts.encoding)
    except (LookupError, UnicodeDecodeError) as exc:
        raise ConnectorError(
            f"cannot decode CSV with encoding {opts.encoding!r}: {exc}", path=path_str
        ) from exc

    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=opts.delimiter)
    if reader.fieldnames is None:
        raise ConnectorError(
            "CSV file is empty or missing a header row", path=path_str, location="header"
        )

    _check_columns(opts, reader.fieldnames, path_str)

    debit_tokens = {token.strip().lower() for token in opts.debit_values}
    credit_tokens = {token.strip().lower() for token in opts.credit_values}

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

        record = _parse_row(
            name=name,
            row=row,
            opts=opts,
            line_num=reader.line_num,
            debit_tokens=debit_tokens,
            credit_tokens=credit_tokens,
            path_str=path_str,
            location=location,
        )
        if record.id in seen_ids:
            raise ConnectorError(
                f"duplicate record id {record.id!r}", path=path_str, location=location
            )
        seen_ids.add(record.id)
        records.append(record)

    return records


def _check_columns(opts: CsvOptions, fieldnames: Sequence[str], path_str: str) -> None:
    present = set(fieldnames)
    required = [opts.value_date_column]
    for col in (
        opts.id_column,
        opts.external_id_column,
        opts.amount_column,
        opts.inflow_column,
        opts.outflow_column,
        opts.direction_column,
        opts.currency_column,
        opts.booking_date_column,
        opts.description_column,
        *opts.attribute_columns,
    ):
        if col is not None:
            required.append(col)

    missing = sorted({col for col in required if col not in present})
    if missing:
        raise ConnectorError(f"missing column(s) {missing}", path=path_str, location="header")


def _parse_row(
    *,
    name: str,
    row: dict[str, str | None],
    opts: CsvOptions,
    line_num: int,
    debit_tokens: set[str],
    credit_tokens: set[str],
    path_str: str,
    location: str,
) -> Record:
    try:
        currency = _read_currency(row, opts)
        amount = _read_amount(row, opts, currency, debit_tokens, credit_tokens)
        value_date = _read_date(_cell(row, opts.value_date_column), opts.date_format, "value date")
        booking_date = (
            _read_optional_date(
                _cell(row, opts.booking_date_column), opts.date_format, "booking date"
            )
            if opts.booking_date_column is not None
            else None
        )
    except (MoneyError, ValueError) as exc:
        raise ConnectorError(str(exc), path=path_str, location=location) from exc

    if opts.id_column is not None:
        raw_id = _cell(row, opts.id_column)
        if not raw_id:
            raise ConnectorError(
                f"empty id column {opts.id_column!r}", path=path_str, location=location
            )
        record_id = f"{name}:{raw_id}"
    else:
        record_id = f"{name}:{line_num}"

    external_id = _cell(row, opts.external_id_column) if opts.external_id_column else ""
    description = _cell(row, opts.description_column) if opts.description_column else ""
    attributes = {col: val for col in opts.attribute_columns if (val := _cell(row, col))}

    return Record(
        id=record_id,
        source=name,
        amount=amount,
        currency=currency,
        value_date=value_date,
        booking_date=booking_date,
        external_id=external_id or None,
        description=description,
        attributes=attributes,
        location=location,
    )


def _cell(row: dict[str, str | None], column: str) -> str:
    return (row.get(column) or "").strip()


def _read_currency(row: dict[str, str | None], opts: CsvOptions) -> str:
    if opts.currency_column is not None:
        raw = _cell(row, opts.currency_column)
        if raw:
            return normalize_currency(raw)
    if opts.currency is not None:
        return opts.currency
    raise ValueError(f"empty currency column {opts.currency_column!r}")


def _read_amount(
    row: dict[str, str | None],
    opts: CsvOptions,
    currency: str,
    debit_tokens: set[str],
    credit_tokens: set[str],
) -> int:
    if opts.amount_column is not None:
        parsed = parse_amount(
            _cell(row, opts.amount_column),
            currency,
            decimal_separator=opts.decimal_separator,
            thousands_separator=opts.thousands_separator,
        )
        if opts.direction_column is None:
            return parsed
        direction = _cell(row, opts.direction_column).lower()
        if direction in debit_tokens:
            return -abs(parsed)
        if direction in credit_tokens:
            if parsed < 0:
                raw_amt = _cell(row, opts.amount_column)
                raise ValueError(f"negative amount {raw_amt!r} conflicts with credit direction")
            return parsed
        raise ValueError(
            f"unrecognized direction {direction!r} in column {opts.direction_column!r}"
        )

    assert opts.inflow_column is not None
    assert opts.outflow_column is not None
    inflow_text = _cell(row, opts.inflow_column)
    outflow_text = _cell(row, opts.outflow_column)
    if not inflow_text and not outflow_text:
        raise ValueError("both inflow and outflow columns are empty")
    inflow = (
        parse_amount(
            inflow_text,
            currency,
            decimal_separator=opts.decimal_separator,
            thousands_separator=opts.thousands_separator,
        )
        if inflow_text
        else 0
    )
    outflow = (
        parse_amount(
            outflow_text,
            currency,
            decimal_separator=opts.decimal_separator,
            thousands_separator=opts.thousands_separator,
        )
        if outflow_text
        else 0
    )
    return inflow - outflow


def _read_date(raw: str, date_format: str, label: str) -> date:
    if not raw:
        raise ValueError(f"missing {label}")
    if date_format == "%Y-%m-%d":
        return date.fromisoformat(raw)
    return datetime.strptime(raw, date_format).date()  # noqa: DTZ007


def _read_optional_date(raw: str, date_format: str, label: str) -> date | None:
    if not raw:
        return None
    return _read_date(raw, date_format, label)
