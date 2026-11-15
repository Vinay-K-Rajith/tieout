# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). Before 1.0, minor versions may change
the rule format and the Python API.

## [0.1.0] - 2026-11-15

First public release.

### Added

- Matching engine with `one_to_one`, `one_to_many` and `many_to_one` rules:
  amount tolerance and sign conventions, date windows on value or booking date,
  normalised reference checks, and grouping by shared key or bounded subset search.
- Connectors for generic CSV, ISO 20022 camt.053 / camt.054, and Stripe itemized
  payout reconciliation reports.
- `tieout validate` and `tieout run` commands with a terminal summary, JSON and
  CSV reports, and `--fail-on-breaks` for CI use.
- YAML specs validated strictly: unknown keys, duplicate keys, impossible date
  windows and rule fields a source cannot produce are rejected before any input
  is read.

### Guarantees

- **Exact money.** Integer minor units, scaled by the ISO 4217 exponent
  (list of 2026-09-17). Unknown currencies, unrepresentable amounts (`10.005 USD`),
  malformed digit grouping and non-ASCII digits are errors, never roundings.
  `register_currency()` declares private or crypto codes.
- **No guessing.** Pairs match only as mutual unique best candidates; ties become
  `ambiguous` breaks with their candidates. A subset-search group is refused when
  another target could also claim one of its members.
- **Every record accounted for.** Each record is in exactly one match or one break,
  and rows a connector filters out are listed with their location. The invariants
  are re-checked at the end of every run.
- **Reproducible.** Record ids come from natural keys or content, so shuffled input
  rows give the same matches, breaks and run id. Raw-file SHA-256 digests are kept
  in the run log. Search limits count steps, not seconds.
- **Fail-closed parsing.** Hostile XML, duplicate, missing or misaligned CSV
  columns, unbalanced quotes, and camt statements whose balances or `TxsSummry`
  totals don't add up are rejected with the file and line or entry. A missing
  value date is never filled in from the booking date.
- **Correct dates and signs.** Stripe `*_utc` columns are preferred, other columns
  need `report_timezone`; camt reversals follow ISO 20022 (`CdtDbtInd` is the
  entry's own direction), with `reversal_sign: flipped` for banks that differ.
- **Explained and safe output.** Every match states its rule, totals, residual,
  date gap and the reference values that agreed. CSV reports neutralise
  spreadsheet formulas in text cells.

[0.1.0]: https://github.com/Vinay-K-Rajith/tieout/releases/tag/v0.1.0
