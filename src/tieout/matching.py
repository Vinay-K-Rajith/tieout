"""Pairing bundles of records under one rule, refusing to guess.

A *bundle* is what one side of a match is made of: a single record, or a keyed
group of records (all charges in one payout). One-to-one rules pair single
records; keyed grouping rules pair single records with groups. Both go through
the same resolution:

1. **Candidates.** For each left bundle, find right bundles that pass every
   check. An index on currency, total and first date keeps this from comparing
   everything with everything.
2. **Mutual best.** Rank candidates by (amount difference, date distance). A
   pair is accepted only when each side is the other's *unique* best. Accepted
   bundles leave the pool, which may give their neighbours a new unique best,
   so this repeats until nothing changes.
3. **Leftovers.** A bundle that still has open candidates was contested: two
   options were equally good, or its best option preferred someone else. It is
   reported as ambiguous with those candidates, never settled by a guess.

Mutual-best pairs in a given state never overlap, so each round accepts all of
them at once and the outcome does not depend on iteration order.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field

from tieout.conditions import (
    DateSpan,
    amount_difference,
    date_distance,
    date_span,
    dates_fit,
    expected_right_total,
    references_hold,
)
from tieout.records import Record
from tieout.spec import Rule


@dataclass(frozen=True, slots=True)
class Bundle:
    key: str
    records: tuple[Record, ...]
    currency: str
    total: int
    span: DateSpan

    @property
    def record_ids(self) -> tuple[str, ...]:
        return tuple(record.id for record in self.records)


def single_bundles(records: Iterable[Record], rule: Rule) -> list[Bundle]:
    """One bundle per record. Records missing the rule's date field can't qualify."""
    bundles = []
    for record in records:
        span = date_span((record,), rule)
        if span is not None:
            bundles.append(Bundle(record.id, (record,), record.currency, record.amount, span))
    return bundles


def keyed_bundles(records: Iterable[Record], rule: Rule, key_field: str) -> list[Bundle]:
    """Group records sharing ``key_field`` and currency. Records without a key can't qualify."""
    groups: dict[tuple[str, str], list[Record]] = defaultdict(list)
    for record in records:
        key = record.value_of(key_field)
        if key:
            groups[(key, record.currency)].append(record)

    bundles = []
    for (key, currency), members in sorted(groups.items()):
        span = date_span(members, rule)
        if span is None:
            continue
        total = sum(record.amount for record in members)
        bundle_key = f"{key_field}={key} ({currency})"
        bundles.append(Bundle(bundle_key, tuple(members), currency, total, span))
    return bundles


@dataclass(frozen=True, slots=True, order=True)
class Score:
    """Lower is better. Two options with equal scores mean ambiguity."""

    amount_difference: int
    date_distance: int


@dataclass(slots=True)
class Resolution:
    pairs: list[tuple[Bundle, Bundle]] = field(default_factory=list)
    contested: list[tuple[Bundle, list[Bundle]]] = field(default_factory=list)
    """Bundles left unpaired while candidates were still open, with those candidates."""


