"""Tests for the reconciliation matching engine."""

from __future__ import annotations

from datetime import date

import pytest

from tieout import BreakReason, Record, Rule, RuleKind, Source, reconcile
from tieout.errors import InputError
from tieout.spec import AmountCondition, DateCondition, GroupSpec, ReferenceCondition


def _rec(
    record_id: str,
    source: str,
    amount: int,
    *,
    currency: str = "USD",
    value_date: date = date(2026, 11, 1),
    booking_date: date | None = None,
    external_id: str | None = None,
    description: str = "",
    **attrs: str,
) -> Record:
    return Record(
        id=record_id,
        source=source,
        amount=amount,
        currency=currency,
        value_date=value_date,
        booking_date=booking_date,
        external_id=external_id,
        description=description,
        attributes=attrs,
    )


def test_one_to_one_exact_tolerant_and_opposite_sign() -> None:
    left = Source.from_records(
        "ledger",
        [
            _rec("l1", "ledger", 10000),
            _rec("l2", "ledger", -5000),
            _rec("l3", "ledger", 2000),
        ],
    )
    right = Source.from_records(
        "bank",
        [
            _rec("b1", "bank", 10000),
            _rec("b2", "bank", 5025),
            _rec("b3", "bank", 2500),
        ],
    )
    rules = [
        Rule(id="exact", left="ledger", right="bank", priority=10),
        Rule(
            id="opposite-tolerant",
            left="ledger",
            right="bank",
            priority=20,
            amount=AmountCondition(tolerance=50, sign="opposite"),
        ),
    ]
    result = reconcile([left, right], rules)
    assert len(result.matches) == 2
    by_rule = {m.rule_id: m for m in result.matches}
    assert by_rule["exact"].left_ids == ("l1",)
    assert by_rule["exact"].right_ids == ("b1",)
    assert by_rule["exact"].difference == 0
    assert "exact" in "\n".join(by_rule["exact"].explanation)

    assert by_rule["opposite-tolerant"].left_ids == ("l2",)
    assert by_rule["opposite-tolerant"].right_ids == ("b2",)
    assert by_rule["opposite-tolerant"].difference == 25
    assert "opposite signs" in "\n".join(by_rule["opposite-tolerant"].explanation)

    breaks = {b.record_id: b for b in result.breaks}
    assert set(breaks) == {"l3", "b3"}
    assert breaks["l3"].reason is BreakReason.UNMATCHED
    assert breaks["b3"].reason is BreakReason.UNMATCHED


def test_date_windows_value_and_booking_date() -> None:
    left = Source.from_records(
        "a",
        [
            _rec("a1", "a", 1000, value_date=date(2026, 11, 1), booking_date=date(2026, 11, 2)),
            _rec("a2", "a", 2000, value_date=date(2026, 11, 1), booking_date=None),
        ],
    )
    right = Source.from_records(
        "b",
        [
            _rec("b1", "b", 1000, value_date=date(2026, 11, 10), booking_date=date(2026, 11, 3)),
            _rec("b2", "b", 2000, value_date=date(2026, 11, 1), booking_date=date(2026, 11, 1)),
        ],
    )
    rule = Rule(
        id="booking",
        left="a",
        right="b",
        date=DateCondition(min_days=0, max_days=2, field="booking_date"),
    )
    result = reconcile([left, right], [rule])
    assert [(m.left_ids, m.right_ids) for m in result.matches] == [(("a1",), ("b1",))]
    assert {b.record_id for b in result.breaks} == {"a2", "b2"}


def test_reference_conditions_normalization_and_min_length() -> None:
    left = Source.from_records(
        "invoices",
        [
            _rec("i1", "invoices", 5000, invoice_no="INV-0042"),
            _rec("i2", "invoices", 6000, invoice_no="7"),
            _rec("i3", "invoices", 7000, invoice_no=""),
        ],
    )
    right = Source.from_records(
        "bank",
        [
            _rec("r1", "bank", 5000, description="Payment for inv 0042 received"),
            _rec("r2", "bank", 6000, description="Payment #777"),
            _rec("r3", "bank", 7000, description="Payment"),
        ],
    )
    rule = Rule(
        id="ref",
        left="invoices",
        right="bank",
        references=(
            ReferenceCondition(left="invoice_no", right="description", op="left_in_right"),
        ),
    )
    result = reconcile([left, right], [rule])
    assert [(m.left_ids, m.right_ids) for m in result.matches] == [(("i1",), ("r1",))]
    assert {b.record_id for b in result.breaks} == {"i2", "r2", "i3", "r3"}


