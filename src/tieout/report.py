"""Serialize reconciliation results as JSON, CSV, or human-readable summaries."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

from tieout.money import format_amount
from tieout.result import Break, Match, Result

__all__ = [
    "format_summary",
    "to_dict",
    "to_json",
    "write_breaks_csv",
    "write_matches_csv",
]

_SUMMARY_BREAK_LIMIT = 20

# Cells starting with these are run as formulas by spreadsheet apps (OWASP "CSV
# injection"). Bank descriptions and references are untrusted, so text cells that
# start with one are prefixed with a single quote. Amount columns are written by
# tieout itself and are never touched.
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r", "\n", "\uff1d", "\uff0b", "\uff0d", "\uff20")


def _safe(text: str) -> str:
    return "'" + text if text.startswith(_FORMULA_TRIGGERS) else text


def _date(value: date | None) -> str:
    return value.isoformat() if value is not None else ""


def to_dict(result: Result) -> dict[str, Any]:
    """Convert a `Result` into a plain JSON-serializable dictionary."""
    summary = result.summary()
    return {
        "run": {
            "run_id": result.run.run_id,
            "engine_version": result.run.engine_version,
            "spec_digest": result.run.spec_digest,
            "input_digests": dict(result.run.input_digests),
            "content_digests": dict(result.run.content_digests),
        },
        "summary": {
            "records": summary.records,
            "matches": summary.matches,
            "matched_records": summary.matched_records,
            "breaks": summary.breaks,
            "match_rate": round(summary.match_rate, 6),
            "excluded_rows": summary.excluded,
            "breaks_by_reason": {
                reason.value: count for reason, count in summary.breaks_by_reason.items()
            },
        },
        "matches": [_match_dict(result, match) for match in result.matches],
        "breaks": [_break_dict(result, item) for item in result.breaks],
        "excluded": {
            source: [{"location": row.location, "reason": row.reason} for row in rows]
            for source, rows in sorted(result.excluded.items())
        },
    }


def to_json(result: Result, *, indent: int = 2) -> str:
    """Render a `Result` as formatted JSON."""
    return json.dumps(to_dict(result), indent=indent, ensure_ascii=False)


def write_matches_csv(result: Result, path: str | Path) -> None:
    """Write matched record groups and their explanations to a CSV file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "match_id",
                "rule_id",
                "left_ids",
                "right_ids",
                "currency",
                "left_amount",
                "right_amount",
                "difference",
                "explanation",
            ]
        )
        for match in result.matches:
            left_total, right_total = _match_totals(result, match)
            writer.writerow(
                [
                    match.id,
                    _safe(match.rule_id),
                    _safe(";".join(match.left_ids)),
                    _safe(";".join(match.right_ids)),
                    match.currency,
                    format_amount(left_total, match.currency),
                    format_amount(right_total, match.currency),
                    format_amount(match.difference, match.currency),
                    _safe(" | ".join(match.explanation)),
                ]
            )


