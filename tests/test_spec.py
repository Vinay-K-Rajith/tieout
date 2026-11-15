"""Tests for reconciliation spec parsing and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from tieout.errors import SpecError
from tieout.spec import RuleKind, load_spec, parse_spec


def test_parse_spec_and_ordering() -> None:
    spec = parse_spec(
        {
            "version": 1,
            "sources": {
                "bank": {"format": "camt"},
                "stripe": {"format": "stripe"},
            },
            "rules": [
                {"id": "b-rule", "left": "stripe", "right": "bank", "priority": 20},
                {"id": "a-rule", "left": "stripe", "right": "bank", "priority": 20},
                {"id": "top-rule", "left": "stripe", "right": "bank", "priority": 5},
            ],
        }
    )
    assert [rule.id for rule in spec.ordered_rules()] == ["top-rule", "a-rule", "b-rule"]
    assert spec.digest().startswith("sha256:")
    assert spec.digest() == parse_spec(spec.model_dump()).digest()


def test_load_spec_from_file(tmp_path: Path) -> None:
    spec_path = tmp_path / "recon.yaml"
    spec_path.write_text(
        """
version: 1
sources:
  left_src: { format: csv }
  right_src: { format: csv }
rules:
  - id: grouped
    left: left_src
    right: right_src
    kind: many_to_one
    date: { min_days: 0, max_days: 2 }
""",
        encoding="utf-8",
    )
    spec = load_spec(spec_path)
    assert spec.rules[0].kind is RuleKind.MANY_TO_ONE
    assert spec.rules[0].grouping.max_size == 5


def test_load_spec_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    spec_path = tmp_path / "dup.yaml"
    spec_path.write_text(
        """
version: 1
rules:
  - id: r1
    left: a
    right: b
    priority: 10
    priority: 20
""",
        encoding="utf-8",
    )
    with pytest.raises(SpecError, match="duplicate key 'priority'"):
        load_spec(spec_path)


def test_load_spec_missing_file_raises_spec_error(tmp_path: Path) -> None:
    with pytest.raises(SpecError, match="cannot read spec"):
        load_spec(tmp_path / "missing.yaml")


def test_load_spec_invalid_yaml_raises_spec_error(tmp_path: Path) -> None:
    spec_path = tmp_path / "bad.yaml"
    spec_path.write_text("rules: [unclosed\n", encoding="utf-8")
    with pytest.raises(SpecError, match="invalid YAML"):
        load_spec(spec_path)


@pytest.mark.parametrize(
    "payload",
    [
        # Unknown key forbidden
        {
            "version": 1,
            "unknown_field": True,
            "rules": [{"id": "r1", "left": "a", "right": "b"}],
        },
        # left == right
        {"version": 1, "rules": [{"id": "r1", "left": "a", "right": "a"}]},
        # min_days > max_days
        {
            "version": 1,
            "rules": [
                {
                    "id": "r1",
                    "left": "a",
                    "right": "b",
                    "date": {"min_days": 5, "max_days": 1},
                }
            ],
        },
        # group on one_to_one
        {
            "version": 1,
            "rules": [
                {
                    "id": "r1",
                    "left": "a",
                    "right": "b",
                    "kind": "one_to_one",
                    "group": {"by": "payout_id"},
                }
            ],
        },
        # unkeyed grouping without date window
        {
            "version": 1,
            "rules": [
                {
                    "id": "r1",
                    "left": "a",
                    "right": "b",
                    "kind": "many_to_one",
                }
            ],
        },
        # undeclared source when sources section is populated
        {
            "version": 1,
            "sources": {"a": {"format": "csv"}},
            "rules": [{"id": "r1", "left": "a", "right": "b"}],
        },
        # duplicate rule id
        {
            "version": 1,
            "rules": [
                {"id": "r1", "left": "a", "right": "b"},
                {"id": "r1", "left": "a", "right": "b"},
            ],
        },
    ],
)
def test_parse_spec_validation_errors(payload: dict[str, object]) -> None:
    with pytest.raises(SpecError):
        parse_spec(payload)
