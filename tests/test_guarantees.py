"""Regression tests for the failure modes a finance team would never forgive.

Each test pins one way a reconciliation could be *silently* wrong, and checks that
tieout either gets it right or refuses loudly.
"""

from __future__ import annotations

import csv
import dataclasses
import random
from datetime import date
from pathlib import Path

import pytest

from tieout import BreakReason, Record, Rule, RuleKind, Source, money, reconcile
from tieout.connectors import read_source, validate_spec
from tieout.engine import verify_result
from tieout.errors import ConnectorError, InputError, InvariantError, MoneyError, SpecError
from tieout.money import minor_unit_exponent, parse_amount, register_currency
from tieout.report import write_breaks_csv
from tieout.spec import (
    AmountCondition,
    DateCondition,
    GroupSpec,
    ReferenceCondition,
    Spec,
    parse_spec,
)

DAY = date(2026, 11, 2)


def _rec(rid: str, source: str, amount: int, **kw: object) -> Record:
    kw.setdefault("value_date", DAY)
    return Record(id=rid, source=source, amount=amount, currency="USD", **kw)  # type: ignore[arg-type]


# --- 1. Exact money --------------------------------------------------------------


@pytest.mark.parametrize("code", ["USX", "XAU", "XDR", "XXX", "ABC"])
def test_unknown_or_unitless_currency_is_refused(code: str) -> None:
    with pytest.raises(MoneyError, match="unknown currency"):
        parse_amount("1.00", code)
    with pytest.raises(InputError, match="unknown currency"):
        Record(id="r", source="s", amount=1, currency=code, value_date=DAY)


def test_iso_4217_exponents_and_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(money, "_EXPONENTS", dict(money._EXPONENTS))  # keep the registry clean
    assert minor_unit_exponent("ZWG") == 2  # added in amendment 177 (2024)
    assert minor_unit_exponent("XCG") == 2  # Caribbean guilder (2025)
    assert minor_unit_exponent("HRK") == 2  # withdrawn, still in old statements
    register_currency("USC", 6)
    assert parse_amount("1.000001", "USC") == 1_000_001
    with pytest.raises(MoneyError, match="refusing"):
        register_currency("JPY", 2)


@pytest.mark.parametrize(
    ("text", "kwargs", "expected"),
    [
        ("1,234,567.89", {"thousands_separator": ","}, 123456789),
        ("12,34,567.00", {"thousands_separator": ","}, 123456700),  # Indian lakh grouping
        ("1.234.567,89", {"thousands_separator": ".", "decimal_separator": ","}, 123456789),
        ("1 234,50", {"thousands_separator": " ", "decimal_separator": ","}, 123450),
    ],
)
def test_well_formed_digit_grouping(text: str, kwargs: dict[str, str], expected: int) -> None:
    assert parse_amount(text, "USD", **kwargs) == expected


@pytest.mark.parametrize(
    ("text", "kwargs"),
    [
        ("1,23", {"thousands_separator": ","}),  # a decimal comma read with wrong settings
        ("1,2345", {"thousands_separator": ","}),
        ("12,345,67", {"thousands_separator": ","}),
        (",123", {"thousands_separator": ","}),
        ("1,,234", {"thousands_separator": ","}),
        ("1.234,5,6", {"thousands_separator": ".", "decimal_separator": ","}),
        ("1,234.5,6", {"thousands_separator": ","}),
        (chr(0x661) + chr(0x660) + "." + chr(0x660) * 2, {}),  # Arabic-Indic digits
        (chr(0xFF11) + chr(0xFF10) + ".00", {}),  # full-width digits
    ],
)
def test_ambiguous_or_foreign_amounts_fail(text: str, kwargs: dict[str, str]) -> None:
    with pytest.raises(MoneyError):
        parse_amount(text, "USD", **kwargs)


# --- 2. Conservation: invariants are checked on every run ------------------------


