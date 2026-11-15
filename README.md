# tieout

**An open-source reconciliation engine.** tieout matches money records across bank
statements, payment-provider reports, ledgers and invoices, and explains every match.
Anything it can't match with certainty comes back as a *break* with the reason. It
never guesses.

> Status: pre-alpha. The rule format and Python API may still change before v0.1.

## Why

Every company that moves money has to prove its records agree: bank vs ledger,
Stripe payouts vs orders, invoices vs incoming payments. That work is still mostly
spreadsheets, and every fintech writes its own matcher. The open-source finance stack
has ledgers, payments, billing and AML, but no standalone reconciliation engine.
See [docs/design/PRODUCT.md](docs/design/PRODUCT.md) for the full case.

## What it guarantees

- **Exact money.** Amounts are integer minor units. A value that can't be represented
  exactly (`10.005 USD`) is an error, never a rounding.
- **No guessing.** A pair is matched only when each side is the other's unique best
  candidate. Ties are reported as `ambiguous` breaks, with the candidates listed.
- **Every record is accounted for.** Each input record ends up in exactly one match
  or one break, duplicates included.
- **Every match is explained.** The rule, the amounts, the date gap and the
  references that agreed are all recorded in plain language.
- **Reproducible.** The same inputs, spec and engine version give the same output and
  the same run id. Search limits count steps, not seconds.

## Quick start

```bash
pip install tieout            # not yet published; for now: pip install -e .
tieout run examples/stripe-to-bank/recon.yaml \
  --input stripe=examples/stripe-to-bank/data/stripe_itemized_payouts.csv \
  --input bank=examples/stripe-to-bank/data/bank_statement.camt053.xml
```

A spec declares sources and rules:

```yaml
version: 1
sources:
  stripe: { format: stripe }
  bank:   { format: camt }
rules:
  # All Stripe balance transactions of one payout must add up to one bank credit.
  - id: payout-to-bank
    left: stripe
    right: bank
    kind: many_to_one
    group: { by: payout_id }
    date: { min_days: 0, max_days: 4 }
```

Or from Python:

```python
from tieout import load_spec, reconcile
from tieout.connectors import read_source

spec = load_spec("recon.yaml")
sources = [read_source(name, path, spec.sources[name]) for name, path in inputs.items()]
result = reconcile(sources, spec)

for match in result.matches:
    print(match.rule_id, match.left_ids, match.right_ids, *match.explanation, sep="\n  ")
for item in result.breaks:
    print(item.record_id, item.reason, item.detail)
```

## Rules

| Kind | Meaning |
|---|---|
| `one_to_one` | One left record against one right record |
| `one_to_many` | One left record equals the sum of several right records |
| `many_to_one` | Several left records sum to one right record |

Conditions, all optional:

| Condition | Example | Meaning |
|---|---|---|
| `amount` | `{ tolerance: 50, sign: opposite }` | Totals agree within 50 minor units; `opposite` when the two sources use opposite signs |
| `date` | `{ min_days: 0, max_days: 3 }` | Right date minus left date is 0 to 3 days, for every record pair |
| `references` | `[{ left: invoice_no, right: description, op: left_in_right }]` | Normalised references agree (case and punctuation ignored) |
| `group` | `{ by: payout_id }` or `{ max_size: 4 }` | Group by a shared field, or search for subsets of up to 4 records |

Rules run in `priority` order (lower first). Put exact rules before tolerant ones.

## Connectors

| Format | `format:` | Notes |
|---|---|---|
| Any CSV, with a column mapping | `csv` | Signed amount column or separate inflow/outflow columns; configurable separators and date format |
| ISO 20022 camt.053 / camt.054 | `camt` | Booked entries by default; one record per entry |
| Stripe itemized payout reconciliation report | `stripe` | Net amounts, grouped by payout |

## Project layout

```
src/tieout/
  money.py           exact amounts and currencies
  records.py         the canonical Record and Source
  spec.py            the YAML spec: sources and rules
  conditions.py      what each rule condition means, and how it is explained
  matching.py        candidate search and mutual-best resolution
  subset_search.py   bounded search for many-to-one groups
  engine.py          a reconciliation run, start to finish
  result.py          matches, breaks and run metadata
  report.py          JSON and CSV output
  cli.py             the `tieout` command
  connectors/        one module per input format
tests/               mirrors src/, plus fixtures/
docs/adr/            architecture decisions
examples/            runnable end-to-end examples
```

## Development

```bash
pip install uv
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

## License

Apache-2.0. See [LICENSE](LICENSE).
