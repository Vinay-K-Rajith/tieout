"""ISO 20022 camt.053 (bank statement) and camt.054 (debit/credit notification) connector.

Signs. ``<Amt>`` is unsigned and ``<CdtDbtInd>`` gives the direction of *this*
entry. ``<RvslInd>true</RvslInd>`` marks an entry that reverses an earlier one;
per ISO 20022 its ``CdtDbtInd`` already shows the reversal's own direction (a
CRDT reversal undoes an earlier debit), so by default the flag never flips the
sign again. Some banks instead put the *original* entry's direction in
``CdtDbtInd`` and expect the flag to invert it; ``reversal_sign: flipped``
reads such a bank's files. The balance checks below run on the resulting signs,
so the wrong setting is rejected wherever a statement carries balances or a net
total; a camt.054 notification has neither, and there the setting is trusted.

Integrity. A statement that carries an opening (OPBD/PRCD) and closing (CLBD)
booked balance must satisfy opening + booked entries = closing, and its
``TxsSummry`` (entry count, net total) must agree with the entries. A file that
fails either check is incomplete or corrupt and is rejected; set
``verify_balances: false`` only for a bank known to fill these in differently.
Multi-page statements (``MsgPgntn``) are not checked page by page.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as ET
from pydantic import BaseModel, ConfigDict

from tieout.errors import ConnectorError, MoneyError
from tieout.money import format_amount, normalize_currency, parse_amount
from tieout.records import BUILTIN_FIELDS, Exclusion, Record, assign_stable_ids

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element


class CamtOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: tuple[str, ...] = ("BOOK",)
    include_reversals: bool = True
    reversal_sign: Literal["iso", "flipped"] = "iso"
    verify_balances: bool = True

    def fields(self) -> frozenset[str]:
        """Names rules can refer to on records from this source."""
        return frozenset({*BUILTIN_FIELDS, "value_date", "booking_date", *_ATTRIBUTES})


_ATTRIBUTES = (
    "statement_id",
    "account_id",
    "reversal",
    "remittance",
    "end_to_end_id",
    "instruction_id",
    "tx_id",
    "counterparty",
)
_TRUE_FALSE = {"true": True, "1": True, "false": False, "0": False}


class _Movement(NamedTuple):
    status: str
    currency: str
    amount: int  # signed minor units
    reversal: bool


def parse_camt(
    name: str, path: Path, raw_bytes: bytes, raw_options: dict[str, Any]
) -> tuple[list[Record], list[Exclusion]]:
    """Parse an ISO 20022 camt.053 or camt.054 XML document into canonical records."""
    opts = CamtOptions.model_validate(raw_options)
    path_str = str(path)

    try:
        root = ET.fromstring(raw_bytes)
    except (DefusedXmlException, ET.ParseError) as exc:
        raise ConnectorError(f"invalid or unsafe XML: {exc}", path=path_str) from exc

    containers = _find_containers(root, path_str)
    allowed_statuses = {code.strip().upper() for code in opts.status}
    paginated = _is_paginated(root)

    records: list[Record] = []
    excluded: list[Exclusion] = []
    entry_index = 0
    for statement_index, container in enumerate(containers, start=1):
        statement_id = _child_text(container, "Id") or ""
        account_id = (
            _path_text(container, "Acct", "Id", "IBAN")
            or _path_text(container, "Acct", "Id", "Othr", "Id")
            or ""
        )
        movements: list[_Movement] = []
        for entry in _children(container, "Ntry"):
            entry_index += 1
            location = f"entry {entry_index}"
            record, status, skip_reason = _parse_entry(
                name=name,
                entry=entry,
                entry_index=entry_index,
                statement_id=statement_id,
                account_id=account_id,
                allowed_statuses=allowed_statuses,
                include_reversals=opts.include_reversals,
                flip_reversals=opts.reversal_sign == "flipped",
                path_str=path_str,
                location=location,
            )
            movements.append(
                _Movement(status, record.currency, record.amount, "reversal" in record.attributes)
            )
            if skip_reason is None:
                records.append(record)
            else:
                excluded.append(Exclusion(location, skip_reason))
        if opts.verify_balances and not paginated:
            where = f"statement {statement_index}" + (f" ({statement_id})" if statement_id else "")
            _verify_balances(container, movements, opts.reversal_sign, path_str, where)
            _verify_summary(container, movements, opts.reversal_sign, path_str, where)

    return assign_stable_ids(name, records, lambda record: record.external_id), excluded


def _is_paginated(root: Element) -> bool:
    header = _child(root, "GrpHdr")
    if header is None:
        for envelope in root:
            header = _child(envelope, "GrpHdr")
            if header is not None:
                break
    if header is None:
        return False
    pagination = _child(header, "MsgPgntn")
    if pagination is None:
        return False
    page = _child_text(pagination, "PgNb")
    last = (_child_text(pagination, "LastPgInd") or "true").lower()
    return page not in ("", "1") or last not in ("true", "1")


def _signed(
    amount_elem: Element | None,
    indicator: str,
    path_str: str,
    where: str,
    *,
    currency: str | None = None,
) -> tuple[str, int]:
    """Parse an unsigned ISO 20022 amount and apply its credit/debit indicator.

    ``currency`` is used when the element carries no ``Ccy`` attribute (summary
    totals in some message versions).
    """
    if amount_elem is None:
        raise ConnectorError("missing amount", path=path_str, location=where)
    try:
        code = normalize_currency(amount_elem.attrib.get("Ccy") or currency or "")
        minor = parse_amount((amount_elem.text or "").strip(), code)
    except MoneyError as exc:
        raise ConnectorError(f"invalid amount: {exc}", path=path_str, location=where) from exc
    if minor < 0 or (amount_elem.text or "").strip().startswith("+"):
        raise ConnectorError(
            "camt amounts must be unsigned; direction belongs in <CdtDbtInd>",
            path=path_str,
            location=where,
        )
    if indicator.upper() == "CRDT":
        return code, minor
    if indicator.upper() == "DBIT":
        return code, -minor
    raise ConnectorError(
        f"invalid <CdtDbtInd> {indicator!r}; expected 'CRDT' or 'DBIT'",
        path=path_str,
        location=where,
    )


def _reversal_hint(movements: list[_Movement], expected_net: int, reversal_sign: str) -> str:
    """Why a total is off, when reading the reversal entries the other way explains it."""
    reversal_net = sum(m.amount for m in movements if m.reversal)
    net = sum(m.amount for m in movements)
    if reversal_net == 0 or net - 2 * reversal_net != expected_net:
        return ""
    if reversal_sign == "iso":
        return (
            "; it does add up if the <RvslInd> entries carry the opposite sign, so this bank "
            "probably puts the original entry's direction in <CdtDbtInd>: "
            "set reversal_sign: flipped"
        )
    return (
        "; it does add up if the <RvslInd> entries keep the sign <CdtDbtInd> gives them: "
        "remove reversal_sign: flipped"
    )


def _verify_balances(
    container: Element,
    movements: list[_Movement],
    reversal_sign: str,
    path_str: str,
    where: str,
) -> None:
    balances: dict[str, tuple[str, int]] = {}
    for balance in _children(container, "Bal"):
        code = (
            _path_text(balance, "Tp", "CdOrPrtry", "Cd")
            or _path_text(balance, "Tp", "CdOrPrtry", "Prtry")
        ).upper()
        if code not in ("OPBD", "PRCD", "CLBD"):
            continue
        if code in balances:
            raise ConnectorError(f"more than one {code} balance", path=path_str, location=where)
        balances[code] = _signed(
            _child(balance, "Amt"), _child_text(balance, "CdtDbtInd"), path_str, f"{where} {code}"
        )

    opening = balances.get("OPBD") or balances.get("PRCD")
    closing = balances.get("CLBD")
    if opening is None or closing is None:
        return
    currency = opening[0]
    if closing[0] != currency:
        raise ConnectorError(
            f"opening balance is in {currency} but closing balance in {closing[0]}",
            path=path_str,
            location=where,
        )
    booked = [m for m in movements if m.status.upper() == "BOOK"]
    if any(m.currency != currency for m in booked):
        raise ConnectorError(
            f"booked entries in a currency other than the {currency} balances; "
            "cannot verify (set verify_balances: false to skip)",
            path=path_str,
            location=where,
        )
    net = sum(m.amount for m in booked)
    if opening[1] + net != closing[1]:
        hint = _reversal_hint(booked, closing[1] - opening[1], reversal_sign)
        raise ConnectorError(
            f"balances do not add up: opening {format_amount(opening[1], currency)} + booked "
            f"entries {format_amount(net, currency)} != closing "
            f"{format_amount(closing[1], currency)} {currency}"
            + (hint or "; the file may be truncated"),
            path=path_str,
            location=where,
        )


def _verify_summary(
    container: Element,
    movements: list[_Movement],
    reversal_sign: str,
    path_str: str,
    where: str,
) -> None:
    totals = _path(container, "TxsSummry", "TtlNtries")
    if totals is None:
        return
    count_text = _child_text(totals, "NbOfNtries")
    if count_text:
        if not count_text.isdigit():
            raise ConnectorError(
                f"invalid <NbOfNtries> {count_text!r}", path=path_str, location=where
            )
        if int(count_text) != len(movements):
            raise ConnectorError(
                f"<TxsSummry> declares {count_text} entries but the statement has "
                f"{len(movements)}; the file may be truncated",
                path=path_str,
                location=where,
            )
    # v08+: <TtlNetNtry><Amt/><CdtDbtInd/></TtlNetNtry>; v02: <TtlNetNtryAmt/> + <CdtDbtInd/>.
    net_node = _child(totals, "TtlNetNtry")
    if net_node is not None:
        amount_elem, indicator = _child(net_node, "Amt"), _child_text(net_node, "CdtDbtInd")
    else:
        amount_elem, indicator = _child(totals, "TtlNetNtryAmt"), _child_text(totals, "CdtDbtInd")
    if amount_elem is None:
        return
    currencies = {m.currency for m in movements}
    if len(currencies) > 1:
        return  # a net total over several currencies is not a number we can check
    currency = next(iter(currencies), None)
    declared_currency, declared = _signed(
        amount_elem, indicator, path_str, f"{where} TxsSummry", currency=currency
    )
    actual = sum(m.amount for m in movements)
    if (currency is not None and declared_currency != currency) or declared != actual:
        hint = _reversal_hint(movements, declared, reversal_sign)
        raise ConnectorError(
            f"<TxsSummry> net total {format_amount(declared, declared_currency)} "
            f"{declared_currency} does not match the entries' net "
            f"{format_amount(actual, currency or declared_currency)}" + hint,
            path=path_str,
            location=where,
        )


def _find_containers(root: Element, path_str: str) -> list[Element]:
    tag = _local_name(root)
    if tag == "Document":
        envelope = _child(root, "BkToCstmrStmt")
        if envelope is None:
            envelope = _child(root, "BkToCstmrDbtCdtNtfctn")
    elif tag in ("BkToCstmrStmt", "BkToCstmrDbtCdtNtfctn"):
        envelope = root
    else:
        envelope = None

    if envelope is None:
        raise ConnectorError(
            "expected ISO 20022 camt.053 (BkToCstmrStmt) or camt.054 (BkToCstmrDbtCdtNtfctn)",
            path=path_str,
        )

    section_tag = "Stmt" if _local_name(envelope) == "BkToCstmrStmt" else "Ntfctn"
    containers = _children(envelope, section_tag)
    if not containers:
        raise ConnectorError(f"no <{section_tag}> elements found", path=path_str)
    return containers


def _parse_entry(
    *,
    name: str,
    entry: Element,
    entry_index: int,
    statement_id: str,
    account_id: str,
    allowed_statuses: set[str],
    include_reversals: bool,
    flip_reversals: bool,
    path_str: str,
    location: str,
) -> tuple[Record, str, str | None]:
    """The entry as a record, its status, and why it is filtered out (None if kept)."""
    status = _entry_status(entry)
    if not status:
        raise ConnectorError("missing entry status <Sts>", path=path_str, location=location)

    is_reversal = _reversal_flag(entry, path_str, location)

    skip_reason: str | None = None
    if allowed_statuses and status.upper() not in allowed_statuses:
        skip_reason = f"status {status.upper()}"
    elif is_reversal and not include_reversals:
        skip_reason = "reversal"

    currency, signed_amount = _entry_amount(
        entry, path_str, location, flip=is_reversal and flip_reversals
    )
    booking_date = _entry_date(entry, "BookgDt", path_str, location)
    value_date = _entry_date(entry, "ValDt", path_str, location)
    if value_date is None and booking_date is None:
        raise ConnectorError(
            "entry has neither <ValDt> nor <BookgDt>", path=path_str, location=location
        )

    tx_details = _all_tx_details(entry)
    external_id = (
        _child_text(entry, "AcctSvcrRef")
        or _child_text(entry, "NtryRef")
        or _first_tx_path(tx_details, "Refs", "AcctSvcrRef")
        or None
    )

    extra_info = _child_text(entry, "AddtlNtryInf")
    remittance = _collect_remittance(tx_details)
    description = extra_info or remittance or ""

    attributes: dict[str, str] = {}
    if statement_id:
        attributes["statement_id"] = statement_id
    if account_id:
        attributes["account_id"] = account_id
    if is_reversal:
        attributes["reversal"] = "true"
    if remittance:
        attributes["remittance"] = remittance

    end_to_end_id = _first_tx_path(tx_details, "Refs", "EndToEndId")
    if end_to_end_id and end_to_end_id.upper() != "NOTPROVIDED":
        attributes["end_to_end_id"] = end_to_end_id
    if instr_id := _first_tx_path(tx_details, "Refs", "InstrId"):
        attributes["instruction_id"] = instr_id
    if tx_id := _first_tx_path(tx_details, "Refs", "TxId"):
        attributes["tx_id"] = tx_id
    if counterparty := _extract_counterparty(tx_details, is_credit=signed_amount >= 0):
        attributes["counterparty"] = counterparty

    record = Record(
        id=f"{name}:entry-{entry_index}",  # provisional; replaced by a stable id
        source=name,
        amount=signed_amount,
        currency=currency,
        value_date=value_date,
        booking_date=booking_date,
        external_id=external_id,
        description=description,
        attributes=attributes,
        location=location,
    )
    return record, status, skip_reason


def _reversal_flag(entry: Element, path_str: str, location: str) -> bool:
    raw = _child_text(entry, "RvslInd")
    if not raw:
        return False
    try:
        return _TRUE_FALSE[raw.lower()]
    except KeyError:
        raise ConnectorError(
            f"invalid <RvslInd> {raw!r}; expected 'true' or 'false'",
            path=path_str,
            location=location,
        ) from None


def _entry_status(entry: Element) -> str:
    sts = _child(entry, "Sts")
    if sts is None:
        return ""
    if code := (_child_text(sts, "Cd") or _child_text(sts, "Prtry")):
        return code
    return (sts.text or "").strip()


def _entry_amount(entry: Element, path_str: str, location: str, *, flip: bool) -> tuple[str, int]:
    """The entry's currency and signed amount.

    ``flip`` is for a reversal from a bank whose ``CdtDbtInd`` states the
    original entry's direction: the reversal moves money the other way.
    """
    amt_elem = _child(entry, "Amt")
    if amt_elem is None:
        raise ConnectorError("missing <Amt>", path=path_str, location=location)
    currency, amount = _signed(amt_elem, _child_text(entry, "CdtDbtInd"), path_str, location)
    return currency, -amount if flip else amount


def _entry_date(entry: Element, tag: str, path_str: str, location: str) -> date | None:
    node = _child(entry, tag)
    if node is None:
        return None
    raw = _child_text(node, "Dt") or _child_text(node, "DtTm")
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise ConnectorError(
            f"invalid date {raw!r} in <{tag}>", path=path_str, location=location
        ) from exc


def _all_tx_details(entry: Element) -> list[Element]:
    details: list[Element] = []
    for ntry_dtls in _children(entry, "NtryDtls"):
        details.extend(_children(ntry_dtls, "TxDtls"))
    return details


def _first_tx_path(tx_details: list[Element], *tags: str) -> str:
    for tx in tx_details:
        if value := _path_text(tx, *tags):
            return value
    return ""


def _collect_remittance(tx_details: list[Element]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for tx in tx_details:
        rmt = _child(tx, "RmtInf")
        if rmt is None:
            continue
        candidates = [*(child.text or "" for child in _children(rmt, "Ustrd"))]
        for strd in _children(rmt, "Strd"):
            if ref := _path_text(strd, "CdtrRefInf", "Ref"):
                candidates.append(ref)
        for text in candidates:
            cleaned = text.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                parts.append(cleaned)
    return " ".join(parts)


def _extract_counterparty(tx_details: list[Element], *, is_credit: bool) -> str:
    order = ("Dbtr", "Cdtr") if is_credit else ("Cdtr", "Dbtr")
    for tx in tx_details:
        parties = _child(tx, "RltdPties")
        if parties is None:
            continue
        for role in order:
            party = _child(parties, role)
            if party is None:
                continue
            # v02 puts <Nm> directly under <Dbtr>/<Cdtr>; v08+ nests it under <Pty>.
            if name := (_child_text(party, "Nm") or _path_text(party, "Pty", "Nm")):
                return name
    return ""


def _local_name(elem: Element) -> str:
    return elem.tag.rsplit("}", 1)[-1]


def _children(parent: Element, name: str) -> list[Element]:
    return [child for child in parent if _local_name(child) == name]


def _child(parent: Element, name: str) -> Element | None:
    for child in parent:
        if _local_name(child) == name:
            return child
    return None


def _child_text(parent: Element, name: str) -> str:
    child = _child(parent, name)
    return (child.text or "").strip() if child is not None else ""


def _path(parent: Element, *names: str) -> Element | None:
    current: Element | None = parent
    for name in names:
        if current is None:
            return None
        current = _child(current, name)
    return current


def _path_text(parent: Element, *names: str) -> str:
    current: Element | None = parent
    for name in names:
        if current is None:
            return ""
        current = _child(current, name)
    return (current.text or "").strip() if current is not None else ""
