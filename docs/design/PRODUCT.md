# Open Reconciliation Engine — Project Document

**An open-source, embeddable engine that matches money records across banks, payment providers, ledgers and invoices, with every match explained.**

| | |
|---|---|
| Status | Pre-code. Direction decided 13 Nov 2026. |
| Name | TBD. Check GitHub, PyPI, npm and trademarks before choosing. |
| Type | Open-source framework (Apache-2.0). Not a startup or a hosted product. |
| Built for | Engineers at fintechs, marketplaces, payment providers, lenders and ERP/accounting vendors; finance and data engineers; builders of AI bookkeeping agents |
| One-line bet | Every company that moves money has to reconcile. The open-source finance stack already has ledgers, payments, billing, AML and e-invoicing. It has no standalone reconciliation engine. |
| History | Replaces the InvoiceAgent startup plan of 12 Nov 2026 (kept in a private archive). [RESEARCH.md](RESEARCH.md) explains why the direction changed. |

---

## 0. TL;DR

- **Problem.** Reconciliation means proving that records which should agree actually do: bank statement vs ledger, payment-provider payouts vs orders, invoices vs incoming payments. It is still mostly spreadsheets.
  - 27% of finance teams rely primarily on spreadsheets for reconciliations (CFO.com 2026 CFO Report).
  - Cash reconciliation takes 20–50 hours a month (Ledge benchmark, 2025).
  - Cross-system reconciliation is the #1 manual task finance teams want to eliminate (Leapfin, 2025).
- **Gap.** Fintech engineering teams rebuild reconciliation in-house again and again. There is no standalone open-source option that people widely use:
  - Formance keeps its ledger open (MIT) but puts reconciliation in its paid Enterprise Edition.
  - Blnk's reconciliation only works inside the Blnk ledger.
  - The best standalone project, Reconify, has about 10 GitHub stars.
- **What we build.**
  - A deterministic matching core: one-to-one, one-to-many and many-to-many matches, with tolerances for fees, FX and timing, and an explanation for every match.
  - Community-maintained connectors for bank statement formats (ISO 20022 camt.053/054, MT940, BAI2), payment-provider settlement reports, ledgers and ERPs.
  - An optional AI assistant that only *proposes* matches for leftover exceptions. A human confirms.
  - Several ways in: a Python library, a CLI, an HTTP server, and an MCP server for AI agents.
- **Why it can spread widely.** It's useful to one team on day one. It needs no network of participants, no licence and no partner deal. People adopt it one reconciliation at a time, and the community grows it by adding connectors.
- **Later modules** carry over the InvoiceAgent work: cash application (invoice ↔ payment matching), duplicate and double-financing detection, and India connectors (Tally, UPI, Razorpay, TReDS settlements).

---

## 1. Problem

### 1.1 What reconciliation is
Reconciliation takes records from two or more sources that describe the same money and checks they agree. Anything that doesn't match gets explained or fixed. Common forms:

| Reconciliation | Left side | Right side | Typical difficulty |
|---|---|---|---|
| Bank-to-ledger | Bank statement lines | Ledger/GL entries | Timing, bank references truncated, batched deposits |
| Payment-provider settlement | Orders / charges | Stripe/Adyen/Razorpay payouts | One payout covers hundreds of charges, minus fees, refunds and chargebacks |
| Cash application (invoice-to-cash) | Open invoices | Incoming payments | Partial payments, one payment for many invoices, missing remittance info |
| Marketplace payouts | Seller balances | Split payouts | Many-to-many, reserves, holdbacks |
| Intercompany | Entity A's books | Entity B's books | FX, timing, different charts of accounts |
| Payables | Bills | Outgoing payments | Duplicates, partial payments |

### 1.2 Why every team rebuilds it
Simple matching is easy. Reconciliation is hard because of the edge cases:
- one payout covering many charges, with fees netted out
- partial payments, overpayments and refunds
- chargebacks and reversals arriving weeks later
- FX conversion and rounding
- value date vs booking date vs timezone
- references that banks truncate or mangle
- duplicate files and re-sent statements
- amounts that match by coincidence

Every fintech hits these, writes its own matcher, and maintains it forever. Finance teams without engineers fall back to Excel or buy expensive closed tools (BlackLine, Ledge, Numeric, Modern Treasury and others).

