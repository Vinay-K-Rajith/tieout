"""The canonical record every connector produces, and the source that groups them."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType

from tieout.errors import InputError
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
    """

    id: str
    source: str
    amount: int
    currency: str
    value_date: date
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
        if self.currency != normalize_currency(self.currency):
            raise InputError(f"record {self.id}: currency must be upper-case ISO 4217")
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


@dataclass(frozen=True, slots=True)
class Source:
    """A named collection of records, e.g. one bank statement or one ledger export.

    ``digest`` identifies the exact input for the run log. Connectors set it to the
    SHA-256 of the file bytes; for records built in memory it is derived from content.
    """

    name: str
    records: tuple[Record, ...]
    digest: str = ""

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
        if not self.digest:
            object.__setattr__(self, "digest", _content_digest(self.records))

    @classmethod
    def from_records(cls, name: str, records: Iterable[Record], digest: str = "") -> Source:
        return cls(name=name, records=tuple(records), digest=digest)


def _content_digest(records: Iterable[Record]) -> str:
    hasher = hashlib.sha256()
    for record in sorted(records, key=lambda r: r.id):
        payload = [record.id, *record.content_key()]
        hasher.update(json.dumps(payload, default=str, ensure_ascii=False).encode())
        hasher.update(b"\n")
    return f"sha256:{hasher.hexdigest()}"