def test_verify_result_rejects_a_tampered_result() -> None:
    left = Source.from_records("l", [_rec("l1", "l", 100)])
    right = Source.from_records("r", [_rec("r1", "r", 101)])
    rule = Rule(id="r", left="l", right="r", amount=AmountCondition(tolerance=1))
    spec = Spec(rules=(rule,))
    result = reconcile([left, right], spec)
    verify_result(result, spec)  # a genuine result passes

    bad_diff = dataclasses.replace(
        result, matches=(dataclasses.replace(result.matches[0], difference=0),)
    )
    with pytest.raises(InvariantError, match="difference is wrong"):
        verify_result(bad_diff, spec)
    dropped = dataclasses.replace(result, matches=())
    with pytest.raises(InvariantError, match="neither matched nor reported"):
        verify_result(dropped, spec)
    strict = Spec(rules=(rule.model_copy(update={"amount": AmountCondition()}),))
    with pytest.raises(InvariantError, match="exceeds tolerance"):
        verify_result(result, strict)


# --- 3. Never guess: subset claims across targets --------------------------------


def test_unique_group_is_refused_when_an_unresolved_target_could_claim_a_member() -> None:
    invoices = [_rec(i, "inv", a) for i, a in [("a", 100), ("b", 200), ("c", 300), ("e", 50)]]
    invoices.append(_rec("f", "inv", 350))
    # T1 = 150 has one grouping, {a, e}. T2 = 400 is ambiguous: {a, c} or {e, f}.
    # Both of T2's groupings need a member of T1's, so matching T1 would be a guess.
    deposits = [_rec("T1", "bank", 150), _rec("T2", "bank", 400)]
    rule = Rule(
        id="m",
        left="inv",
        right="bank",
        kind=RuleKind.MANY_TO_ONE,
        date=DateCondition(),
        group=GroupSpec(max_size=2),
    )
    result = reconcile(
        [Source.from_records("inv", invoices), Source.from_records("bank", deposits)], [rule]
    )
    assert result.matches == ()
    breaks = {item.record_id: item for item in result.breaks}
    assert breaks["T1"].reason is BreakReason.AMBIGUOUS
    assert "T2 could also claim" in breaks["T1"].detail
    assert "T2" in breaks["T1"].candidate_ids
    assert breaks["b"].reason is BreakReason.UNMATCHED


def test_unique_group_still_matches_when_no_other_target_can_use_its_members() -> None:
    invoices = [_rec(i, "inv", a) for i, a in [("a", 100), ("b", 200), ("c", 250), ("d", 250)]]
    deposits = [_rec("D1", "bank", 300), _rec("D2", "bank", 500)]
    rule = Rule(
        id="m",
        left="inv",
        right="bank",
        kind=RuleKind.MANY_TO_ONE,
        date=DateCondition(),
        group=GroupSpec(max_size=3),
    )
    result = reconcile(
        [Source.from_records("inv", invoices), Source.from_records("bank", deposits)], [rule]
    )
    assert sorted((m.left_ids, m.right_ids) for m in result.matches) == [
        (("a", "b"), ("D1",)),
        (("c", "d"), ("D2",)),
    ]


# --- 4. Determinism: shuffled files give the same answer -------------------------


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "amount", "memo"])
        writer.writerows(rows)


def test_shuffled_csv_rows_give_identical_matches_breaks_and_run_id(tmp_path: Path) -> None:
    ledger_rows = [
        ["2026-11-02", "100.00", "coffee"],
        ["2026-11-02", "100.00", "coffee"],  # identical, no external id: both kept
        ["2026-11-03", "250.00", "rent"],
        ["2026-11-04", "75.10", "fees"],
        ["2026-11-04", "75.10", "fees again"],
    ]
    bank_rows = [
        ["2026-11-02", "100.00", "card"],
        ["2026-11-03", "250.00", "transfer"],
        ["2026-11-05", "75.10", "debit"],
        ["2026-11-06", "12.00", "stray"],
    ]
    options = {"amount_column": "amount", "currency": "USD", "attribute_columns": ("memo",)}
    rule = Rule(id="r", left="ledger", right="bank", date=DateCondition(max_days=2))

    def run(seed: int | None) -> object:
        rng = random.Random(seed)
        paths = {}
        for name, rows in (("ledger", ledger_rows), ("bank", bank_rows)):
            shuffled = list(rows)
            if seed is not None:
                rng.shuffle(shuffled)
            paths[name] = tmp_path / f"{name}-{seed}.csv"
            _write_csv(paths[name], shuffled)
        sources = [read_source(name, path, "csv", **options) for name, path in paths.items()]
        return reconcile(sources, [rule])

    baseline = run(None)
    for seed in range(5):
        shuffled = run(seed)
        assert shuffled.matches == baseline.matches  # type: ignore[attr-defined]
        assert shuffled.breaks == baseline.breaks  # type: ignore[attr-defined]
        assert shuffled.run.run_id == baseline.run.run_id  # type: ignore[attr-defined]
    # The raw bytes differ, and the run log still says so.
    assert run(1).run.input_digests != baseline.run.input_digests  # type: ignore[attr-defined]