### 1.3 Why now
- **Bank data is becoming structured.** SWIFT's move to ISO 20022 makes camt.053/054 bank statements the norm, so a good open camt parser is broadly useful.
- **AI bookkeeping is exploding, and it needs a deterministic engine underneath.** An LLM that "reconciles" by itself will invent matches. Agents need a tool that matches exactly and explains why.
- **The rest of the open stack has matured.** Teams already self-host TigerBeetle, Formance, Blnk, Hyperswitch and Lago. Reconciliation is the piece they still have to buy or build.

---

## 2. The gap (details and sources in [RESEARCH.md](RESEARCH.md))

| Layer of the open-source finance stack | Projects people use today | Status |
|---|---|---|
| Ledger | TigerBeetle, Formance, Blnk | Covered |
| Payments orchestration | Hyperswitch (Juspay) | Covered |
| Billing | Lago, Kill Bill | Covered |
| AML / fraud monitoring | Marble, Tazama | Covered |
| Sanctions screening | OpenSanctions | Covered |
| E-invoicing formats | GOBL | Covered |
| **Reconciliation** | Formance (Enterprise-only), Blnk (inside its ledger only), Reconify (~10 stars) | **Open gap** |

---

## 3. Users and use cases

| Persona | What they need | How they'll use it |
|---|---|---|
| **Fintech / marketplace engineer** | "Stop maintaining our home-grown matcher." | Python library or HTTP server inside their stack |
| **Finance / data engineer** | "Reconcile our bank and payment-provider data without Excel." | CLI and YAML rules, run on a schedule |
| **Ledger / ERP integrator** | "Offer reconciliation on top of Formance/Blnk/TigerBeetle/ERPNext." | Ledger connectors |
| **AI bookkeeping agent builder** | "Give my agent a reconciliation tool it can't hallucinate with." | MCP server |
| **Lender / factoring company** | "Match collections to receivables, and catch duplicates." | Cash-application and duplicate-detection modules (later) |

---

## 4. Design principles

1. **Deterministic core, AI only at the edges.** Rules decide matches. AI may only *propose* matches for exceptions, and a human confirms them. No AI output changes a result on its own.
2. **Every match is explainable.** Each match records which rule matched, which conditions passed, and any remaining difference (for example "₹12.40 fee, within tolerance").
3. **Runs are reproducible.** The same inputs, rules and engine version always give the same output. Input files are hashed and the rule-set version is recorded.
4. **Money is exact.** Amounts are integer minor units or Decimals, never floats. Currency travels with every amount. Rounding is explicit.
5. **Embeddable, not a platform.** Library first. No required server, database, ledger or cloud account.
6. **Connectors are first-class.** Each connector ships with anonymised fixture files and tests. Contributing a connector should take a day, not a week.
7. **Local and private by default.** No telemetry. AI is optional and pluggable, and local models are supported.
8. **Append-only audit trail.** Every run and every human decision (confirm, reject, override) becomes an event with an actor and a timestamp.
9. **Ambiguity is an exception, never a guess.** If two candidate matches are equally good, the engine reports both and asks.

---

## 5. Scope

### 5.1 In scope for v1.0
- Matching core: 1:1, 1:N, N:1, N:M (bounded); exact, tolerant and grouped matching; deduplication within a source.
- YAML rule format with priorities and tolerances.
- Connectors: bank formats, the major payment providers, the main open-source ledgers, file formats (CSV/XLSX/OFX).
- Outputs: match report, exceptions file, an event log, and JSON/CSV/Parquet exports.
- Interfaces: Python library, CLI, HTTP server, MCP server.
- An optional AI exception assistant with a pluggable model.
- A minimal review UI for exceptions (confirm/reject). It may be a separate package.

### 5.2 Non-goals
- **Not a ledger or general ledger.** Integrate with ledgers; don't replace them.
- **Not an ERP or close-management suite.** No task lists, close checklists or approval chains beyond confirming matches.
- **Not bank connectivity.** We parse files and exports. We don't store bank credentials or act as an aggregator.
- **Not a payments system.** We never move money.
- **No autonomous AI decisions.**

---

## 6. System design

### 6.1 Architecture