def resolve(rule: Rule, lefts: Sequence[Bundle], rights: Sequence[Bundle]) -> Resolution:
    left_by_key = {bundle.key: bundle for bundle in lefts}
    right_by_key = {bundle.key: bundle for bundle in rights}

    left_options: dict[str, dict[str, Score]] = defaultdict(dict)
    right_options: dict[str, dict[str, Score]] = defaultdict(dict)
    for left_key, right_key, score in _candidate_edges(rule, lefts, rights):
        left_options[left_key][right_key] = score
        right_options[right_key][left_key] = score

    open_left, open_right = set(left_options), set(right_options)
    resolution = Resolution()
    to_check = set(open_left)

    while to_check:
        accepted = []
        for left_key in sorted(to_check & open_left):
            choice = _unique_best(left_options[left_key], open_right)
            if choice is not None and (_unique_best(right_options[choice], open_left) == left_key):
                accepted.append((left_key, choice))

        to_check = set()
        for left_key, right_key in accepted:
            open_left.discard(left_key)
            open_right.discard(right_key)
            resolution.pairs.append((left_by_key[left_key], right_by_key[right_key]))
        for left_key, right_key in accepted:
            # A left can gain a new unique best only by losing an option...
            to_check.update(right_options[right_key].keys() & open_left)
            # ...and a right can, too; then only its new favourite can pair with it.
            for neighbour in left_options[left_key].keys() & open_right:
                favourite = _unique_best(right_options[neighbour], open_left)
                if favourite is not None:
                    to_check.add(favourite)

    for key in sorted(open_left):
        options = sorted(left_options[key].keys() & open_right)
        if options:
            resolution.contested.append((left_by_key[key], [right_by_key[k] for k in options]))
    for key in sorted(open_right):
        options = sorted(right_options[key].keys() & open_left)
        if options:
            resolution.contested.append((right_by_key[key], [left_by_key[k] for k in options]))

    resolution.pairs.sort(key=lambda pair: (pair[0].key, pair[1].key))
    return resolution


def _unique_best(options: dict[str, Score], available: set[str]) -> str | None:
    best_key: str | None = None
    best_score: Score | None = None
    tied = False
    for key, score in options.items():
        if key not in available:
            continue
        if best_score is None or score < best_score:
            best_key, best_score, tied = key, score, False
        elif score == best_score:
            tied = True
    return None if tied else best_key


def _candidate_edges(
    rule: Rule, lefts: Sequence[Bundle], rights: Sequence[Bundle]
) -> Iterator[tuple[str, str, Score]]:
    index = _CandidateIndex(rights)
    tolerance = rule.amount.tolerance
    for left in lefts:
        expected = expected_right_total(rule, left.total)
        first_from, first_to = _first_date_range(rule, left.span)
        candidates = index.lookup(
            left.currency, expected - tolerance, expected + tolerance, first_from, first_to
        )
        for right in candidates:
            if not dates_fit(rule, left.span, right.span):
                continue
            if not references_hold(rule, left.records, right.records):
                continue
            score = Score(
                abs(amount_difference(rule, left.total, right.total)),
                date_distance(left.span, right.span),
            )
            yield left.key, right.key, score


def _first_date_range(rule: Rule, left: DateSpan) -> tuple[int | None, int | None]:
    """Bounds on a candidate's earliest date implied by the date window, if any."""
    if rule.date is None:
        return None, None
    return left.last + rule.date.min_days, left.first + rule.date.max_days


class _CandidateIndex:
    """Bundles by currency, then total, then earliest date."""

    def __init__(self, bundles: Iterable[Bundle]) -> None:
        buckets: dict[str, dict[int, list[Bundle]]] = defaultdict(lambda: defaultdict(list))
        for bundle in bundles:
            buckets[bundle.currency][bundle.total].append(bundle)
        self._buckets: dict[str, dict[int, list[Bundle]]] = {}
        self._first_dates: dict[str, dict[int, list[int]]] = {}
        self._totals: dict[str, list[int]] = {}
        for currency, by_total in buckets.items():
            self._totals[currency] = sorted(by_total)
            self._buckets[currency] = {}
            self._first_dates[currency] = {}
            for total, members in by_total.items():
                members.sort(key=lambda bundle: (bundle.span.first, bundle.key))
                self._buckets[currency][total] = members
                self._first_dates[currency][total] = [bundle.span.first for bundle in members]

    def lookup(
        self,
        currency: str,
        low: int,
        high: int,
        first_from: int | None,
        first_to: int | None,
    ) -> Iterator[Bundle]:
        totals = self._totals.get(currency, [])
        for position in range(bisect_left(totals, low), bisect_right(totals, high)):
            total = totals[position]
            members = self._buckets[currency][total]
            dates = self._first_dates[currency][total]
            start = 0 if first_from is None else bisect_left(dates, first_from)
            stop = len(members) if first_to is None else bisect_right(dates, first_to)
            yield from members[start:stop]
