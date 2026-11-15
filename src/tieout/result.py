"""What a reconciliation run produces.

Every input record ends up in exactly one place: inside one `Match`, or as one
`Break`. Nothing is dropped silently, not even duplicates.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from tieout.records import Exclusion, Record


class BreakReason(StrEnum):
    UNMATCHED = "unmatched"
    """No rule found a counterpart."""
    AMBIGUOUS = "ambiguous"
    """Several counterparts were equally good; the engine refuses to guess."""
    SEARCH_LIMIT = "search_limit"
    """Group search hit its step budget before it could prove a unique answer."""
    DUPLICATE = "duplicate"
    """Same external id and same content as an earlier record in the same source."""
    DUPLICATE_CONFLICT = "duplicate_conflict"
    """Same external id as another record in the same source, but different content."""


@dataclass(frozen=True, slots=True)
class Match:
    id: str
    rule_id: str
    left_ids: tuple[str, ...]
    right_ids: tuple[str, ...]
    currency: str
    difference: int
    """Right total minus expected right total, in minor units; zero for exact matches."""
    explanation: tuple[str, ...]

    @property
    def record_ids(self) -> tuple[str, ...]:
        return self.left_ids + self.right_ids


@dataclass(frozen=True, slots=True)
class Break:
    record_id: str
    reason: BreakReason
    detail: str
    rule_id: str | None = None
    candidate_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunInfo:
    run_id: str
    """Derived from engine version, spec and inputs: same inputs, same run id."""
    engine_version: str
    spec_digest: str
    input_digests: Mapping[str, str]
    """SHA-256 of each source's raw bytes (or of its records, for in-memory sources)."""
    content_digests: Mapping[str, str]
    """Digest of each source's records, independent of row order; feeds the run id."""


@dataclass(frozen=True, slots=True)
class Result:
    run: RunInfo
    records: Mapping[str, Record]
    matches: tuple[Match, ...]
    breaks: tuple[Break, ...]
    excluded: Mapping[str, tuple[Exclusion, ...]] = field(default_factory=dict)
    """Rows each connector filtered out on purpose (pending entries, payouts...)."""

    def summary(self) -> ResultSummary:
        matched = sum(len(match.record_ids) for match in self.matches)
        reasons = Counter(item.reason for item in self.breaks)
        return ResultSummary(
            records=len(self.records),
            matches=len(self.matches),
            matched_records=matched,
            breaks_by_reason={reason: reasons[reason] for reason in BreakReason if reasons[reason]},
            excluded=sum(len(rows) for rows in self.excluded.values()),
        )


@dataclass(frozen=True, slots=True)
class ResultSummary:
    records: int
    matches: int
    matched_records: int
    breaks_by_reason: Mapping[BreakReason, int]
    excluded: int = 0
    """Input rows filtered out by connectors before matching."""

    @property
    def breaks(self) -> int:
        return sum(self.breaks_by_reason.values())

    @property
    def match_rate(self) -> float:
        """Share of records that were matched; for display only, never for decisions."""
        return self.matched_records / self.records if self.records else 1.0
