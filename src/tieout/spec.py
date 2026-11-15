"""The reconciliation spec: which sources exist and which rules match them.

A spec is usually written in YAML::

    version: 1
    sources:
      bank:   { format: camt }
      stripe: { format: stripe }
    rules:
      - id: payout-to-bank
        left: stripe
        right: bank
        kind: many_to_one
        group: { by: payout_id }
        date: { min_days: 0, max_days: 3 }

Every model forbids unknown keys, so a typo is an error instead of a rule that
silently does less than its author thinks.
"""

from __future__ import annotations

import hashlib
from collections.abc import Hashable
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from tieout.errors import SpecError

DEFAULT_MAX_GROUP_SIZE = 5
DEFAULT_SEARCH_BUDGET = 50_000


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuleKind(StrEnum):
    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"
    """One left record equals the sum of several right records."""
    MANY_TO_ONE = "many_to_one"
    """Several left records sum to one right record."""


class AmountCondition(_Model):
    tolerance: int = Field(default=0, ge=0, description="Allowed difference, in minor units.")
    sign: Literal["same", "opposite"] = Field(
        default="same",
        description="'opposite' when the two sources record the same movement with "
        "opposite signs, e.g. a bank credit against a payable.",
    )


class DateCondition(_Model):
    """Allowed gap between dates, measured as right date minus left date, in days."""

    min_days: int = 0
    max_days: int = 0
    field: Literal["value_date", "booking_date"] = "value_date"

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.min_days > self.max_days:
            raise ValueError(
                f"min_days ({self.min_days}) is greater than max_days ({self.max_days})"
            )
        return self


class ReferenceCondition(_Model):
    """Compare a field of the left record with a field of the right record.

    Values are normalised before comparing: upper-cased, with everything except
    letters and digits removed, so ``"INV-0042"`` equals ``"inv 0042"``.
    """

    left: str
    right: str
    op: Literal["equals", "left_in_right", "right_in_left"] = "equals"
    min_length: int = Field(
        default=4,
        ge=1,
        description="For containment checks: shorter needles never match, so that "
        "a reference like '7' doesn't match every description containing a 7.",
    )


class GroupSpec(_Model):
    """How to form the 'many' side of a one-to-many or many-to-one rule.

    With ``by``, records sharing that field (and currency) form one group, of any
    size. Without it, the engine searches for subsets of at most ``max_size``
    records, spending at most ``search_budget`` steps per target record.
    """

    by: str | None = None
    max_size: int = Field(default=DEFAULT_MAX_GROUP_SIZE, ge=1, le=20)
    search_budget: int = Field(default=DEFAULT_SEARCH_BUDGET, ge=1)


class Rule(_Model):
    id: str = Field(min_length=1)
    left: str = Field(min_length=1)
    right: str = Field(min_length=1)
    kind: RuleKind = RuleKind.ONE_TO_ONE
    priority: int = Field(default=100, description="Lower runs first.")
    amount: AmountCondition = AmountCondition()
    date: DateCondition | None = None
    references: tuple[ReferenceCondition, ...] = ()
    group: GroupSpec | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.left == self.right:
            raise ValueError("left and right must be different sources")
        if self.kind is RuleKind.ONE_TO_ONE:
            if self.group is not None:
                raise ValueError("'group' only applies to one_to_many and many_to_one rules")
        elif self.grouping.by is None and self.date is None:
            # Unkeyed subset search over a whole source is unbounded in practice.
            raise ValueError(
                "grouping rules need 'group.by' or a 'date' window to bound the search"
            )
        return self

    @property
    def grouping(self) -> GroupSpec:
        """Group settings for grouping rules, with defaults when ``group`` was omitted."""
        return self.group or GroupSpec()


class SourceSpec(_Model):
    format: str = Field(min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)


class Spec(_Model):
    version: Literal[1] = 1
    sources: dict[str, SourceSpec] = Field(default_factory=dict)
    rules: tuple[Rule, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _references_resolve(self) -> Self:
        seen: set[str] = set()
        for rule in self.rules:
            if rule.id in seen:
                raise ValueError(f"duplicate rule id {rule.id!r}")
            seen.add(rule.id)
        if self.sources:
            for rule in self.rules:
                for side in (rule.left, rule.right):
                    if side not in self.sources:
                        raise ValueError(f"rule {rule.id!r} refers to undeclared source {side!r}")
        return self

    def ordered_rules(self) -> tuple[Rule, ...]:
        """Rules in evaluation order: by priority, then id, so ties are deterministic."""
        return tuple(sorted(self.rules, key=lambda rule: (rule.priority, rule.id)))

    def digest(self) -> str:
        """Stable hash of the whole spec, recorded with every run."""
        canonical = self.model_dump_json()
        return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


def parse_spec(data: object) -> Spec:
    """Validate already-loaded data (e.g. from YAML or JSON) into a `Spec`."""
    try:
        return Spec.model_validate(data)
    except ValidationError as exc:
        raise SpecError(_describe(exc)) from exc


def load_spec(path: str | Path) -> Spec:
    """Read and validate a YAML spec file."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError(f"cannot read spec {path}: {exc.strerror}") from exc
    try:
        data = yaml.load(text, Loader=_StrictLoader)
    except yaml.YAMLError as exc:
        raise SpecError(f"{path}: invalid YAML: {exc}") from exc
    try:
        return parse_spec(data)
    except SpecError as exc:
        raise SpecError(f"{path}: {exc}") from exc


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate mapping keys instead of keeping the last one."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        keys: set[Hashable] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in keys:
                mark = key_node.start_mark
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r} (line {mark.line + 1})", mark
                )
            keys.add(key)
        return super().construct_mapping(node, deep=deep)


def _describe(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "spec"
        lines.append(f"{location}: {error['msg']}")
    return "invalid spec:\n  " + "\n  ".join(lines)
