"""Source connectors: parse external files into canonical `Source` objects."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tieout.connectors.camt import parse_camt
from tieout.connectors.csv import parse_csv
from tieout.connectors.stripe import parse_stripe
from tieout.errors import ConnectorError, SpecError
from tieout.records import Record, Source
from tieout.spec import SourceSpec

_Parser = Callable[[str, Path, bytes, dict[str, Any]], list[Record]]

_PARSERS: dict[str, _Parser] = {
    "camt": parse_camt,
    "csv": parse_csv,
    "stripe": parse_stripe,
}


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

    parser = _PARSERS.get(fmt)
    if parser is None:
        supported = ", ".join(sorted(_PARSERS))
        raise SpecError(f"unknown source format {fmt!r} (expected one of: {supported})")

    file_path = Path(path)
    try:
        raw_bytes = file_path.read_bytes()
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise ConnectorError(f"cannot read file: {detail}", path=str(file_path)) from exc

    digest = f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}"
    try:
        records = parser(name, file_path, raw_bytes, merged_options)
    except ValidationError as exc:
        lines = [
            f"{'.'.join(str(p) for p in err['loc']) or 'options'}: {err['msg']}"
            for err in exc.errors()
        ]
        raise SpecError(f"invalid {fmt} options:\n  " + "\n  ".join(lines)) from exc

    return Source(name=name, records=tuple(records), digest=digest)


__all__ = ["read_source"]
