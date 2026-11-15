"""The canonical record every connector produces, and the source that groups them."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType

from tieout.errors import InputError, MoneyError
from tieout.money import normalize_currency

# Field names a rule may refer to besides free-form attributes.
BUILTIN_FIELDS = frozenset({"external_id", "description"})


@dataclass(frozen=True, slots=True, kw_only=True)
class Record:
    """One line of money movement from one source, in canonical form.

    ``amount`` is signed, in minor units, from the point of view of the account
    the source describes: positive means money came in, negative means it went out.
    Connectors are responsible for that normalisation; rules can still compare
    sources with opposite conventions via ``amount.sign: opposite``.

    A record has a value date, a booking date, or both. A missing date is left
    missing, never filled in from the other one: a rule that compares value dates
    must not quietly compare a booking date instead.
    """

    id: str
    source: str
    amount: int
    currency: str
    value_date: date | None = None
    booking_date: date | None = None
    external_id: str | None = None
    description: str = ""
    attributes: Mapping[str, str] = field(default_factory=dict)
    location: str | None = None
    """Where the record came from inside its file, e.g. ``"line 42"``."""

    def __post_init__(self) -> None:
        if not self.id:
            raise InputError("record id must not be empty")
        if isinstance(self.amount, bool) or not isinstance(self.amount, int):
            raise InputError(
                f"record {self.id}: amount must be int minor units, got {self.amount!r}"
            )
        try:
            canonical_currency = normalize_currency(self.currency)
        except MoneyError as exc:
            raise InputError(f"record {self.id}: {exc}") from exc
        if self.currency != canonical_currency:
            raise InputError(f"record {self.id}: currency must be upper-case ISO 4217")
        if self.value_date is None and self.booking_date is None:
            raise InputError(f"record {self.id}: needs a value_date or a booking_date")
        clashes = BUILTIN_FIELDS & self.attributes.keys()
        if clashes:
            raise InputError(f"record {self.id}: attribute names {sorted(clashes)} are reserved")
        # Freeze attributes so a record really is immutable once built.
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))

    def value_of(self, name: str) -> str:
        """Value of a named field for rule evaluation; missing fields are ``""``."""
        if name == "external_id":
            return self.external_id or ""
        if name == "description":
            return self.description
        return self.attributes.get(name, "")

    def date_of(self, name: str) -> date | None:
        """``value_date`` or ``booking_date`` by name, with no fallback between them."""
        return self.booking_date if name == "booking_date" else self.value_date

    def content_key(self) -> tuple[object, ...]:
        """Everything that defines the record's content (not its id or location)."""
        return (
            self.amount,
            self.currency,
            self.value_date,
            self.booking_date,
            self.external_id,
            self.description,
            tuple(sorted(self.attributes.items())),
        )

    def content_fingerprint(self) -> str:
        """Canonical text of `content_key`, stable across runs and platforms."""
        return json.dumps(self.content_key(), default=str, ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class Exclusion:
    """An input row a connector deliberately left out, e.g. a pending bank entry.

    Kept so a run can account for every row of every file, not only the records
    that reached the engine.
    """

    location: str
    reason: str


@dataclass(frozen=True, slots=True)
class Source:
    """A named collection of records, e.g. one bank statement or one ledger export.

    ``digest`` identifies the exact input bytes for the run log: connectors set it
    to the SHA-256 of the file. ``content_digest`` is derived from the records
    alone, ignoring their order, so shuffled rows of the same file reconcile to the
    same run id.
    """

    name: str
    records: tuple[Record, ...]
    digest: str = ""
    excluded: tuple[Exclusion, ...] = ()
    content_digest: str = field(default="", init=False)

    def __post_init__(self) -> None:
        if not self.name:
            raise InputError("source name must not be empty")
        seen: set[str] = set()
        for record in self.records:
            if record.source != self.name:
                raise InputError(
                    f"record {record.id} belongs to source {record.source!r}, not {self.name!r}"
                )
            if record.id in seen:
                raise InputError(f"duplicate record id {record.id!r} in source {self.name!r}")
            seen.add(record.id)
        object.__setattr__(self, "content_digest", _content_digest(self.records))
        if not self.digest:
            object.__setattr__(self, "digest", self.content_digest)

    @classmethod
    def from_records(
        cls,
        name: str,
        records: Iterable[Record],
        digest: str = "",
        excluded: Iterable[Exclusion] = (),
    ) -> Source:
        return cls(name=name, records=tuple(records), digest=digest, excluded=tuple(excluded))


def assign_stable_ids(
    source: str,
    records: Iterable[Record],
    natural_key: Callable[[Record], str | None] = lambda record: None,
) -> list[Record]:
    """Give records ids that do not depend on their position in the file.

    The id is ``source:<natural key>`` when the record has one (a Stripe balance
    transaction id, a bank reference), else ``source:<content hash>``. Records that
    share a base get ``~2``, ``~3``... in content order, so an exact re-delivery and
    an id collision both stay distinct and reproducible. Shuffling the input rows
    yields the same ids; only ``location`` follows the rows.
    """
    ordered = list(records)
    groups: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(ordered):
        base = natural_key(record) or _content_hash(record)
        groups[base].append(index)

    renamed = list(ordered)
    for base, indexes in groups.items():
        # Stable sort: identical records keep file order, which cannot change any outcome.
        indexes.sort(key=lambda i: ordered[i].content_fingerprint())
        for rank, index in enumerate(indexes):
            suffix = "" if rank == 0 else f"~{rank + 1}"
            renamed[index] = dataclasses.replace(ordered[index], id=f"{source}:{base}{suffix}")
    return renamed


def _content_hash(record: Record) -> str:
    return hashlib.sha256(record.content_fingerprint().encode()).hexdigest()[:12]


def _content_digest(records: Iterable[Record]) -> str:
    hasher = hashlib.sha256()
    for record in sorted(records, key=lambda r: r.id):
        hasher.update(json.dumps([record.id, record.content_fingerprint()]).encode())
        hasher.update(b"\n")
    return f"sha256:{hasher.hexdigest()}"