```
  Sources: bank files (camt.053, MT940, BAI2, CSV) · PSP reports (Stripe, Adyen, PayPal, Razorpay)
           ledgers (Formance, Blnk, TigerBeetle, Beancount) · ERPs (Tally, ERPNext, QuickBooks, Xero)
                                         │
                                         ▼
                         ┌──────────────────────────────┐
                         │ CONNECTORS                    │  parse → raw rows (kept, with file+line pointer)
                         └──────────────┬───────────────┘
                                        ▼
                         ┌──────────────────────────────┐
                         │ NORMALISE                     │  → canonical Record: minor units, currency,
                         │                               │    sign convention, dates/timezones, references
                         └──────────────┬───────────────┘
                                        ▼
                         ┌──────────────────────────────┐
                         │ MATCHING CORE (deterministic) │  dedupe → candidate generation → rules by
                         │                               │  priority → bounded grouping → explain
                         └──────┬───────────────┬────────┘
                                ▼               ▼
                         Matches (explained)   Exceptions ──► AI assistant (optional, proposes only)
                                │               │                        │
                                ▼               ▼                        ▼
                         ┌──────────────────────────────────────────────────┐
                         │ REVIEW: human confirms / rejects → events          │
                         └──────────────────────┬───────────────────────────┘
                                                ▼
                   Outputs: reports · exports · append-only event log
                   Interfaces: Python library · CLI · HTTP server · MCP server
```

### 6.2 Canonical data model (draft)

```
Source     { id, kind: bank|psp|ledger|erp|file, connector, config_ref }
Record     { id, source_id, external_id, amount_minor, currency, direction: debit|credit,
             value_date, booking_date?, counterparty?, references{...}, metadata{...},
             raw_ref (file + line/offset), fingerprint }
RuleSet    { id, version, rules[] }
Rule       { id, left_source, right_source, kind: 1:1|1:N|N:1|N:M, conditions[], tolerances{},
             priority, max_group_size?, window_days? }
Run        { id, ruleset_version, engine_version, input_hashes[], started_at, stats }
Match      { id, run_id, left_ids[], right_ids[], rule_id, residual{amount_minor, reason},
             explanation[], status: auto|proposed|confirmed|rejected, decided_by?, decided_at? }
Exception  { id, run_id, record_ids[], reason: unmatched|ambiguous|tolerance_exceeded|duplicate,
             candidates[] }
Event      { seq, run_id, type, actor, payload, ts }        # append-only
```

### 6.3 Matching pipeline
1. **Ingest.** The connector produces raw rows, each keeping a pointer to its source file and line.
2. **Normalise** into `Record`: minor units, currency, sign convention, value/booking dates in a set timezone, and extracted reference tokens.
3. **Deduplicate within each source** using fingerprints, which catches re-sent files and duplicate lines.
4. **Generate candidates** by blocking on currency, amount buckets, date windows and reference tokens, so the engine doesn't compare everything with everything.
5. **Evaluate rules in priority order:** exact matches first, then tolerant ones, then grouped ones. A record matched by a higher-priority rule is removed from later rules.
6. **Search for groups** (1:N, N:M). This is a subset-sum problem, which can explode, so it is always bounded by maximum group size, date window and a time budget. Several valid groupings → **ambiguous exception**, never a guess.
7. **Explain.** Every match stores the conditions that passed and any residual difference.
8. **Handle exceptions.** Optionally, the AI assistant proposes matches, and then a human reviews them.
9. **Output.** Reports, exports and events. Later: suggested journal entries for ledgers.

### 6.4 Rule format (illustrative)

```yaml
version: 3
rules:
  - id: payout-to-bank
    left: stripe_payouts
    right: bank_statement
    kind: one_to_one
    priority: 10
    conditions:
      amount: exact
      currency: exact
      date: { right_within_days: [0, 3] }
      reference: { right_contains: left.payout_id }

  - id: charges-to-payout
    left: orders
    right: stripe_payouts
    kind: many_to_one
    priority: 20
    group_by: left.payout_id
    conditions:
      amount: { sum_left_minus: left.fee, equals_right: true }
    tolerances:
      amount_minor: 1          # rounding
```

### 6.5 AI exception assistant (optional)
- **Input:** unmatched or ambiguous records, plus the nearby candidates the engine already found.
- **Output:** *proposals* only, each with a plain-language reason ("Invoice #1042 likely paid by this ₹48,000 transfer; remitter name matches buyer; ₹2,000 short, possibly TDS").
- **Guarantees:**
  - Proposals never change a result until a human confirms them.
  - Every number shown comes from the records, never from the model.
  - Every proposal and decision is logged.
