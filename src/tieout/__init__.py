"""tieout: an open-source reconciliation engine.

from tieout import load_spec, reconcile
from tieout.connectors import read_source

spec = load_spec("recon.yaml")
bank = read_source("bank", "statement.xml", spec.sources["bank"])
ledger = read_source("ledger", "ledger.csv", spec.sources["ledger"])
result = reconcile([bank, ledger], spec)
"""

from tieout.engine import ENGINE_VERSION as __version__
from tieout.engine import reconcile
from tieout.errors import ConnectorError, InputError, MoneyError, SpecError, TieoutError
from tieout.records import Record, Source
from tieout.result import Break, BreakReason, Match, Result, RunInfo
from tieout.spec import Rule, RuleKind, Spec, load_spec, parse_spec

__all__ = [
    "Break",
    "BreakReason",
    "ConnectorError",
    "InputError",
    "Match",
    "MoneyError",
    "Record",
    "Result",
    "Rule",
    "RuleKind",
    "RunInfo",
    "Source",
    "Spec",
    "SpecError",
    "TieoutError",
    "__version__",
    "load_spec",
    "parse_spec",
    "reconcile",
]
