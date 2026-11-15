# ADR 0001: Technology stack

- Status: accepted
- Date: 2026-11-15

## Context

tieout matches money records across files from banks, payment providers and ledgers.
The engine itself is small. The long tail is **connectors**: dozens of file formats,
each with quirks, written mostly by finance and data engineers in the community.
Money must be exact, runs must be reproducible, and the library must embed in other
people's stacks without dragging in a server or a database.

## Decision

| Concern | Choice | Why | Rejected |
|---|---|---|---|
| Language | Python ≥ 3.11 | Connector authors (finance and data engineers) know it; notebooks and data tooling live here. 3.11 gives `StrEnum`, `tomllib`, faster CPython. | **Go**: single binary, matches Formance/Blnk, but shrinks the contributor pool for connectors, which are most of the work. **Rust**: same problem, harder still. |
| Matching compute | Plain Python with hash and sorted indexes | Matching isn't a relational join. Candidate generation is cheap; the hard parts (mutual-best resolution, bounded subset search, explanations) are procedural. Plain code is easier to read, test and explain. | **DuckDB / Polars**: fast for candidate joins, but split the logic between SQL/expressions and Python. Revisit only if benchmarks miss the target; the public API won't change. |
| Money | `int` minor units; `decimal.Decimal` only while parsing | Exact by construction. Floats are never used. | `Decimal` everywhere (slower, and exponent drift), money libraries (extra dependency for ~60 lines of code) |
| Config and rule validation | Pydantic v2 | Readable error messages for users' YAML, `extra="forbid"` catches typos, JSON Schema export for non-Python tools | msgspec (faster, but error messages are less friendly and config is not a hot path) |
| YAML | PyYAML `SafeLoader`, subclassed to **reject duplicate keys** | Ubiquitous, C-accelerated. A duplicate rule key silently overwriting another is a real config bug, so we refuse it. | ruamel.yaml (YAML 1.2, but a heavier and quirkier API; Pydantic already rejects YAML 1.1 bool coercions on string fields) |
| XML (ISO 20022 camt) | stdlib ElementTree via `defusedxml` | Bank files are untrusted input. Modern expat is already hardened; defusedxml adds defense in depth and keeps security scanners quiet. | lxml (faster, has line numbers, but a heavy native dependency for files that are megabytes, not gigabytes) |
| CSV | stdlib `csv` | Amounts must be parsed from exact strings; dataframe readers infer floats | pandas / polars readers |
| Time zones | stdlib `zoneinfo`, plus `tzdata` on Windows | Settlement timestamps (Stripe) must become dates in the merchant's time zone | pytz (legacy) |
| CLI | click | Mature, explicit, testable with `CliRunner`, one small dependency | Typer (pulls in rich and shellingham), argparse (verbose) |
| Tests | pytest + Hypothesis | Property tests express the engine's invariants directly | — |
| Lint / types | ruff, mypy `--strict` | Fast, a single tool for lint and import sorting; strict typing on a money engine | — |
| Packaging | hatchling, `src/` layout, uv for development | Standard, no setup.py; the `src/` layout stops tests from importing the working tree by accident | poetry (non-standard metadata) |
| Later: HTTP server | FastAPI (optional extra) | Shares Pydantic models with the core | — |
| Later: MCP server | official `mcp` Python SDK (optional extra) | Reference implementation | — |

Runtime dependencies are kept to four: `click`, `defusedxml`, `pydantic`, `pyyaml`
(plus `tzdata` on Windows). Every new runtime dependency needs a line in this table.

## Consequences

- Performance is the main risk of plain Python. A benchmark at 100k and 1M records
  lives in `benchmarks/`. If it misses the target, candidate generation (not the
  resolution logic) is the part to move to Polars or DuckDB.
- Connectors stay plain Python functions with no framework to learn.