- **Model:** pluggable. Local models work, and the engine is fully usable with AI turned off.
- **Evaluation:** proposal precision and recall on labelled exception sets, run on every model or prompt change.

### 6.6 Interfaces
- **Python library:** `reconcile(sources, rules) -> Result`
- **CLI:** `recon run --rules rules.yaml --left bank.camt053.xml --right ledger.csv`
- **HTTP server:** runs and results over REST, for non-Python stacks.
- **MCP server:** read-only tools (run, list exceptions, explain a match) plus a *propose* tool. Confirming is restricted to humans by default.

### 6.7 Technology choices (initial; revisit after the week-1 prototype)

| Decision | Initial choice | Why | Alternative considered |
|---|---|---|---|
| Core language | **Python 3.11+** | The people who write connectors (finance and data engineers) mostly know Python. It also has the best data-tooling ecosystem. | Go: matches Formance, Blnk and Reconify and ships as a single binary. Decide in week 1 by benchmarking. |
| Compute | **DuckDB + Apache Arrow**, in-process | Fast joins and windowing on millions of rows with no server, and rules can compile to SQL | Pandas or Polars only |
| Money | Integer minor units and `Decimal` | Exactness | — |
| Schemas | Pydantic models with exported JSON Schema | Validation, plus language-neutral contracts | — |
| Server mode | FastAPI plus Postgres (optional) | Familiar and easy to self-host | — |
| Packaging | `pip install`, Docker image | Lowest friction | — |
| Performance target | 1M × 1M records reconciled in minutes on a laptop | Mid-size companies shouldn't need a cluster | — |

---

## 7. Connector roadmap

| Priority | Connector | Why first |
|---|---|---|
| v0.1 | Generic CSV/XLSX (with a mapping file) | Covers most cases on day one |
| v0.1 | ISO 20022 camt.053 / camt.054 | The new global bank-statement standard |
| v0.1 | Stripe balance transactions and payouts | The most common payment-provider case |
| v0.2 | MT940, BAI2, OFX | Older bank formats still in wide use |
| v0.2 | Adyen, PayPal settlement reports | Large payment-provider share |
| v0.2 | Formance, Blnk, TigerBeetle, Beancount | Pairs with the open-source ledger community |
| v0.3 | Razorpay, Cashfree, UPI settlement files, Tally, ERPNext | India community; carries over from InvoiceAgent |
| v0.3 | QuickBooks, Xero exports | SMB accounting |
| Later | TReDS settlement files, GST e-invoice data (for cash application) | Receivables use cases |

**Connector contract:** a parser, a mapping to `Record`, anonymised fixture files, and golden-output tests. A template repository and a guide make contributing a connector a one-day task.

---

## 8. Quality and evaluation
- **Golden datasets:** synthetic and anonymised real-world-shaped files for each connector and each reconciliation type, with expected matches.
- **Property tests (invariants):**
  - no record is matched twice unless a rule allows it
  - matched plus unmatched amounts equal the input totals
  - runs are deterministic
  - ambiguity always produces an exception
- **Performance benchmarks** in CI at 10k, 100k and 1M records.
- **AI assistant evals:** precision of proposals, and checks that no hallucinated numbers appear. They run on every prompt or model change.
- **Fuzzing** of all parsers, since malformed bank files are common.

---

## 9. Roadmap

| Milestone | Target | Contents |
|---|---|---|
| Week 1 | Prototype | Language and compute benchmark (Python+DuckDB vs Go) on 1M×1M synthetic records. Freeze the `Record` schema draft. |
| **v0.1** | Weeks 6–8 | Core 1:1 and 1:N matching, YAML rules, explanations, CSV + camt.053 + Stripe connectors, CLI and Python library, docs site with 3 cookbook recipes |
| v0.2 | Month 4 | N:M bounded grouping, MT940/BAI2/OFX, Adyen/PayPal, ledger connectors, HTTP server, event log, connector template and guide |
| v0.3 | Month 6 | MCP server, AI exception assistant, minimal review UI, India connectors |
| v1.0 | Month 9–12 | Stable schemas and rule format, cash-application module, performance targets met, security review |
| Later | — | Duplicate and double-financing detection module, suggested journal entries, more ERPs |