def test_mutual_best_ambiguity_and_cascading_rounds() -> None:
    # Ambiguous tie: one left matches two identical rights.
    amb_left = Source.from_records("l", [_rec("l1", "l", 1000, value_date=date(2026, 11, 1))])
    amb_right = Source.from_records(
        "r",
        [
            _rec("r1", "r", 1000, value_date=date(2026, 11, 1)),
            _rec("r2", "r", 1000, value_date=date(2026, 11, 1)),
        ],
    )
    amb_rule = Rule(id="r", left="l", right="r", date=DateCondition(min_days=0, max_days=2))
    amb_result = reconcile([amb_left, amb_right], [amb_rule])
    assert amb_result.matches == ()
    breaks = {b.record_id: b for b in amb_result.breaks}
    assert breaks["l1"].reason is BreakReason.AMBIGUOUS
    assert breaks["l1"].candidate_ids == ("r1", "r2")
    assert breaks["r1"].reason is BreakReason.AMBIGUOUS
    assert breaks["r2"].reason is BreakReason.AMBIGUOUS

    # Cascading mutual best:
    # L1 matches R1 (date diff 0) and R2 (date diff 1).
    # L2 matches only R2 (date diff 1).
    # Round 1 pairs (L1, R1); Round 2 frees R2 to pair with L2.
    cas_left = Source.from_records(
        "l",
        [
            _rec("l1", "l", 1000, value_date=date(2026, 11, 1)),
            _rec("l2", "l", 1000, value_date=date(2026, 11, 3)),
        ],
    )
    cas_right = Source.from_records(
        "r",
        [
            _rec("r1", "r", 1000, value_date=date(2026, 11, 1)),
            _rec("r2", "r", 1000, value_date=date(2026, 11, 2)),
        ],
    )
    # With min_days=-1, max_days=1:
    # l1 (Nov 1) fits r1 (Nov 1, dist 0) and r2 (Nov 2, dist 1).
    # l2 (Nov 3) fits r2 (Nov 2, dist 1) only; r1 (Nov 1) has gap -2 (outside [-1, 1]).
    # Round 1: l1 prefers r1 (dist 0) and r1 prefers l1 (only candidate) -> (l1, r1) pairs.
    # Round 2: r2 now has only l2, and l2 has only r2 -> (l2, r2) pairs.
    cas_rule = Rule(id="cascade", left="l", right="r", date=DateCondition(min_days=-1, max_days=1))
    cas_result = reconcile([cas_left, cas_right], [cas_rule])
    assert {(m.left_ids, m.right_ids) for m in cas_result.matches} == {
        (("l1",), ("r1",)),
        (("l2",), ("r2",)),
    }
    assert cas_result.breaks == ()


