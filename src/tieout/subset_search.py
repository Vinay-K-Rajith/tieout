"""Bounded subset search for grouping rules without a shared key.

Given one target record (a bank deposit) and nearby candidates on the other side
(open invoices), find the subsets of at most ``max_size`` candidates whose total
equals the target within tolerance. This is subset-sum, which explodes, so it is
bounded three ways:

- candidates must already pass the date window, sign, currency and references;
- subsets hold at most ``group.max_size`` records;
- the search stops after ``group.search_budget`` steps. The budget counts steps,
  not seconds, so the same inputs always produce the same answer on any machine.

The engine needs to know whether the answer is *unique*, so the search stops at
the second solution. Two solutions mean ambiguity; an exhausted budget means
"could not prove uniqueness". Neither is ever turned into a guess.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from tieout.conditions import references_hold
from tieout.records import Record
from tieout.spec import Rule


@dataclass(frozen=True, slots=True)
class FoundGroup:
    target: Record
    members: tuple[Record, ...]
    steps: int


@dataclass(slots=True)
class SearchOutcome:
    found: list[FoundGroup] = field(default_factory=list)
    ambiguous: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Record id -> ids of the records it could have been grouped with."""
    exhausted: dict[str, int] = field(default_factory=dict)
    """Target record id -> steps spent before the budget ran out."""


def search_groups(
    rule: Rule, targets: Sequence[Record], pool: Sequence[Record], *, targets_are_left: bool
) -> SearchOutcome:
    """Find a unique group in ``pool`` for each record in ``targets``."""
    group = rule.grouping
    sign = 1 if rule.amount.sign == "same" else -1
    candidates_by_currency: dict[str, list[Record]] = defaultdict(list)
    for record in sorted(pool, key=lambda r: r.id):
        if record.amount != 0:  # zeros would make every solution non-unique
            candidates_by_currency[record.currency].append(record)

    unique: dict[str, FoundGroup] = {}
    outcome = SearchOutcome()
    for target in sorted(targets, key=lambda r: r.id):
        wanted = sign * target.amount
        if wanted == 0:
            continue
        candidates = [
            record
            for record in candidates_by_currency.get(target.currency, ())
            if _compatible(rule, target, record, wanted, targets_are_left=targets_are_left)
        ]
        search = _SubsetSearch(
            values=[abs(record.amount) for record in candidates],
            target=abs(wanted),
            tolerance=rule.amount.tolerance,
            max_size=group.max_size,
            budget=group.search_budget,
        )
        search.run()
        if search.exhausted:
            outcome.exhausted[target.id] = search.steps
        elif len(search.solutions) > 1:
            members = {candidates[i].id for solution in search.solutions for i in solution}
            _note_ambiguous(outcome, target.id, members)
        elif search.solutions:
            chosen = tuple(candidates[i] for i in search.solutions[0])
            unique[target.id] = FoundGroup(target, chosen, search.steps)

    # Two targets claiming the same record is ambiguity, whichever one came first.
    claims: dict[str, list[str]] = defaultdict(list)
    for found in unique.values():
        for member in found.members:
            claims[member.id].append(found.target.id)
    for target_id, found in sorted(unique.items()):
        rivals = {claimant for m in found.members for claimant in claims[m.id]}
        if rivals == {target_id}:
            outcome.found.append(found)
        else:
            _note_ambiguous(outcome, target_id, {m.id for m in found.members})
    return outcome


def _compatible(
    rule: Rule, target: Record, candidate: Record, wanted: int, *, targets_are_left: bool
) -> bool:
    if (candidate.amount > 0) != (wanted > 0):
        return False
    if abs(candidate.amount) > abs(wanted) + rule.amount.tolerance:
        return False
    left, right = (target, candidate) if targets_are_left else (candidate, target)
    if rule.date is not None:
        field_name = rule.date.field
        left_date = left.booking_date if field_name == "booking_date" else left.value_date
        right_date = right.booking_date if field_name == "booking_date" else right.value_date
        if left_date is None or right_date is None:
            return False
        gap = (right_date - left_date).days
        if not rule.date.min_days <= gap <= rule.date.max_days:
            return False
    return references_hold(rule, (left,), (right,))


def _note_ambiguous(outcome: SearchOutcome, target_id: str, member_ids: set[str]) -> None:
    outcome.ambiguous[target_id] = tuple(sorted(member_ids))
    for member_id in member_ids:
        existing = set(outcome.ambiguous.get(member_id, ()))
        outcome.ambiguous[member_id] = tuple(sorted(existing | {target_id}))


class _SubsetSearch:
    """Depth-first search over values sorted largest first, stopping at two solutions."""

    def __init__(
        self, *, values: list[int], target: int, tolerance: int, max_size: int, budget: int
    ) -> None:
        # Largest first makes both pruning rules below cut early.
        self._order = sorted(range(len(values)), key=lambda i: (-values[i], i))
        self._values = [values[i] for i in self._order]
        self._low = target - tolerance
        self._high = target + tolerance
        self._max_size = max_size
        self._budget = budget
        self._prefix = [0]
        for value in self._values:
            self._prefix.append(self._prefix[-1] + value)
        self.steps = 0
        self.exhausted = False
        self.solutions: list[tuple[int, ...]] = []

    def run(self) -> None:
        self._extend(start=0, chosen=[], total=0)

    def _extend(self, start: int, chosen: list[int], total: int) -> bool:
        """Explore subsets extending ``chosen``. Returns True when the search should stop."""
        slots = self._max_size - len(chosen)
        for position in range(start, len(self._values)):
            # The best this branch can still reach uses the largest remaining values.
            reachable = total + self._prefix[min(position + slots, len(self._values))]
            if reachable - self._prefix[position] < self._low:
                return False
            self.steps += 1
            if self.steps > self._budget:
                self.exhausted = True
                return True
            new_total = total + self._values[position]
            if new_total > self._high:
                continue
            chosen.append(position)
            if new_total >= self._low:
                self.solutions.append(tuple(sorted(self._order[p] for p in chosen)))
                if len(self.solutions) > 1:
                    return True
            if (
                slots > 1
                and new_total < self._high
                and self._extend(position + 1, chosen, new_total)
            ):
                return True
            chosen.pop()
        return False