---

## 10. Adoption strategy
1. **Docs-first.** Every connector comes with a 10-minute recipe ("Reconcile Stripe payouts against your bank in 10 minutes").
2. **Pair with the open finance stack.** Build integrations and joint examples with Blnk, Formance, TigerBeetle, Hyperswitch and Lago. Their users already self-host, and reconciliation is what they lack.
3. **The AI agent channel.** Publish the MCP server to MCP registries, positioned as the deterministic reconciliation tool for bookkeeping agents.
4. **Developer channels:** launch posts on Hacker News and r/fintech, plus fintech engineering blogs ("how we replaced our home-grown matcher").
5. **India community.** Tally, UPI and Razorpay connectors reach an under-served developer base and build on the InvoiceAgent research.
6. **Make contributing connectors easy.** Label good first issues by format, maintain a list of formats people want, and credit contributors in release notes.

---

## 11. Governance and sustainability
- **Licence:** Apache-2.0, which includes a patent grant and is friendly to company adoption.
- **Contributions:** DCO sign-off, CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md (financial data means security reports must have a private channel).
- **Maintainers:** a public roadmap and transparent decisions (ADRs in `docs/adr/`).
- **Later:** consider a neutral home such as FINOS once there are multiple corporate users.
- **Sustainability:** GitHub Sponsors, grants, and corporate sponsorship of specific connectors. Making money is not a goal, but maintainer time has to be funded somehow.

---

## 12. Success metrics and checkpoints

**North star:** organisations running it in production.

| Checkpoint | Signal of success | If it fails |
|---|---|---|
| Month 3 (after v0.1) | 10+ people outside the project try it and file issues; 1 external connector PR | Interview the people who tried it; fix onboarding or rethink the core abstraction |
| Month 6 | 3+ external organisations in production; 5+ connectors contributed from outside | Narrow to the strongest use case (likely payment-provider settlement) |
| Month 12 | 10+ production users; a second regular maintainer; at least one integration with an open ledger project | Reassess: merge into an existing project or hand over maintenance |

Stars are tracked but not used as a success measure.

---

## 13. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Formance (or another funded company) open-sources its reconciliation | Medium | High | Stay ledger-agnostic and connector-rich. Partner rather than compete. Move fast. |
| Edge cases make the core too complex | High | Medium | Bound grouping strictly. Report ambiguity as an exception. Build the invariant test suite early. |
| Connector maintenance burden | High | Medium | Fixture-based tests, a connector template, community ownership with a named maintainer per connector |
| Wrong match causes accounting errors | Medium | High | Determinism, explanations, invariants, human confirmation for AI proposals, clear "no warranty" docs |
| Low contributor uptake | Medium | High | Docs-first, recipes, good first issues, integrations with existing communities |
| Sensitive financial data in bug reports | Medium | Medium | Anonymisation tool for fixtures, a private security channel, no telemetry |
| Maintainer burnout (solo start) | High | High | Recruit a second maintainer by month 12. Keep scope tight. Seek sponsorship. |

---

## 14. Open questions
1. Python+DuckDB or Go for the core? (Decide by the week-1 benchmark and how likely contributors are to know each.)
2. Rule format: YAML only, or YAML that compiles to SQL with a SQL escape hatch for power users?
3. How should 1:N/N:M grouping be bounded by default (max group size, window, time budget)?
4. Where do confirmed decisions persist in library mode: a local SQLite/DuckDB file, or something the user supplies?
5. Which payment providers publish settlement-report formats clear enough to support without an account? Which need sample files from the community?
6. Is there appetite at Blnk, Formance or TigerBeetle for an official integration or co-maintenance?
7. What anonymised real-world sample files can we obtain legally for golden tests?

---

## 15. What carries over from InvoiceAgent
- **Principles:** deterministic core with a conversational shell; every number from a tool, never the model; append-only event log; evals from week 1.
- **Cash application:** matching invoices to payments is the heart of invoice-to-cash, and it becomes a core module.
- **Duplicate and double-financing detection:** the same fingerprinting and matching core, extended across parties later. See RESEARCH.md for why a standalone registry was not chosen first.
- **India connectors:** Tally, UPI, Razorpay and TReDS settlements.
- **Finternet:** tokenised assets will still need reconciling to off-chain records. It's a possible future integration, not a dependency.