def test_deduplication_exact_conflict_and_no_external_id() -> None:
    left = Source.from_records(
        "a",
        [
            _rec("a1", "a", 1000, external_id="ext-1"),
            _rec("a2", "a", 1000, external_id="ext-1"),  # exact dup of a1
            _rec("a3", "a", 2000, external_id="ext-2"),
            _rec("a4", "a", 2500, external_id="ext-2"),  # conflict with a3
            _rec("a5", "a", 300, external_id=None),
            _rec("a6", "a", 300, external_id=None),  # no external_id: both kept!
        ],
    )
    right = Source.from_records(
        "b",
        [
            _rec("b1", "b", 1000),
            _rec("b2", "b", 2000),
            _rec("b3", "b", 300),
            _rec("b4", "b", 300),
        ],
    )
    rule = Rule(id="match", left="a", right="b")
    result = reconcile([left, right], [rule])
    breaks = {b.record_id: b for b in result.breaks}
    assert breaks["a2"].reason is BreakReason.DUPLICATE
    assert breaks["a2"].candidate_ids == ("a1",)
    assert breaks["a3"].reason is BreakReason.DUPLICATE_CONFLICT
    assert breaks["a4"].reason is BreakReason.DUPLICATE_CONFLICT
    assert breaks["b2"].reason is BreakReason.UNMATCHED
    # Note: a5/a6 vs b3/b4 are mutually tied (identical amounts and dates), so ambiguous!
    assert breaks["a5"].reason is BreakReason.AMBIGUOUS
    assert breaks["a6"].reason is BreakReason.AMBIGUOUS

    # If a5 and a6 have distinct dates while b3/b4 match those distinct dates, both match.
    left2 = Source.from_records(
        "a",
        [
            _rec("a5", "a", 300, value_date=date(2026, 11, 1)),
            _rec("a6", "a", 300, value_date=date(2026, 11, 5)),
        ],
    )
    right2 = Source.from_records(
        "b",
        [
            _rec("b3", "b", 300, value_date=date(2026, 11, 1)),
            _rec("b4", "b", 300, value_date=date(2026, 11, 5)),
        ],
    )
    r2 = reconcile([left2, right2], [Rule(id="d", left="a", right="b", date=DateCondition())])
    assert len(r2.matches) == 2
    assert r2.breaks == ()


def test_keyed_grouping_and_contested_group_breaks() -> None:
    stripe = Source.from_records(
        "stripe",
        [
            _rec("s1", "stripe", 1200, payout_id="po_1001"),
            _rec("s2", "stripe", 800, payout_id="po_1001"),
            _rec("s3", "stripe", 1500, payout_id="po_1002"),
            _rec("s4", "stripe", 500, payout_id="po_1002"),
        ],
    )
    bank = Source.from_records(
        "bank",
        [
            _rec("b1", "bank", 2000),
            _rec("b2", "bank", 2000),
        ],
    )
    rule = Rule(
        id="payouts",
        left="stripe",
        right="bank",
        kind=RuleKind.MANY_TO_ONE,
        group=GroupSpec(by="payout_id"),
    )
    # Without distinguishing references, both po_1001 and po_1002 equal 2000 -> all contested!
    contested = reconcile([stripe, bank], [rule])
    assert contested.matches == ()
    assert {b.record_id: b.reason for b in contested.breaks} == {
        "s1": BreakReason.AMBIGUOUS,
        "s2": BreakReason.AMBIGUOUS,
        "s3": BreakReason.AMBIGUOUS,
        "s4": BreakReason.AMBIGUOUS,
        "b1": BreakReason.AMBIGUOUS,
        "b2": BreakReason.AMBIGUOUS,
    }

    # With a reference condition, po_1001 matches b1 and po_1002 matches b2.
    bank_with_desc = Source.from_records(
        "bank",
        [
            _rec("b1", "bank", 2000, description="STRIPE po_1001"),
            _rec("b2", "bank", 2000, description="STRIPE po_1002"),
        ],
    )
    ref_rule = Rule(
        id="payouts-ref",
        left="stripe",
        right="bank",
        kind=RuleKind.MANY_TO_ONE,
        group=GroupSpec(by="payout_id"),
        references=(ReferenceCondition(left="payout_id", right="description", op="left_in_right"),),
    )
    matched = reconcile([stripe, bank_with_desc], [ref_rule])
    assert len(matched.matches) == 2
    assert matched.breaks == ()
    assert {m.left_ids for m in matched.matches} == {("s1", "s2"), ("s3", "s4")}


def test_one_to_many_keyed_grouping() -> None:
    bank = Source.from_records("bank", [_rec("b1", "bank", 2500)])
    orders = Source.from_records(
        "orders",
        [
            _rec("o1", "orders", 1000, batch="B1"),
            _rec("o2", "orders", 1500, batch="B1"),
        ],
    )
    rule = Rule(
        id="bank-to-orders",
        left="bank",
        right="orders",
        kind=RuleKind.ONE_TO_MANY,
        group=GroupSpec(by="batch"),
    )
    result = reconcile([bank, orders], [rule])
    assert len(result.matches) == 1
    assert result.matches[0].left_ids == ("b1",)
    assert result.matches[0].right_ids == ("o1", "o2")