def write_breaks_csv(result: Result, path: str | Path) -> None:
    """Write every unmatched, duplicate, or ambiguous record to a CSV file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "record_id",
                "source",
                "reason",
                "rule_id",
                "currency",
                "amount",
                "value_date",
                "booking_date",
                "external_id",
                "location",
                "candidate_ids",
                "detail",
            ]
        )
        for item in result.breaks:
            record = result.records[item.record_id]
            writer.writerow(
                [
                    _safe(item.record_id),
                    _safe(record.source),
                    item.reason.value,
                    _safe(item.rule_id or ""),
                    record.currency,
                    format_amount(record.amount, record.currency),
                    _date(record.value_date),
                    _date(record.booking_date),
                    _safe(record.external_id or ""),
                    _safe(record.location or ""),
                    _safe(";".join(item.candidate_ids)),
                    _safe(item.detail),
                ]
            )


def format_summary(result: Result) -> str:
    """Render a concise plain-text report for terminal display."""
    summary = result.summary()
    lines = [
        f"Run {result.run.run_id} (tieout {result.run.engine_version})",
        (
            f"{summary.matched_records}/{summary.records} records matched "
            f"({summary.match_rate:.1%}) across {summary.matches} match(es); "
            f"{summary.breaks} break(s)"
        ),
    ]
    if summary.excluded:
        per_reason: dict[str, int] = {}
        for source, rows in sorted(result.excluded.items()):
            for row in rows:
                key = f"{source}: {row.reason}"
                per_reason[key] = per_reason.get(key, 0) + 1
        details = "; ".join(f"{key} x{count}" for key, count in per_reason.items())
        lines.append(f"{summary.excluded} input row(s) excluded by source filters ({details})")
    if result.matches:
        lines.append("")
        lines.append("Matches:")
        for match in result.matches[:_SUMMARY_BREAK_LIMIT]:
            left_total, right_total = _match_totals(result, match)
            diff = (
                ""
                if match.difference == 0
                else f" (diff {format_amount(match.difference, match.currency)})"
            )
            lines.append(
                f"  [{match.rule_id}] {', '.join(match.left_ids)} <-> {', '.join(match.right_ids)} "
                f"({format_amount(left_total, match.currency)} / "
                f"{format_amount(right_total, match.currency)} {match.currency}){diff}"
            )
        if len(result.matches) > _SUMMARY_BREAK_LIMIT:
            lines.append(f"  ... and {len(result.matches) - _SUMMARY_BREAK_LIMIT} more match(es)")

    if result.breaks:
        lines.append("")
        lines.append("Breaks by reason:")
        for reason, count in summary.breaks_by_reason.items():
            lines.append(f"  {reason.value}: {count}")
        lines.append("Breaks:")
        for item in result.breaks[:_SUMMARY_BREAK_LIMIT]:
            rec = result.records[item.record_id]
            amt = format_amount(rec.amount, rec.currency)
            where = f", {rec.location}" if rec.location else ""
            lines.append(
                f"  [{item.reason.value}] {item.record_id} "
                f"({_date(rec.value_date or rec.booking_date)} {amt} {rec.currency}{where}): "
                f"{item.detail}"
            )
        if len(result.breaks) > _SUMMARY_BREAK_LIMIT:
            lines.append(f"  ... and {len(result.breaks) - _SUMMARY_BREAK_LIMIT} more break(s)")

    return "\n".join(lines)


def _match_totals(result: Result, match: Match) -> tuple[int, int]:
    left_total = sum(result.records[rid].amount for rid in match.left_ids)
    right_total = sum(result.records[rid].amount for rid in match.right_ids)
    return left_total, right_total


def _match_dict(result: Result, match: Match) -> dict[str, Any]:
    left_total, right_total = _match_totals(result, match)
    return {
        "id": match.id,
        "rule_id": match.rule_id,
        "left_ids": list(match.left_ids),
        "right_ids": list(match.right_ids),
        "currency": match.currency,
        "left_amount": format_amount(left_total, match.currency),
        "right_amount": format_amount(right_total, match.currency),
        "difference": format_amount(match.difference, match.currency),
        "difference_minor": match.difference,
        "explanation": list(match.explanation),
    }


def _break_dict(result: Result, item: Break) -> dict[str, Any]:
    rec = result.records[item.record_id]
    return {
        "record_id": item.record_id,
        "source": rec.source,
        "reason": item.reason.value,
        "rule_id": item.rule_id,
        "currency": rec.currency,
        "amount": format_amount(rec.amount, rec.currency),
        "amount_minor": rec.amount,
        "value_date": _date(rec.value_date) or None,
        "booking_date": _date(rec.booking_date) or None,
        "external_id": rec.external_id,
        "location": rec.location,
        "candidate_ids": list(item.candidate_ids),
        "detail": item.detail,
    }