def test_duplicate_representative_does_not_depend_on_order() -> None:
    a = _rec("s:a", "s", 100, external_id="X")
    b = _rec("s:b", "s", 100, external_id="X")
    other = Source.from_records("o", [_rec("o:1", "o", 100)])
    rule = Rule(id="r", left="s", right="o")
    first = reconcile([Source.from_records("s", [a, b]), other], [rule])
    second = reconcile([Source.from_records("s", [b, a]), other], [rule])
    assert first.matches == second.matches
    assert first.matches[0].left_ids == ("s:a",)
    assert first.breaks == second.breaks


# --- 6. Dates and time zones -----------------------------------------------------


def _stripe_csv(path: Path, header: str, when: str) -> Path:
    path.write_text(
        f"balance_transaction_id,{header},reporting_category,currency,net,automatic_payout_id\n"
        f"txn_1,{when},charge,usd,10.00,po_1\n"
        f"po_x,{when},payout,usd,-10.00,po_1\n",
        encoding="utf-8",
    )
    return path


def test_stripe_requested_timezone_columns_are_not_read_as_utc(tmp_path: Path) -> None:
    # 02:00 in the report's own (New York) time zone is still 3 November in New York.
    local = _stripe_csv(
        tmp_path / "local.csv", "automatic_payout_effective_at", "2026-11-03 02:00:00"
    )
    with pytest.raises(ConnectorError, match="set report_timezone"):
        read_source("stripe", local, "stripe", timezone="America/New_York")
    source = read_source(
        "stripe",
        local,
        "stripe",
        timezone="America/New_York",
        report_timezone="America/New_York",
    )
    assert source.records[0].value_date == date(2026, 11, 3)
    assert [(row.location, row.reason) for row in source.excluded] == [
        ("line 3", "reporting category payout")
    ]

    # The _utc twin is unambiguous: 02:00 UTC is 2 November in New York.
    utc = _stripe_csv(
        tmp_path / "utc.csv", "automatic_payout_effective_at_utc", "2026-11-03 02:00:00"
    )
    utc_source = read_source("stripe", utc, "stripe", timezone="America/New_York")
    assert utc_source.records[0].value_date == date(2026, 11, 2)


def test_stripe_never_falls_back_to_another_date_column(tmp_path: Path) -> None:
    path = tmp_path / "mixed.csv"
    path.write_text(
        "balance_transaction_id,automatic_payout_effective_at,created,currency,net\n"
        "txn_1,2026-11-03 10:00:00,2026-11-01 10:00:00,usd,10.00\n"
        "txn_2,,2026-11-01 10:00:00,usd,10.00\n",
        encoding="utf-8",
    )
    with pytest.raises(ConnectorError, match=r"\(line 3\): empty automatic_payout_effective_at"):
        read_source("stripe", path, "stripe")
    source = read_source("stripe", path, "stripe", date_column="created")
    assert {r.value_date for r in source.records} == {date(2026, 11, 1)}


