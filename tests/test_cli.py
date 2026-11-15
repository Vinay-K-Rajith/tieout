"""Tests for the tieout CLI and CSV/JSON reporting."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from click.testing import CliRunner

from tieout.cli import main

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DIR = REPO_ROOT / "examples" / "stripe-to-bank"


def test_cli_validate_valid_and_invalid(tmp_path: Path) -> None:
    runner = CliRunner()

    ok = runner.invoke(main, ["validate", str(EXAMPLE_DIR / "recon.yaml")])
    assert ok.exit_code == 0
    assert "Valid spec: 2 source(s), 1 rule(s)" in ok.output

    bad_opts = tmp_path / "bad_opts.yaml"
    bad_opts.write_text(
        """
version: 1
sources:
  a: { format: stripe, options: { timezone: Mars/Olympus } }
  b: { format: camt }
rules:
  - id: r1
    left: a
    right: b
""",
        encoding="utf-8",
    )
    bad = runner.invoke(main, ["validate", str(bad_opts)])
    assert bad.exit_code == 2
    assert "unknown timezone" in bad.output


def test_cli_run_example_summary_json_and_outputs(tmp_path: Path) -> None:
    runner = CliRunner()
    spec_path = EXAMPLE_DIR / "recon.yaml"
    stripe_path = EXAMPLE_DIR / "data" / "stripe_itemized_payouts.csv"
    bank_path = EXAMPLE_DIR / "data" / "bank_statement.camt053.xml"

    # Plain summary output
    summary_run = runner.invoke(
        main,
        [
            "run",
            str(spec_path),
            "--input",
            f"stripe={stripe_path}",
            "--input",
            f"bank={bank_path}",
        ],
    )
    assert summary_run.exit_code == 0
    assert "7/8 records matched" in summary_run.output
    assert "2 match(es); 1 break(s)" in summary_run.output
    assert "bank:BANK-20261105-FEE" in summary_run.output
    assert "entry 3" in summary_run.output

    # JSON output + CSV/JSON artifact directory
    out_dir = tmp_path / "out"
    json_run = runner.invoke(
        main,
        [
            "run",
            str(spec_path),
            "-i",
            f"stripe={stripe_path}",
            "-i",
            f"bank={bank_path}",
            "--json",
            "--output-dir",
            str(out_dir),
        ],
    )
    assert json_run.exit_code == 0
    assert "Wrote reports to" in json_run.stderr
    payload = json.loads(json_run.stdout)
    assert payload["summary"]["matches"] == 2
    assert payload["summary"]["breaks"] == 1
    assert payload["summary"]["matched_records"] == 7
    assert payload["matches"][0]["difference"] == "0.00"
    assert payload["breaks"][0]["record_id"] == "bank:BANK-20261105-FEE"
    assert payload["breaks"][0]["location"] == "entry 3"
    assert payload["breaks"][0]["amount"] == "-25.00"

    assert (out_dir / "result.json").exists()
    with (out_dir / "matches.csv").open(encoding="utf-8", newline="") as handle:
        matches_rows = list(csv.DictReader(handle))
    assert len(matches_rows) == 2
    assert {row["right_ids"] for row in matches_rows} == {
        "bank:BANK-20261103-01",
        "bank:BANK-20261104-01",
    }
    assert {row["right_amount"] for row in matches_rows} == {"365.40", "184.10"}

    with (out_dir / "breaks.csv").open(encoding="utf-8", newline="") as handle:
        breaks_rows = list(csv.DictReader(handle))
    assert len(breaks_rows) == 1
    assert breaks_rows[0]["record_id"] == "bank:BANK-20261105-FEE"
    assert breaks_rows[0]["location"] == "entry 3"
    assert breaks_rows[0]["reason"] == "unmatched"
    assert breaks_rows[0]["amount"] == "-25.00"

    # --fail-on-breaks exits with 1 when breaks exist
    failed = runner.invoke(
        main,
        [
            "run",
            str(spec_path),
            "-i",
            f"stripe={stripe_path}",
            "-i",
            f"bank={bank_path}",
            "--fail-on-breaks",
        ],
    )
    assert failed.exit_code == 1


def test_cli_fail_on_breaks_clean_and_error_exit_codes(tmp_path: Path) -> None:
    runner = CliRunner()
    spec_path = tmp_path / "recon.yaml"
    spec_path.write_text(
        """
version: 1
sources:
  left:  { format: csv, options: { amount_column: amt, currency: USD } }
  right: { format: csv, options: { amount_column: amt, currency: USD } }
rules:
  - id: exact
    left: left
    right: right
""",
        encoding="utf-8",
    )
    left_csv = tmp_path / "left.csv"
    right_csv = tmp_path / "right.csv"
    left_csv.write_text("date,amt\n2026-11-01,10.00\n", encoding="utf-8")
    right_csv.write_text("date,amt\n2026-11-01,10.00\n", encoding="utf-8")

    clean = runner.invoke(
        main,
        [
            "run",
            str(spec_path),
            "-i",
            f"left={left_csv}",
            "-i",
            f"right={right_csv}",
            "--fail-on-breaks",
        ],
    )
    assert clean.exit_code == 0

    bad_arg = runner.invoke(main, ["run", str(spec_path), "-i", "not-a-pair"])
    assert bad_arg.exit_code == 2

    dup_input = runner.invoke(
        main,
        ["run", str(spec_path), "-i", f"left={left_csv}", "-i", f"left={right_csv}"],
    )
    assert dup_input.exit_code == 2

    unknown_src = runner.invoke(
        main,
        ["run", str(spec_path), "-i", f"typo={left_csv}", "-i", f"right={right_csv}"],
    )
    assert unknown_src.exit_code == 2
    assert "not declared in spec.sources" in unknown_src.output

    missing_file = runner.invoke(
        main,
        ["run", str(spec_path), "-i", f"left={tmp_path / 'nope.csv'}", "-i", f"right={right_csv}"],
    )
    assert missing_file.exit_code == 2
