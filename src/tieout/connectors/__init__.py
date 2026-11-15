"""Source connectors: parse external files into canonical `Source` objects."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tieout.connectors.camt import CamtOptions, parse_camt
from tieout.connectors.csv import CsvOptions, parse_csv
from tieout.connectors.stripe import StripeOptions, parse_stripe
from tieout.errors import ConnectorError, SpecError
from tieout.records import Exclusion, Record, Source
from tieout.spec import RuleKind, SourceSpec, Spec

_Parser = Callable[[str, Path, bytes, dict[str, Any]], tuple[list[Record], list[Exclusion]]]

_CONNECTORS: dict[str, tuple[_Parser, type[CamtOptions | CsvOptions | StripeOptions]]] = {
    "camt": (parse_camt, CamtOptions),
    "csv": (parse_csv, CsvOptions),
    "stripe": (parse_stripe, StripeOptions),
}


def validate_source_spec(spec: SourceSpec) -> frozenset[str]:
    """Verify a source's connector format and options without reading a file.

    Returns the field names records from that source can carry.
    """
    entry = _CONNECTORS.get(spec.format)
    if entry is None:
        supported = ", ".join(sorted(_CONNECTORS))
        raise SpecError(f"unknown source format {spec.format!r} (expected one of: {supported})")
    _, option_model = entry
    try:
        options = option_model.model_validate(spec.options)
    except ValidationError as exc:
        lines = [
            f"{'.'.join(str(p) for p in err['loc']) or 'options'}: {err['msg']}"
            for err in exc.errors()
        ]
        raise SpecError(f"invalid {spec.format} options:\n  " + "\n  ".join(lines)) from exc
    return options.fields()


def validate_spec(spec: Spec) -> None:
    """Check every source's options, and that every field a rule names can exist.

    A misspelt field (``payout_idd``) would otherwise match nothing, silently: an
    absent reference never counts as agreement and an absent group key forms no
    group. Only declared sources are checked.
    """
    available = {name: validate_source_spec(source) for name, source in spec.sources.items()}
    problems: list[str] = []
    for rule in spec.rules:
        wanted: list[tuple[str, str, str]] = []  # (side, source, field)
        for condition in rule.references:
            wanted.append(("left", rule.left, condition.left))
            wanted.append(("right", rule.right, condition.right))
        if rule.date is not None:
            wanted.append(("left", rule.left, rule.date.field))
            wanted.append(("right", rule.right, rule.date.field))
        if rule.group is not None and rule.group.by is not None:
            side, source = (
                ("right", rule.right) if rule.kind is RuleKind.ONE_TO_MANY else ("left", rule.left)
            )
            wanted.append((side, source, rule.group.by))
        for side, source, field in wanted:
            fields = available.get(source)
            if fields is not None and field not in fields:
                problems.append(
                    f"rule {rule.id!r}: {side} source {source!r} has no field {field!r} "
                    f"(available: {', '.join(sorted(fields))})"
                )
    if problems:
        raise SpecError("invalid spec:\n  " + "\n  ".join(dict.fromkeys(problems)))


def read_source(
    name: str,
    path: str | Path,
    spec: SourceSpec | str,
    **options: Any,
) -> Source:
    """Read a file into a `Source` using the named connector and record its byte digest."""
    if isinstance(spec, str):
        fmt = spec
        merged_options: dict[str, Any] = dict(options)
    else:
        fmt = spec.format
        merged_options = {**spec.options, **options}

    entry = _CONNECTORS.get(fmt)
    if entry is None:
        supported = ", ".join(sorted(_CONNECTORS))
        raise SpecError(f"unknown source format {fmt!r} (expected one of: {supported})")
    parser, _ = entry

    file_path = Path(path)
    try:
        raw_bytes = file_path.read_bytes()
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise ConnectorError(f"cannot read file: {detail}", path=str(file_path)) from exc

    digest = f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}"
    try:
        records, excluded = parser(name, file_path, raw_bytes, merged_options)
    except ValidationError as exc:
        lines = [
            f"{'.'.join(str(p) for p in err['loc']) or 'options'}: {err['msg']}"
            for err in exc.errors()
        ]
        raise SpecError(f"invalid {fmt} options:\n  " + "\n  ".join(lines)) from exc

    return Source(name=name, records=tuple(records), digest=digest, excluded=tuple(excluded))


__all__ = ["read_source", "validate_source_spec", "validate_spec"]