def test_value_date_rules_do_not_use_booking_dates() -> None:
    ledger = Source.from_records("ledger", [_rec("l1", "ledger", 500)])
    bank = Source.from_records("bank", [_rec("b1", "bank", 500, value_date=None, booking_date=DAY)])
    on_value = Rule(id="v", left="ledger", right="bank", date=DateCondition(max_days=1))
    result = reconcile([ledger, bank], [on_value])
    assert result.matches == ()
    breaks = {item.record_id: item for item in result.breaks}
    assert "has no value_date" in breaks["b1"].detail

    on_booking = Rule(
        id="b",
        left="ledger",
        right="bank",
        date=DateCondition(max_days=1, field="booking_date"),
    )
    ledger_booked = Source.from_records("ledger", [_rec("l1", "ledger", 500, booking_date=DAY)])
    assert len(reconcile([ledger_booked, bank], [on_booking]).matches) == 1


# --- 7. References ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("needle", "haystack", "expected"),
    [
        ("INV-0042", "Payment for inv 0042", True),
        ("INV-0042", "REF:INV0042/2026", True),
        ("0042", "Payment INV-0042", True),
        ("7712", "Payment #77712", False),
        ("INV0042", "Payment INV00421", False),
        ("INV0042", "PREINV0042", False),
    ],
)
def test_containment_respects_token_boundaries(needle: str, haystack: str, expected: bool) -> None:
    rule = Rule(
        id="r",
        left="l",
        right="r",
        references=(ReferenceCondition(left="ref", right="description", op="left_in_right"),),
    )
    left = Source.from_records("l", [_rec("l1", "l", 100, attributes={"ref": needle})])
    right = Source.from_records("r", [_rec("r1", "r", 100, description=haystack)])
    result = reconcile([left, right], [rule])
    assert bool(result.matches) is expected
    if expected:
        assert f"right.description {haystack!r}" in " ".join(result.matches[0].explanation)


# --- 8. Parser safety ------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("date,amount,amount\n2026-11-01,10.00,99.00\n", "duplicate column 'amount'"),
        ("date,amount,memo\n2026-11-01,10.00\n", r"\(line 2\): row has fewer fields"),
        # Lenient parsing would fold the following rows into one quoted memo field.
        ('date,amount,memo\n2026-11-01,1.00,"x\n2026-11-02,2.00,y\n', "malformed CSV"),
        ('date,amount\n2026-11-01,"10.00"x\n', r"\(near line 2\): malformed CSV"),
    ],
)
def test_csv_shapes_that_would_lose_data_are_rejected(
    tmp_path: Path, content: str, message: str
) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConnectorError, match=message):
        read_source("s", path, "csv", amount_column="amount", currency="USD", attribute_columns=())


def _statement(entries: str, balances: str = "", summary: str = "", header: str = "") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
  <BkToCstmrStmt>
    <GrpHdr><MsgId>M1</MsgId>{header}</GrpHdr>
    <Stmt>
      <Id>S1</Id>
      {balances}
      {summary}
      {entries}
    </Stmt>
  </BkToCstmrStmt>
