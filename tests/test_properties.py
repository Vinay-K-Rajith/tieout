"""Property-based tests expressing the reconciliation engine's invariants."""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from hypothesis import given, settings
from hypothesis import strategies as st

from tieout import BreakReason, Record, Rule, RuleKind, Source, reconcile
from tieout.conditions import expected_right_total
from tieout.connectors import read_source
from tieout.money import _EXPONENTS, format_amount, parse_amount
from tieout.result import Result
from tieout.spec import AmountCondition, DateCondition, GroupSpec

_BASE_DATE = date(2026, 11, 1)


@st.composite
def _record_list(
    draw: st.DrawFn,
    *,
    source: str,
    prefix: str,
    min_size: int = 0,
    max_size: int = 12,
    allow_zero: bool = True,
) -> list[Record]:
    count = draw(st.integers(min_value=min_size, max_value=max_size))
    records: list[Record] = []
    min_amt = 0 if allow_zero else 100
    for index in range(count):
        amount = draw(st.integers(min_value=min_amt, max_value=2000))
        day_offset = draw(st.integers(min_value=0, max_value=4))
        ext_id = draw(st.one_of(st.none(), st.sampled_from(["e1", "e2", "e3", f"u{index}"])))
        records.append(
            Record(
                id=f"{prefix}{index}",
                source=source,
                amount=amount,
                currency="USD",
                value_date=_BASE_DATE + timedelta(days=day_offset),
                external_id=ext_id,
            )
        )
    return records


@given(
    left_records=_record_list(source="left", prefix="L"),
    right_records=_record_list(source="right", prefix="R"),
    tolerance=st.integers(min_value=0, max_value=50),
    sign=st.sampled_from(["same", "opposite"]),
    max_days=st.integers(min_value=0, max_value=3),
)
@settings(max_examples=80)
def test_conservation_and_amount_invariants(
    left_records: list[Record],
    right_records: list[Record],
    tolerance: int,
    sign: Literal["same", "opposite"],
    max_days: int,
) -> None:
    if sign == "opposite":
        right_records = [
            Record(
                id=r.id,
                source=r.source,
                amount=-r.amount,
                currency=r.currency,
                value_date=r.value_date,
                external_id=r.external_id,
            )
            for r in right_records
        ]

    rule = Rule(
        id="r1",
        left="left",
        right="right",
        kind=RuleKind.ONE_TO_ONE,
        amount=AmountCondition(tolerance=tolerance, sign=sign),
        date=DateCondition(min_days=-1, max_days=max_days),
    )
    left = Source.from_records("left", left_records)
    right = Source.from_records("right", right_records)
    result = reconcile([left, right], [rule])

    all_ids = {r.id for r in (*left_records, *right_records)}
    matched_ids = [rid for m in result.matches for rid in m.record_ids]
    break_ids = [b.record_id for b in result.breaks]

    # No record appears twice, and every record appears somewhere.
    assert len(matched_ids) == len(set(matched_ids))
    assert len(break_ids) == len(set(break_ids))
    assert set(matched_ids).isdisjoint(break_ids)
    assert set(matched_ids) | set(break_ids) == all_ids

    # Summary totals agree with matches and breaks.
    summary = result.summary()
    assert summary.records == len(all_ids)
    assert summary.matched_records == len(matched_ids)
    assert summary.breaks == len(break_ids)

    # Amount invariant holds on every match.
    for match in result.matches:
        left_total = sum(result.records[rid].amount for rid in match.left_ids)
        right_total = sum(result.records[rid].amount for rid in match.right_ids)
        expected = expected_right_total(rule, left_total)
        assert right_total - expected == match.difference
        assert abs(match.difference) <= tolerance


@given(
    left_records=_record_list(source="left", prefix="L", min_size=1, max_size=7, allow_zero=False),
    right_records=_record_list(
        source="right", prefix="R", min_size=1, max_size=4, allow_zero=False
    ),
)
@settings(max_examples=50)
def test_subset_search_conservation_and_determinism(
    left_records: list[Record],
    right_records: list[Record],
) -> None:
    # Ensure external_ids don't collide on ordering changes by clearing them here.
    clean_left = [
        Record(
            id=r.id,
            source=r.source,
            amount=r.amount,
            currency=r.currency,
            value_date=r.value_date,
        )
        for r in left_records
    ]
    clean_right = [
        Record(
            id=r.id,
            source=r.source,
            amount=r.amount,
            currency=r.currency,
            value_date=r.value_date,
        )
        for r in right_records
    ]
    rule = Rule(
        id="m2o",
        left="left",
        right="right",
        kind=RuleKind.MANY_TO_ONE,
        date=DateCondition(min_days=0, max_days=4),
        group=GroupSpec(max_size=3, search_budget=5000),
    )
    res1 = reconcile(
        [Source.from_records("left", clean_left), Source.from_records("right", clean_right)],
        [rule],
    )
    res2 = reconcile(
        [
            Source.from_records("left", reversed(clean_left)),
            Source.from_records("right", reversed(clean_right)),
        ],
        [rule],
    )
    assert res1.matches == res2.matches
    assert res1.breaks == res2.breaks
    assert res1.run.run_id == res2.run.run_id


