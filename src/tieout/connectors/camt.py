"""ISO 20022 camt.053 (bank statement) and camt.054 (debit/credit notification) connector."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as ET
from pydantic import BaseModel, ConfigDict

from tieout.errors import ConnectorError, MoneyError
from tieout.money import normalize_currency, parse_amount
from tieout.records import Record

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element


class CamtOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: tuple[str, ...] = ("BOOK",)
    include_reversals: bool = True


def parse_camt(
    name: str, path: Path, raw_bytes: bytes, raw_options: dict[str, Any]
) -> list[Record]:
    """Parse an ISO 20022 camt.053 or camt.054 XML document into canonical records."""
    opts = CamtOptions.model_validate(raw_options)
    path_str = str(path)

    try:
        root = ET.fromstring(raw_bytes)
    except (DefusedXmlException, ET.ParseError) as exc:
        raise ConnectorError(f"invalid or unsafe XML: {exc}", path=path_str) from exc

    containers = _find_containers(root, path_str)
    allowed_statuses = {code.strip().upper() for code in opts.status}

    records: list[Record] = []
    entry_index = 0
    for container in containers:
        statement_id = _child_text(container, "Id") or ""
        account_id = (
            _path_text(container, "Acct", "Id", "IBAN")
            or _path_text(container, "Acct", "Id", "Othr", "Id")
            or ""
        )
        for entry in _children(container, "Ntry"):
            entry_index += 1
            location = f"entry {entry_index}"
            record = _parse_entry(
                name=name,
                entry=entry,
                entry_index=entry_index,
                statement_id=statement_id,
                account_id=account_id,
                allowed_statuses=allowed_statuses,
                include_reversals=opts.include_reversals,
                path_str=path_str,
                location=location,
            )
            if record is not None:
                records.append(record)

    return records


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
    path_str: str,
    location: str,
) -> Record | None:
    status = _entry_status(entry)
    if not status:
        raise ConnectorError("missing entry status <Sts>", path=path_str, location=location)
    if allowed_statuses and status.upper() not in allowed_statuses:
        return None

    is_reversal = (_child_text(entry, "RvslInd") or "").lower() in ("true", "1")
    if is_reversal and not include_reversals:
        return None

    currency, signed_amount = _entry_amount(entry, path_str, location)
    booking_date = _entry_date(entry, "BookgDt", path_str, location)
    value_date = _entry_date(entry, "ValDt", path_str, location) or booking_date
    if value_date is None:
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

    return Record(
        id=f"{name}:{entry_index}",
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


def _entry_status(entry: Element) -> str:
    sts = _child(entry, "Sts")
    if sts is None:
        return ""
    if code := (_child_text(sts, "Cd") or _child_text(sts, "Prtry")):
        return code
    return (sts.text or "").strip()


def _entry_amount(entry: Element, path_str: str, location: str) -> tuple[str, int]:
    amt_elem = _child(entry, "Amt")
    if amt_elem is None:
        raise ConnectorError("missing <Amt>", path=path_str, location=location)
    raw_currency = amt_elem.attrib.get("Ccy", "")
    raw_amount = (amt_elem.text or "").strip()
    try:
        currency = normalize_currency(raw_currency)
        minor = parse_amount(raw_amount, currency)
    except MoneyError as exc:
        raise ConnectorError(f"invalid <Amt>: {exc}", path=path_str, location=location) from exc
    if minor < 0:
        raise ConnectorError(
            "camt <Amt> must be unsigned; direction belongs in <CdtDbtInd>",
            path=path_str,
            location=location,
        )

    indicator = (_child_text(entry, "CdtDbtInd") or "").upper()
    if indicator == "CRDT":
        return currency, minor
    if indicator == "DBIT":
        return currency, -minor
    raise ConnectorError(
        f"invalid <CdtDbtInd> {indicator!r}; expected 'CRDT' or 'DBIT'",
        path=path_str,
        location=location,
    )


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


def _path_text(parent: Element, *names: str) -> str:
    current: Element | None = parent
    for name in names:
        if current is None:
            return ""
        current = _child(current, name)
    return (current.text or "").strip() if current is not None else ""
