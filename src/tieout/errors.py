"""Exception hierarchy.

Every error tieout raises on purpose derives from `TieoutError`, so callers can
separate bad input (which they should report to a user) from programming bugs.
"""

from __future__ import annotations


class TieoutError(Exception):
    """Base class for all errors raised deliberately by tieout."""


class MoneyError(TieoutError):
    """An amount or currency could not be represented exactly."""


class SpecError(TieoutError):
    """A reconciliation spec (sources and rules) is invalid."""


class InputError(TieoutError):
    """Records handed to the engine are inconsistent (duplicate ids, unknown sources...)."""


class InvariantError(TieoutError):
    """The engine produced a result that breaks an accounting invariant.

    This is a bug in tieout, never a property of the input. The run is aborted
    rather than reporting numbers that cannot be right.
    """


class ConnectorError(TieoutError):
    """A source file could not be parsed. Carries the location of the problem."""

    def __init__(self, message: str, *, path: str, location: str | None = None) -> None:
        self.path = path
        self.location = location
        where = f"{path} ({location})" if location else path
        super().__init__(f"{where}: {message}")
