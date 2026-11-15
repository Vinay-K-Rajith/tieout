"""The reconciliation run: deduplicate, apply rules in order, account for every record."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Sequence
from importlib.metadata import PackageNotFoundError, version

from tieout.conditions import amount_difference, explain
from tieout.errors import InputError, InvariantError
from tieout.matching import Bundle, Resolution, keyed_bundles, resolve, single_bundles
from tieout.records import Record, Source
from tieout.result import Break, BreakReason, Match, Result, RunInfo
from tieout.spec import Rule, RuleKind, Spec
from tieout.subset_search import SearchOutcome, search_groups

try:
    ENGINE_VERSION = version("tieout")
except PackageNotFoundError:  # running from a source tree that isn't installed
    ENGINE_VERSION = "0+unknown"


def reconcile(sources: Sequence[Source], spec: Spec | Iterable[Rule]) -> Result:
    """Match records across sources using the spec's rules.

    Rules run in priority order. A record matched by one rule is not offered to
    later rules. Records nothing could match, or that several candidates fought
    over, come back as breaks with the reason.
    """
    if not isinstance(spec, Spec):
        spec = Spec(rules=tuple(spec))
    _check_inputs(sources, spec)

    records = {record.id: record for source in sources for record in source.records}
    breaks: list[Break] = []
    pools: dict[str, dict[str, Record]] = {}
    for source in sources:
        kept, duplicates = _deduplicate(source)
        pools[source.name] = {record.id: record for record in kept}
        breaks.extend(duplicates)

    matches: list[Match] = []
    notes: dict[str, Break] = {}
    for rule in spec.ordered_rules():
        rule_matches, rule_notes = _apply_rule(
            rule, pools[rule.left].values(), pools[rule.right].values()
        )
        for match in rule_matches:
            for record_id in match.left_ids:
                del pools[rule.left][record_id]
            for record_id in match.right_ids:
                del pools[rule.right][record_id]
        matches.extend(rule_matches)
        for record_id, note in rule_notes.items():
            notes.setdefault(record_id, note)  # the highest-priority rule's view wins

    for pool in pools.values():
        for record_id, record in pool.items():
            breaks.append(notes.get(record_id) or _unmatched(record, spec))

    breaks.sort(key=lambda item: item.record_id)
    result = Result(
        run=_run_info(sources, spec),
        records=records,
        matches=tuple(matches),
        breaks=tuple(breaks),
        excluded={source.name: source.excluded for source in sources if source.excluded},
    )
    verify_result(result, spec)
    return result


def _unmatched(record: Record, spec: Spec) -> Break:
    """An unmatched break, saying so when a rule could never see the record."""
    missing = sorted(
        {
            rule.date.field
            for rule in spec.rules
            if rule.date is not None
            and record.source in (rule.left, rule.right)
            and record.date_of(rule.date.field) is None
        }
    )
    if missing:
        return Break(
            record.id,
            BreakReason.UNMATCHED,
            f"no rule found a counterpart; the record has no {' or '.join(missing)}, "
            "which rules for its source compare",
        )
    return Break(record.id, BreakReason.UNMATCHED, "no rule found a counterpart")


def verify_result(result: Result, spec: Spec) -> None:
    """Check the accounting invariants of a finished run; raise rather than return a wrong one.

    - every record is in exactly one match or exactly one break;
    - every match is in one currency, with ``difference`` equal to the right total
      minus the expected right total, and within the rule's tolerance.
    """
    rules = {rule.id: rule for rule in spec.rules}
    placed: dict[str, str] = {}

    def place(record_id: str, where: str) -> None:
        if record_id not in result.records:
            raise InvariantError(f"{where} refers to unknown record {record_id!r}")
        if record_id in placed:
            raise InvariantError(f"record {record_id!r} is in {placed[record_id]} and in {where}")
        placed[record_id] = where

    for match in result.matches:
        rule = rules[match.rule_id]
        if not match.left_ids or not match.right_ids:
            raise InvariantError(f"match {match.id} has an empty side")
        for record_id in match.left_ids:
            place(record_id, f"match {match.id}")
            if result.records[record_id].source != rule.left:
                raise InvariantError(f"match {match.id}: {record_id} is not from {rule.left}")
        for record_id in match.right_ids:
            place(record_id, f"match {match.id}")
            if result.records[record_id].source != rule.right:
                raise InvariantError(f"match {match.id}: {record_id} is not from {rule.right}")
        members = [result.records[rid] for rid in match.record_ids]
        if {record.currency for record in members} != {match.currency}:
            raise InvariantError(f"match {match.id} mixes currencies")
        left_total = sum(result.records[rid].amount for rid in match.left_ids)
        right_total = sum(result.records[rid].amount for rid in match.right_ids)
        if amount_difference(rule, left_total, right_total) != match.difference:
            raise InvariantError(f"match {match.id}: recorded difference is wrong")
        if abs(match.difference) > rule.amount.tolerance:
            raise InvariantError(f"match {match.id}: difference exceeds tolerance")

    for item in result.breaks:
        place(item.record_id, f"break {item.reason.value}")

    unplaced = sorted(set(result.records) - set(placed))
    if unplaced:
        raise InvariantError(f"records neither matched nor reported: {unplaced[:10]}")


def _check_inputs(sources: Sequence[Source], spec: Spec) -> None:
    names = [source.name for source in sources]
    duplicated = sorted({name for name in names if names.count(name) > 1})
    if duplicated:
        raise InputError(f"source names must be unique, got duplicates: {duplicated}")
    for rule in spec.rules:
        for side in (rule.left, rule.right):
            if side not in names:
                raise InputError(f"rule {rule.id!r} needs source {side!r}, which was not provided")
    seen: dict[str, str] = {}
    for source in sources:
        for record in source.records:
            if record.id in seen:
                raise InputError(
                    f"record id {record.id!r} appears in sources {seen[record.id]!r} "
                    f"and {source.name!r}; ids must be unique across sources"
                )
            seen[record.id] = source.name


def _deduplicate(source: Source) -> tuple[list[Record], list[Break]]:
    """Drop exact re-deliveries; quarantine records whose external ids collide.

    Only external ids count. Two identical rows *without* an id may be two real
    payments (two coffees, same price, same day), so they are both kept.
    """
    by_external_id: dict[str, list[Record]] = defaultdict(list)
    for record in source.records:
        if record.external_id:
            by_external_id[record.external_id].append(record)

    excluded: dict[str, Break] = {}
    for external_id, group in by_external_id.items():
        if len(group) < 2:
            continue
        # The record with the smallest id is the original, not the first in file order:
        # connectors assign ids from content, so this choice survives shuffled input.
        group.sort(key=lambda record: record.id)
        first = group[0]
        if all(record.content_key() == first.content_key() for record in group[1:]):
            for record in group[1:]:
                excluded[record.id] = Break(
                    record.id,
                    BreakReason.DUPLICATE,
                    f"same external id {external_id!r} and content as {first.id}",
                    candidate_ids=(first.id,),
                )
        else:
            for record in group:
                others = tuple(other.id for other in group if other is not record)
                excluded[record.id] = Break(
                    record.id,
                    BreakReason.DUPLICATE_CONFLICT,
                    f"external id {external_id!r} is reused with different content; "
                    "excluded from matching until resolved",
                    candidate_ids=others,
                )

    kept = [record for record in source.records if record.id not in excluded]
    return kept, list(excluded.values())


def _apply_rule(
    rule: Rule, lefts: Iterable[Record], rights: Iterable[Record]
) -> tuple[list[Match], dict[str, Break]]:
    if rule.kind is RuleKind.ONE_TO_ONE:
        resolution = resolve(rule, single_bundles(lefts, rule), single_bundles(rights, rule))
        return _from_resolution(rule, resolution)

    key_field = rule.grouping.by
    if key_field is not None:
        if rule.kind is RuleKind.ONE_TO_MANY:
            left_bundles = single_bundles(lefts, rule)
            right_bundles = keyed_bundles(rights, rule, key_field)
        else:
            left_bundles = keyed_bundles(lefts, rule, key_field)
            right_bundles = single_bundles(rights, rule)
        return _from_resolution(rule, resolve(rule, left_bundles, right_bundles))

    if rule.kind is RuleKind.ONE_TO_MANY:
        outcome = search_groups(rule, list(lefts), list(rights), targets_are_left=True)
    else:
        outcome = search_groups(rule, list(rights), list(lefts), targets_are_left=False)
    return _from_search(rule, outcome)


def _from_resolution(rule: Rule, resolution: Resolution) -> tuple[list[Match], dict[str, Break]]:
    matches = []
    for left, right in resolution.pairs:
        note = _shared_key_note(rule, left, right)
        matches.append(_make_match(rule, left.records, right.records, note))

    notes: dict[str, Break] = {}
    for bundle, options in resolution.contested:
        candidate_ids = tuple(sorted(rid for option in options for rid in option.record_ids))
        detail = (
            f"{len(options)} open candidate(s) under rule {rule.id}: equally good, "
            "or preferred by a better counterpart"
        )
        for record_id in bundle.record_ids:
            notes[record_id] = Break(
                record_id, BreakReason.AMBIGUOUS, detail, rule.id, candidate_ids
            )
    return matches, notes


def _from_search(rule: Rule, outcome: SearchOutcome) -> tuple[list[Match], dict[str, Break]]:
    matches = []
    for found in outcome.found:
        note = (
            f"group: the only subset of at most {rule.grouping.max_size} records "
            f"that fits, found in {found.steps} search steps"
        )
        if rule.kind is RuleKind.ONE_TO_MANY:
            matches.append(_make_match(rule, (found.target,), found.members, note))
        else:
            matches.append(_make_match(rule, found.members, (found.target,), note))

    notes: dict[str, Break] = {}
    for record_id, candidate_ids in outcome.ambiguous.items():
        rivals = outcome.contested.get(record_id)
        detail = (
            f"its only grouping under rule {rule.id} uses records that "
            f"{', '.join(rivals)} could also claim"
            if rivals
            else f"more than one grouping fits under rule {rule.id}"
        )
        notes[record_id] = Break(
            record_id,
            BreakReason.AMBIGUOUS,
            detail,
            rule.id,
            tuple(sorted({*candidate_ids, *(rivals or ())})),
        )
    for record_id, steps in outcome.exhausted.items():
        notes[record_id] = Break(
            record_id,
            BreakReason.SEARCH_LIMIT,
            f"group search stopped after {steps} steps without a unique answer; "
            "raise group.search_budget or narrow the date window",
            rule.id,
        )
    return matches, notes


def _make_match(
    rule: Rule, lefts: Sequence[Record], rights: Sequence[Record], grouping_note: str | None
) -> Match:
    left_ids = tuple(sorted(record.id for record in lefts))
    right_ids = tuple(sorted(record.id for record in rights))
    fingerprint = "\x1f".join((rule.id, *left_ids, "|", *right_ids))
    return Match(
        id="m_" + hashlib.sha256(fingerprint.encode()).hexdigest()[:16],
        rule_id=rule.id,
        left_ids=left_ids,
        right_ids=right_ids,
        currency=lefts[0].currency,
        difference=amount_difference(
            rule, sum(r.amount for r in lefts), sum(r.amount for r in rights)
        ),
        explanation=explain(rule, lefts, rights, grouping_note=grouping_note),
    )


def _shared_key_note(rule: Rule, left: Bundle, right: Bundle) -> str | None:
    key_field = rule.grouping.by if rule.kind is not RuleKind.ONE_TO_ONE else None
    if key_field is None:
        return None
    grouped = right if rule.kind is RuleKind.ONE_TO_MANY else left
    side = "right" if rule.kind is RuleKind.ONE_TO_MANY else "left"
    value = grouped.records[0].value_of(key_field)
    return f"group: {len(grouped.records)} {side} records share {key_field} {value!r}"


def _run_info(sources: Sequence[Source], spec: Spec) -> RunInfo:
    ordered = sorted(sources, key=lambda s: s.name)
    input_digests = {source.name: source.digest for source in ordered}
    content_digests = {source.name: source.content_digest for source in ordered}
    spec_digest = spec.digest()
    # The run id covers what was reconciled, not how the file happened to be ordered:
    # content digests ignore row order, while input_digests pin the exact bytes.
    material = "\n".join(
        [ENGINE_VERSION, spec_digest, *(f"{name}={d}" for name, d in content_digests.items())]
    )
    return RunInfo(
        run_id="run_" + hashlib.sha256(material.encode()).hexdigest()[:16],
        engine_version=ENGINE_VERSION,
        spec_digest=spec_digest,
        input_digests=input_digests,
        content_digests=content_digests,
    )