def test_subset_search_unique_ambiguous_overlapping_and_exhausted() -> None:
    # 1. Unique subset found (many_to_one)
    invoices = Source.from_records(
        "inv",
        [
            _rec("i1", "inv", 1000),
            _rec("i2", "inv", 2500),
            _rec("i3", "inv", 4000),
        ],
    )
    payments = Source.from_records("pay", [_rec("p1", "pay", 3500)])
    rule = Rule(
        id="cash-app",
        left="inv",
        right="pay",
        kind=RuleKind.MANY_TO_ONE,
        date=DateCondition(min_days=0, max_days=5),
        group=GroupSpec(max_size=3, search_budget=1000),
    )
    res = reconcile([invoices, payments], [rule])
    assert len(res.matches) == 1
    assert res.matches[0].left_ids == ("i1", "i2")
    assert res.matches[0].right_ids == ("p1",)
    assert {b.record_id: b.reason for b in res.breaks} == {"i3": BreakReason.UNMATCHED}

    # 2. Multiple subsets sum to target -> AMBIGUOUS
    amb_inv = Source.from_records(
        "inv",
        [
            _rec("i1", "inv", 1000),
            _rec("i2", "inv", 2000),
            _rec("i3", "inv", 3000),
        ],
    )
    amb_pay = Source.from_records("pay", [_rec("p1", "pay", 3000)])
    amb_res = reconcile([amb_inv, amb_pay], [rule])
    assert amb_res.matches == ()
    assert {b.record_id: b.reason for b in amb_res.breaks}["p1"] is BreakReason.AMBIGUOUS

    # 3. Two targets claiming overlapping members -> both AMBIGUOUS
    ov_inv = Source.from_records(
        "inv",
        [
            _rec("i1", "inv", 1000),
            _rec("i2", "inv", 2000),
            _rec("i3", "inv", 4000),
        ],
    )
    ov_pay = Source.from_records(
        "pay",
        [
            _rec("p1", "pay", 3000),  # only {1000, 2000}
            _rec("p2", "pay", 6000),  # only {2000, 4000}
        ],
    )
    ov_res = reconcile([ov_inv, ov_pay], [rule])
    assert ov_res.matches == ()
    ov_breaks = {b.record_id: b.reason for b in ov_res.breaks}
    assert ov_breaks["p1"] is BreakReason.AMBIGUOUS
    assert ov_breaks["p2"] is BreakReason.AMBIGUOUS

    # 4. Exhausted search budget -> SEARCH_LIMIT
    tiny_rule = Rule(
        id="tiny-budget",
        left="inv",
        right="pay",
        kind=RuleKind.ONE_TO_MANY,
        date=DateCondition(min_days=0, max_days=5),
        group=GroupSpec(max_size=4, search_budget=1),
    )
    many_rights = Source.from_records(
        "pay",
        [_rec(f"p{i}", "pay", 100 + i) for i in range(10)],
    )
    one_left = Source.from_records("inv", [_rec("i1", "inv", 415)])
    lim_res = reconcile([one_left, many_rights], [tiny_rule])
    assert lim_res.matches == ()
    assert {b.record_id: b.reason for b in lim_res.breaks}["i1"] is BreakReason.SEARCH_LIMIT


def test_input_validation_errors() -> None:
    s1 = Source.from_records("a", [_rec("dup", "a", 100)])
    s2 = Source.from_records("b", [_rec("dup", "b", 100)])
    rule = Rule(id="r", left="a", right="b")

    with pytest.raises(InputError, match="ids must be unique across sources"):
        reconcile([s1, s2], [rule])

    with pytest.raises(InputError, match="source names must be unique"):
        reconcile([s1, s1], [rule])

    with pytest.raises(InputError, match="was not provided"):
        reconcile([s1], [rule])