</Document>"""


def _entry(ref: str, amount: str, indicator: str, extra: str = "") -> str:
    return f"""<Ntry><Amt Ccy="EUR">{amount}</Amt><CdtDbtInd>{indicator}</CdtDbtInd>{extra}
      <Sts><Cd>BOOK</Cd></Sts><BookgDt><Dt>2026-11-02</Dt></BookgDt>
      <ValDt><Dt>2026-11-02</Dt></ValDt><AcctSvcrRef>{ref}</AcctSvcrRef></Ntry>"""


def _balance(code: str, amount: str, indicator: str = "CRDT") -> str:
    return f"""<Bal><Tp><CdOrPrtry><Cd>{code}</Cd></CdOrPrtry></Tp>
      <Amt Ccy="EUR">{amount}</Amt><CdtDbtInd>{indicator}</CdtDbtInd>
      <Dt><Dt>2026-11-02</Dt></Dt></Bal>"""


ENTRIES = _entry("A", "100.00", "CRDT") + _entry("B", "30.00", "DBIT")


def test_camt_balances_and_summary_must_add_up(tmp_path: Path) -> None:
    path = tmp_path / "s.xml"
    good = _balance("OPBD", "1000.00") + _balance("CLBD", "1070.00")
    path.write_text(_statement(ENTRIES, good), encoding="utf-8")
    assert len(read_source("bank", path, "camt").records) == 2

    truncated = _balance("OPBD", "1000.00") + _balance("CLBD", "1170.00")
    path.write_text(_statement(ENTRIES, truncated), encoding="utf-8")
    with pytest.raises(ConnectorError, match="balances do not add up"):
        read_source("bank", path, "camt")
    assert len(read_source("bank", path, "camt", verify_balances=False).records) == 2

    overdrawn = _balance("PRCD", "50.00", "DBIT") + _balance("CLBD", "20.00", "CRDT")
    path.write_text(_statement(ENTRIES, overdrawn), encoding="utf-8")
    assert len(read_source("bank", path, "camt").records) == 2

    summary = (
        "<TxsSummry><TtlNtries><NbOfNtries>3</NbOfNtries>"
        "<TtlNetNtry><Amt>70.00</Amt><CdtDbtInd>CRDT</CdtDbtInd></TtlNetNtry>"
        "</TtlNtries></TxsSummry>"
    )
    path.write_text(_statement(ENTRIES, summary=summary), encoding="utf-8")
    with pytest.raises(ConnectorError, match="declares 3 entries but the statement has 2"):
        read_source("bank", path, "camt")
    path.write_text(_statement(ENTRIES, summary=summary.replace(">3<", ">2<")), encoding="utf-8")
    assert len(read_source("bank", path, "camt").records) == 2
    wrong_net = summary.replace(">3<", ">2<").replace("70.00", "130.00")
    path.write_text(_statement(ENTRIES, summary=wrong_net), encoding="utf-8")
    with pytest.raises(ConnectorError, match="net total"):
        read_source("bank", path, "camt")

    # A page of a multi-page statement is not expected to balance on its own.
    paged = "<MsgPgntn><PgNb>1</PgNb><LastPgInd>false</LastPgInd></MsgPgntn>"
    path.write_text(_statement(ENTRIES, truncated, header=paged), encoding="utf-8")
    assert len(read_source("bank", path, "camt").records) == 2


def test_camt_reversal_flag_is_strict_and_does_not_flip_the_sign_by_default(tmp_path: Path) -> None:
    path = tmp_path / "r.xml"
    reversal = _entry("R", "30.00", "CRDT", "<RvslInd>true</RvslInd>")
    path.write_text(_statement(reversal), encoding="utf-8")
    (record,) = read_source("bank", path, "camt").records
    assert record.amount == 3000  # a credit that undoes an earlier debit
    assert record.attributes["reversal"] == "true"

    path.write_text(
        _statement(_entry("R", "30.00", "CRDT", "<RvslInd>yes</RvslInd>")), encoding="utf-8"
    )
    with pytest.raises(ConnectorError, match=r"\(entry 1\): invalid <RvslInd> 'yes'"):
        read_source("bank", path, "camt")

    path.write_text(_statement(_entry("N", "(30.00)", "CRDT")), encoding="utf-8")
    with pytest.raises(ConnectorError, match="must be unsigned"):
        read_source("bank", path, "camt")


def test_camt_reversal_sign_option_is_checked_against_the_balances(tmp_path: Path) -> None:
    # A bank that puts the ORIGINAL entry's direction on a reversal: CRDT + RvslInd is money out.
    path = tmp_path / "fi.xml"
    entries = _entry("A", "100.00", "CRDT") + _entry(
        "R", "30.00", "CRDT", "<RvslInd>true</RvslInd>"
    )
    balances = _balance("OPBD", "1000.00") + _balance("CLBD", "1070.00")
    path.write_text(_statement(entries, balances), encoding="utf-8")

    # Read the ISO way the statement cannot balance: rejected, and the error names the fix.
    with pytest.raises(ConnectorError, match=r"balances do not add up.*set reversal_sign: flipped"):
        read_source("bank", path, "camt")
    flipped = read_source("bank", path, "camt", reversal_sign="flipped").records
    assert sorted(record.amount for record in flipped) == [-3000, 10000]

    # The wrong setting on an ISO-conformant bank is rejected the same way.
    iso_balances = _balance("OPBD", "1000.00") + _balance("CLBD", "1130.00")
    path.write_text(_statement(entries, iso_balances), encoding="utf-8")
    assert len(read_source("bank", path, "camt").records) == 2
    with pytest.raises(ConnectorError, match=r"remove reversal_sign: flipped"):
        read_source("bank", path, "camt", reversal_sign="flipped")

    # The net total in <TxsSummry> catches it when there are no balances.
    summary = (
        "<TxsSummry><TtlNtries><NbOfNtries>2</NbOfNtries>"
        "<TtlNetNtry><Amt>70.00</Amt><CdtDbtInd>CRDT</CdtDbtInd></TtlNetNtry>"
        "</TtlNtries></TxsSummry>"
    )
    path.write_text(_statement(entries, summary=summary), encoding="utf-8")
    with pytest.raises(ConnectorError, match=r"net total.*set reversal_sign: flipped"):
        read_source("bank", path, "camt")
    assert len(read_source("bank", path, "camt", reversal_sign="flipped").records) == 2

    # A mismatch the reversals do not explain keeps the plain message.
    path.write_text(
        _statement(entries, _balance("OPBD", "1000.00") + _balance("CLBD", "999.00")),
        encoding="utf-8",
    )
    with pytest.raises(ConnectorError, match="the file may be truncated"):
        read_source("bank", path, "camt")

    with pytest.raises(SpecError, match="reversal_sign"):
        read_source("bank", path, "camt", reversal_sign="sometimes")


def test_camt_ids_survive_reordered_entries(tmp_path: Path) -> None:
    unreferenced = _entry("", "5.00", "CRDT").replace("<AcctSvcrRef></AcctSvcrRef>", "")
    forward = tmp_path / "f.xml"
    backward = tmp_path / "b.xml"
    forward.write_text(_statement(ENTRIES + unreferenced), encoding="utf-8")
    backward.write_text(
        _statement(unreferenced + _entry("B", "30.00", "DBIT") + _entry("A", "100.00", "CRDT")),
        encoding="utf-8",
    )
    ids_forward = sorted(r.id for r in read_source("bank", forward, "camt").records)
    ids_backward = sorted(r.id for r in read_source("bank", backward, "camt").records)
    assert ids_forward == ids_backward
    assert "bank:A" in ids_forward


# --- 9/10. Explanations, config validation, safe reports -------------------------


def test_misspelt_rule_fields_are_spec_errors() -> None:
    spec = parse_spec(
        {
            "sources": {"stripe": {"format": "stripe"}, "bank": {"format": "camt"}},
            "rules": [
                {
                    "id": "payout",
                    "left": "stripe",
                    "right": "bank",
                    "kind": "many_to_one",
                    "group": {"by": "payout_idd"},
                    "date": {"field": "booking_date", "max_days": 3},
                    "references": [{"left": "payout_id", "right": "descripton"}],
                }
            ],
        }
    )
    with pytest.raises(SpecError) as caught:
        validate_spec(spec)
    message = str(caught.value)
    assert "left source 'stripe' has no field 'payout_idd'" in message
    assert "left source 'stripe' has no field 'booking_date'" in message
    assert "right source 'bank' has no field 'descripton'" in message


def test_breaks_csv_neutralises_spreadsheet_formulas(tmp_path: Path) -> None:
    left = Source.from_records(
        "l", [_rec("l1", "l", 100, external_id='=HYPERLINK("http://x","pay")')]
    )
    right = Source.from_records("r", [_rec("r1", "r", -5, location="line 7")])
    result = reconcile([left, right], [Rule(id="r", left="l", right="r")])
    out = tmp_path / "breaks.csv"
    write_breaks_csv(result, out)
    with out.open(encoding="utf-8", newline="") as handle:
        rows = {row["record_id"]: row for row in csv.DictReader(handle)}
    assert rows["l1"]["external_id"].startswith("'=HYPERLINK")
    assert rows["r1"]["amount"] == "-0.05"  # numbers are left as numbers
    assert rows["r1"]["location"] == "line 7"
