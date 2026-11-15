"""Command-line interface for tieout."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from tieout.connectors import read_source, validate_source_spec
from tieout.engine import ENGINE_VERSION, reconcile
from tieout.errors import TieoutError
from tieout.report import format_summary, to_json, write_breaks_csv, write_matches_csv
from tieout.spec import load_spec


class _CliError(click.ClickException):
    """Configuration or input error; exits with code 2 to stay distinct from breaks (1)."""

    exit_code = 2


@click.group()
@click.version_option(ENGINE_VERSION, prog_name="tieout")
def main() -> None:
    """Reconcile money records across banks, payment providers, ledgers, and invoices."""


@main.command()
@click.argument("spec_path", type=click.Path(dir_okay=False, path_type=Path))
def validate(spec_path: Path) -> None:
    """Validate a YAML reconciliation spec and its source options."""
    try:
        spec = load_spec(spec_path)
        for source_spec in spec.sources.values():
            validate_source_spec(source_spec)
    except TieoutError as exc:
        raise _CliError(str(exc)) from exc

    click.echo(
        f"Valid spec: {len(spec.sources)} source(s), {len(spec.rules)} rule(s) ({spec.digest()})"
    )


@main.command()
@click.argument("spec_path", type=click.Path(dir_okay=False, path_type=Path))
@click.option(
    "--input",
    "-i",
    "inputs",
    multiple=True,
    required=True,
    metavar="NAME=PATH",
    help="Bind a declared source name to an input file (repeat for each source).",
)
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Write result.json, matches.csv, and breaks.csv into this directory.",
)
@click.option("--json", "as_json", is_flag=True, help="Print full result JSON to stdout.")
@click.option(
    "--fail-on-breaks",
    is_flag=True,
    help="Exit with status 1 if any breaks remain after running all rules.",
)
def run(
    spec_path: Path,
    inputs: tuple[str, ...],
    output_dir: Path | None,
    as_json: bool,
    fail_on_breaks: bool,
) -> None:
    """Run a reconciliation spec against input files."""
    bindings = _parse_inputs(inputs)
    try:
        spec = load_spec(spec_path)
        for name in bindings:
            if name not in spec.sources:
                declared = ", ".join(sorted(spec.sources)) or "(none)"
                raise _CliError(
                    f"input {name!r} is not declared in spec.sources (declared: {declared})"
                )
        sources = [read_source(name, path, spec.sources[name]) for name, path in bindings.items()]
        result = reconcile(sources, spec)
    except TieoutError as exc:
        raise _CliError(str(exc)) from exc

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "result.json").write_text(to_json(result) + "\n", encoding="utf-8")
        write_matches_csv(result, output_dir / "matches.csv")
        write_breaks_csv(result, output_dir / "breaks.csv")

    if as_json:
        click.echo(to_json(result))
        if output_dir is not None:
            click.echo(f"Wrote reports to {output_dir}", err=True)
    else:
        click.echo(format_summary(result))
        if output_dir is not None:
            click.echo(f"\nWrote reports to {output_dir}")

    if fail_on_breaks and result.breaks:
        sys.exit(1)


def _parse_inputs(raw_inputs: tuple[str, ...]) -> dict[str, Path]:
    bindings: dict[str, Path] = {}
    for item in raw_inputs:
        name, sep, path_str = item.partition("=")
        name = name.strip()
        path_str = path_str.strip()
        if not sep or not name or not path_str:
            raise _CliError(f"invalid --input {item!r}: expected NAME=PATH")
        if name in bindings:
            raise _CliError(f"duplicate --input for source {name!r}")
        bindings[name] = Path(path_str)
    return bindings
