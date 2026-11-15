"""Throughput benchmarks for 1:1 and keyed many-to-one reconciliation."""

from __future__ import annotations

import time
from datetime import date, timedelta

from tieout import Record, Rule, RuleKind, Source, reconcile
from tieout.spec import DateCondition, GroupSpec, ReferenceCondition

_BASE_DATE = date(2026, 1, 1)


def _bench_one_to_one(n: int) -> None:
    left_records = [
        Record(
            id=f"l:{i}",
            source="left",
            amount=10_000 + (i % 500) * 13,
            currency="USD",
            value_date=_BASE_DATE + timedelta(days=i % 90),
            external_id=f"ref-{i:07d}",
        )
        for i in range(n)
    ]
    right_records = [
        Record(
            id=f"r:{i}",
            source="right",
            amount=10_000 + (i % 500) * 13,
            currency="USD",
            value_date=_BASE_DATE + timedelta(days=(i % 90) + (i % 2)),
            description=f"Settlement REF-{i:07d}",
        )
        for i in range(n)
    ]
    left = Source.from_records("left", left_records)
    right = Source.from_records("right", right_records)
    rule = Rule(
        id="exact-1-to-1",
        left="left",
        right="right",
        date=DateCondition(min_days=0, max_days=2),
        references=(
            ReferenceCondition(left="external_id", right="description", op="left_in_right"),
        ),
    )

    start = time.perf_counter()
    result = reconcile([left, right], [rule])
    elapsed = time.perf_counter() - start
    assert len(result.matches) == n
    assert result.breaks == ()
    total_records = 2 * n
    print(f"1:1      {n:>7,} x {n:<7,}  {elapsed:6.3f}s  ({total_records / elapsed:>10,.0f} rec/s)")


def _bench_many_to_one(left_n: int, group_size: int = 5) -> None:
    groups = left_n // group_size
    left_records = [
        Record(
            id=f"l:{i}",
            source="left",
            amount=1_000 + (i % 97),
            currency="USD",
            value_date=_BASE_DATE + timedelta(days=(i // group_size) % 90),
            attributes={"payout_id": f"po_{i // group_size:06d}"},
        )
        for i in range(groups * group_size)
    ]
    right_records = []
    for g in range(groups):
        base = g * group_size
        group_sum = sum(1_000 + ((base + k) % 97) for k in range(group_size))
        right_records.append(
            Record(
                id=f"r:{g}",
                source="right",
                amount=group_sum,
                currency="USD",
                value_date=_BASE_DATE + timedelta(days=(g % 90) + 1),
                description=f"STRIPE po_{g:06d}",
            )
        )

    left = Source.from_records("left", left_records)
    right = Source.from_records("right", right_records)
    rule = Rule(
        id="keyed-m2o",
        left="left",
        right="right",
        kind=RuleKind.MANY_TO_ONE,
        group=GroupSpec(by="payout_id"),
        date=DateCondition(min_days=0, max_days=3),
        references=(ReferenceCondition(left="payout_id", right="description", op="left_in_right"),),
    )

    start = time.perf_counter()
    result = reconcile([left, right], [rule])
    elapsed = time.perf_counter() - start
    assert len(result.matches) == groups
    assert result.breaks == ()
    total_records = len(left_records) + len(right_records)
    print(
        f"N:1      {len(left_records):>7,} -> {len(right_records):<6,}  "
        f"{elapsed:6.3f}s  ({total_records / elapsed:>10,.0f} rec/s)"
    )


def main() -> None:
    for size in (10_000, 100_000):
        _bench_one_to_one(size)
        _bench_many_to_one(size)


if __name__ == "__main__":
    main()