@given(
    data=st.data(),
    left_records=_record_list(source="left", prefix="L", max_size=8),
    right_records=_record_list(source="right", prefix="R", max_size=8),
)
@settings(max_examples=50)
def test_permutation_invariance(
    data: st.DataObject,
    left_records: list[Record],
    right_records: list[Record],
) -> None:
    # Give every record a unique external_id so dedup keeps the same records across shuffles.
    u_left = [
        Record(
            id=r.id,
            source=r.source,
            amount=r.amount,
            currency=r.currency,
            value_date=r.value_date,
            external_id=r.id,
        )
        for r in left_records
    ]
    u_right = [
        Record(
            id=r.id,
            source=r.source,
            amount=r.amount,
            currency=r.currency,
            value_date=r.value_date,
            external_id=r.id,
        )
        for r in right_records
    ]
    shuffled_left = data.draw(st.permutations(u_left))
    shuffled_right = data.draw(st.permutations(u_right))
    rule = Rule(id="r1", left="left", right="right", date=DateCondition(min_days=0, max_days=2))

    res_a = reconcile(
        [Source.from_records("left", u_left), Source.from_records("right", u_right)], [rule]
    )
    res_b = reconcile(
        [Source.from_records("left", shuffled_left), Source.from_records("right", shuffled_right)],
        [rule],
    )
    assert res_a.matches == res_b.matches
    assert res_a.breaks == res_b.breaks
    assert res_a.run.run_id == res_b.run.run_id


@given(
    amount=st.integers(min_value=1, max_value=100_000),
    day_offset=st.integers(min_value=0, max_value=30),
)
def test_cloning_sole_counterpart_produces_ambiguity(amount: int, day_offset: int) -> None:
    when = _BASE_DATE + timedelta(days=day_offset)
    left = Source.from_records(
        "left", [Record(id="l1", source="left", amount=amount, currency="USD", value_date=when)]
    )
    r1 = Record(
        id="r1", source="right", amount=amount, currency="USD", value_date=when, external_id="e1"
    )
    r2 = Record(
        id="r2", source="right", amount=amount, currency="USD", value_date=when, external_id="e2"
    )
    rule = Rule(id="r", left="left", right="right", date=DateCondition(min_days=0, max_days=1))

    unique_res = reconcile([left, Source.from_records("right", [r1])], [rule])
    assert len(unique_res.matches) == 1

    amb_res = reconcile([left, Source.from_records("right", [r1, r2])], [rule])
    assert amb_res.matches == ()
    breaks = {b.record_id: b for b in amb_res.breaks}
    assert breaks["l1"].reason is BreakReason.AMBIGUOUS
    assert breaks["r1"].reason is BreakReason.AMBIGUOUS
    assert breaks["r2"].reason is BreakReason.AMBIGUOUS


@given(
    minor=st.integers(min_value=-(10**15), max_value=10**15),
    currency=st.sampled_from(sorted(_EXPONENTS)),
)
def test_format_then_parse_is_exact(minor: int, currency: str) -> None:
    assert parse_amount(format_amount(minor, currency), currency) == minor


_CSV_ROW = st.tuples(
    st.integers(min_value=0, max_value=3),  # day offset
    st.integers(min_value=1, max_value=400),  # cents
    st.sampled_from(["", "INV-1", "INV-2", "INV-3"]),  # external id (may repeat)
    st.sampled_from(["a", "b"]),  # memo
)


@given(
    left_rows=st.lists(_CSV_ROW, max_size=8),
    right_rows=st.lists(_CSV_ROW, max_size=8),
    data=st.data(),
)
@settings(max_examples=40, deadline=None)
def test_shuffling_file_rows_changes_nothing_but_locations(
    left_rows: list[tuple[int, int, str, str]],
    right_rows: list[tuple[int, int, str, str]],
    data: st.DataObject,
) -> None:
    rule = Rule(id="r", left="left", right="right", date=DateCondition(min_days=-1, max_days=2))
    options = {
        "amount_column": "amount",
        "currency": "USD",
        "external_id_column": "ref",
        "attribute_columns": ("memo",),
    }

    def run(rows: dict[str, list[tuple[int, int, str, str]]], folder: Path) -> Result:
        sources = []
        for name, items in rows.items():
            path = folder / f"{name}.csv"
            lines = ["date,amount,ref,memo"] + [
                f"{_BASE_DATE + timedelta(days=d)},{c / 100:.2f},{ref},{memo}"
                for d, c, ref, memo in items
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            sources.append(read_source(name, path, "csv", **options))
        return reconcile(sources, [rule])

    shuffled = {
        "left": data.draw(st.permutations(left_rows)),
        "right": data.draw(st.permutations(right_rows)),
    }
    with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
        original = run({"left": left_rows, "right": right_rows}, Path(first))
        reordered = run(shuffled, Path(second))
    assert original.matches == reordered.matches
    assert original.breaks == reordered.breaks
    assert original.run.run_id == reordered.run.run_id
