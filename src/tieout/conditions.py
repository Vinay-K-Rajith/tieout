"""How a rule judges a candidate pairing, and how it explains the verdict.

A pairing is a group of left records against a group of right records. For a
one-to-one rule both groups hold one record; for grouping rules one side holds
several. Every check is defined over groups so all rule kinds share one meaning:

- amount: right total must equal the expected total (left total, or its negation
  for ``sign: opposite``) within the tolerance;
- date: *every* right date minus *every* left date must fall in the window;
- references: the condition must hold for *every* left/right record pair.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

from tieout.money import format_amount
from tieout.records import Record
from tieout.spec import ReferenceCondition, Rule

_NON_ALPHANUMERIC = re.compile(r"[^0-9A-Z]+")


@dataclass(frozen=True, slots=True)
class DateSpan:
    """Earliest and latest date of a group of records, as proleptic ordinals."""

    first: int
    last: int


def date_span(records: Sequence[Record], rule: Rule) -> DateSpan | None:
    """Date span used by the rule's date check, or None if any record lacks the date."""
    field = rule.date.field if rule.date else "value_date"
    ordinals = []
    for record in records:
        value = record.booking_date if field == "booking_date" else record.value_date
        if value is None:
            return None
        ordinals.append(value.toordinal())
    return DateSpan(min(ordinals), max(ordinals))


def dates_fit(rule: Rule, left: DateSpan, right: DateSpan) -> bool:
    if rule.date is None:
        return True
    return (
        right.first - left.last >= rule.date.min_days
        and right.last - left.first <= rule.date.max_days
    )


def date_distance(left: DateSpan, right: DateSpan) -> int:
    """Largest absolute gap between any left and any right date; used to rank candidates."""
    return max(abs(right.last - left.first), abs(left.last - right.first))


def expected_right_total(rule: Rule, left_total: int) -> int:
    return left_total if rule.amount.sign == "same" else -left_total


def amount_difference(rule: Rule, left_total: int, right_total: int) -> int:
    return right_total - expected_right_total(rule, left_total)


@lru_cache(maxsize=524288)
def normalize_reference(text: str) -> str:
    return _NON_ALPHANUMERIC.sub("", text.upper())


def reference_holds(condition: ReferenceCondition, left: Record, right: Record) -> bool:
    left_value = normalize_reference(left.value_of(condition.left))
    right_value = normalize_reference(right.value_of(condition.right))
    if not left_value or not right_value:
        return False  # an absent reference never counts as agreement
    if condition.op == "equals":
        return left_value == right_value
    needle, haystack = (
        (left_value, right_value) if condition.op == "left_in_right" else (right_value, left_value)
    )
    return len(needle) >= condition.min_length and needle in haystack


def references_hold(rule: Rule, lefts: Sequence[Record], rights: Sequence[Record]) -> bool:
    return all(
        reference_holds(condition, left, right)
        for condition in rule.references
        for left in lefts
        for right in rights
    )


def explain(
    rule: Rule,
    lefts: Sequence[Record],
    rights: Sequence[Record],
    *,
    grouping_note: str | None = None,
) -> tuple[str, ...]:
    """Plain-language account of why this pairing satisfies the rule."""
    currency = lefts[0].currency
    left_total = sum(record.amount for record in lefts)
    right_total = sum(record.amount for record in rights)
    difference = amount_difference(rule, left_total, right_total)

    lines = [
        f"rule {rule.id} ({rule.kind.value}): "
        f"{_count(len(lefts), 'left record')} against {_count(len(rights), 'right record')}",
        _explain_amount(rule, left_total, right_total, difference, currency),
    ]
    if rule.date is not None:
        lines.append(_explain_dates(rule, lefts, rights))
    lines.extend(
        f"reference: left.{condition.left} {_OP_WORDS[condition.op]} right.{condition.right}"
        for condition in rule.references
    )
    if grouping_note:
        lines.append(grouping_note)
    return tuple(lines)


_OP_WORDS = {
    "equals": "equals",
    "left_in_right": "is contained in",
    "right_in_left": "contains",
}


def _explain_amount(
    rule: Rule, left_total: int, right_total: int, difference: int, currency: str
) -> str:
    sign_note = " (opposite signs)" if rule.amount.sign == "opposite" else ""
    totals = (
        f"amount: left {format_amount(left_total, currency)} {currency}, "
        f"right {format_amount(right_total, currency)} {currency}{sign_note}"
    )
    if difference == 0:
        return f"{totals}, exact"
    return (
        f"{totals}, difference {format_amount(difference, currency)} {currency} "
        f"within tolerance {format_amount(rule.amount.tolerance, currency)}"
    )


def _explain_dates(rule: Rule, lefts: Sequence[Record], rights: Sequence[Record]) -> str:
    assert rule.date is not None
    left, right = date_span(lefts, rule), date_span(rights, rule)
    assert left is not None
    assert right is not None
    low, high = right.first - left.last, right.last - left.first
    gap = f"{low} days" if low == high else f"{low} to {high} days"
    return (
        f"date: right {rule.date.field} minus left is {gap} "
        f"(allowed {rule.date.min_days} to {rule.date.max_days})"
    )


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"
