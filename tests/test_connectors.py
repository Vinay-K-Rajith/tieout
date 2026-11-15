"""Tests for source connectors: CSV, ISO 20022 camt.053/054, and Stripe."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tieout import BreakReason, Rule, RuleKind, reconcile
from tieout.connectors import read_source
from tieout.errors import ConnectorError, SpecError
from tieout.spec import DateCondition, GroupSpec, ReferenceCondition, SourceSpec

FIXTURES = Path(__file__).parent / "fixtures"


def test_read_source_errors_and_digest(tmp_path: Path) -> None:
    with pytest.raises(SpecError, match="unknown source format"):
        read_source("bank", FIXTURES / "ledger.csv", "bai2")

    with pytest.raises(ConnectorError, match="cannot read file"):
        read_source("bank", tmp_path / "missing.csv", "csv")

    with pytest.raises(SpecError, match="invalid csv options"):
        read_source("bank", FIXTURES / "ledger.csv", SourceSpec(format="csv", options={"bad": 1}))

    source = read_source(
        "ledger",
        FIXTURES / "ledger.csv",
        "csv",
        id_column="entry_id",
        value_date_column="posted_date",
        currency_column="currency",
        inflow_column="inflow",
        outflow_column="outflow",
        description_column="memo",
        attribute_columns=("reference",),
    )
    assert source.digest.startswith("sha256:")
    assert len(source.records) == 3
    assert [r.id for r in source.records] == ["ledger:JRN-1", "ledger:JRN-2", "ledger:JRN-3"]
    assert [r.amount for r in source.records] == [20000, -4550, 4550]
    assert source.records[1].attributes["reference"] == "INV-8821"
    assert source.records[2].location == "line 5"


def test_csv_direction_column_and_european_format(tmp_path: Path) -> None:
    csv_path = tmp_path / "eu.csv"
    csv_path.write_text(
        "date;side;amount;ref\n03.11.2026;DBIT;1.234,50;INV-1\n04.11.2026;CRDT;99,00;INV-2\n",
        encoding="utf-8",
    )
    source = read_source(
        "bank",
        csv_path,
        "csv",
        value_date_column="date",
        amount_column="amount",
        direction_column="side",
        currency="eur",
        date_format="%d.%m.%Y",
        delimiter=";",
        decimal_separator=",",
        thousands_separator=".",
        attribute_columns=("ref",),
    )
    assert [r.id for r in source.records] == ["bank:2", "bank:3"]
    assert [r.amount for r in source.records] == [-123450, 9900]
    assert [r.value_date for r in source.records] == [date(2026, 11, 3), date(2026, 11, 4)]
    assert source.records[0].currency == "EUR"


def test_csv_error_paths(tmp_path: Path) -> None:
    bad_amount = tmp_path / "bad_amount.csv"
    bad_amount.write_text("date,amount\n2026-11-01,10.005\n", encoding="utf-8")
    with pytest.raises(ConnectorError, match=r"\(line 2\)"):
        read_source("s", bad_amount, "csv", amount_column="amount", currency="USD")

    missing_col = tmp_path / "missing_col.csv"
    missing_col.write_text("date,val\n2026-11-01,10.00\n", encoding="utf-8")
    with pytest.raises(ConnectorError, match=r"\(header\).*missing column"):
        read_source("s", missing_col, "csv", amount_column="amount", currency="USD")

    bad_dir = tmp_path / "bad_dir.csv"
    bad_dir.write_text("date,amount,dir\n2026-11-01,-10.00,credit\n", encoding="utf-8")
    with pytest.raises(ConnectorError, match="conflicts with credit direction"):
        read_source(
            "s", bad_dir, "csv", amount_column="amount", direction_column="dir", currency="USD"
        )

    extra_fields = tmp_path / "extra.csv"
    extra_fields.write_text("date,amount\n2026-11-01,10.00,extra\n", encoding="utf-8")
    with pytest.raises(ConnectorError, match="more fields than the header"):
        read_source("s", extra_fields, "csv", amount_column="amount", currency="USD")

    with pytest.raises(SpecError, match="invalid csv options"):
        read_source("s", bad_amount, "csv", amount_column="amount")  # no currency

    with pytest.raises(SpecError, match="invalid csv options"):
        read_source(
            "s",
            bad_amount,
            "csv",
            amount_column="amount",
            inflow_column="in",
            outflow_column="out",
            currency="USD",
        )


def test_camt053_fixture_and_filtering() -> None:
    source = read_source("bank", FIXTURES / "bank_camt053.xml", "camt")
    assert len(source.records) == 3  # PDNG entry 3 filtered out by default

    first, second, third = source.records
    assert first.id == "bank:1"
    assert first.amount == 20000
    assert first.currency == "USD"
    assert first.value_date == date(2026, 11, 3)
    assert first.booking_date == date(2026, 11, 3)
    assert first.external_id == "BKREF-001"
    assert first.attributes["counterparty"] == "Stripe Inc"
    assert first.attributes["end_to_end_id"] == "po_1001"
    assert first.attributes["statement_id"] == "STMT-2026-11-01"
    assert first.attributes["account_id"] == "DE89370400440532013000"

    assert second.id == "bank:2"
    assert second.amount == -4550
    assert second.attributes["remittance"] == "INV-8821"
    assert second.attributes["counterparty"] == "Cloud Host LLC"

    # Entry 4 is a reversal with EndToEndId=NOTPROVIDED and ValDt falling back to BookgDt.
    assert third.id == "bank:4"
    assert third.location == "entry 4"
    assert third.amount == 4550
    assert third.value_date == date(2026, 11, 5)
    assert third.attributes["reversal"] == "true"
    assert "end_to_end_id" not in third.attributes

    with_pending = read_source(
        "bank",
        FIXTURES / "bank_camt053.xml",
        "camt",
        status=("BOOK", "PDNG"),
        include_reversals=False,
    )
    assert [r.external_id for r in with_pending.records] == [
        "BKREF-001",
        "BKREF-002",
        "BKREF-PDNG",
    ]


def test_camt054_and_v02_formats_and_errors(tmp_path: Path) -> None:
    camt054 = tmp_path / "camt054_v02.xml"
    camt054.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<Document>
  <BkToCstmrDbtCdtNtfctn>
    <Ntfctn>
      <Id>NTF-1</Id>
      <Ntry>
        <Amt Ccy="JPY">5000</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <Sts>BOOK</Sts>
        <ValDt><Dt>2026-11-06</Dt></ValDt>
        <NtryDtls>
          <TxDtls>
            <RltdPties><Dbtr><Nm>Tanaka Corp</Nm></Dbtr></RltdPties>
          </TxDtls>
        </NtryDtls>
      </Ntry>
    </Ntfctn>
  </BkToCstmrDbtCdtNtfctn>
</Document>""",
        encoding="utf-8",
    )
    src = read_source("ntf", camt054, "camt")
    assert len(src.records) == 1
    assert src.records[0].amount == 5000
    assert src.records[0].currency == "JPY"
    assert src.records[0].attributes["counterparty"] == "Tanaka Corp"

    xml_bomb = tmp_path / "bomb.xml"
    xml_bomb.write_text(
        """<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;">
]>
<Document><BkToCstmrStmt><Stmt><Ntry>&lol2;</Ntry></Stmt></BkToCstmrStmt></Document>""",
        encoding="utf-8",
    )
    with pytest.raises(ConnectorError, match="invalid or unsafe XML"):
        read_source("bomb", xml_bomb, "camt")

    bad_sign = tmp_path / "neg_amt.xml"
    bad_sign.write_text(
        "<Document><BkToCstmrStmt><Stmt><Ntry>"
        '<Amt Ccy="USD">-10.00</Amt><CdtDbtInd>DBIT</CdtDbtInd>'
        "<Sts><Cd>BOOK</Cd></Sts><ValDt><Dt>2026-11-01</Dt></ValDt>"
        "</Ntry></Stmt></BkToCstmrStmt></Document>",
        encoding="utf-8",
    )
    with pytest.raises(ConnectorError, match="must be unsigned"):
        read_source("bad", bad_sign, "camt")


def test_stripe_fixture_timezone_and_duplicate_rows(tmp_path: Path) -> None:
    stripe = read_source("stripe", FIXTURES / "stripe_payouts.csv", "stripe")
    assert len(stripe.records) == 3  # payout row excluded by default
    assert sum(r.amount for r in stripe.records) == 20000
    assert stripe.records[0].id == "stripe:txn_101"
    assert stripe.records[0].attributes["payout_id"] == "po_1001"
    assert stripe.records[0].value_date == date(2026, 11, 2)

    gross_with_payout = read_source(
        "stripe",
        FIXTURES / "stripe_payouts.csv",
        "stripe",
        amount_field="gross",
        exclude_categories=(),
    )
    assert len(gross_with_payout.records) == 4
    assert gross_with_payout.records[0].amount == 15000

    # Timezone conversion: 2026-11-03 03:00:00 UTC -> 2026-11-02 in America/New_York
    tz_csv = tmp_path / "stripe_tz.csv"
    tz_csv.write_text(
        "balance_transaction_id,created,reporting_category,currency,net,automatic_payout_id\n"
        "txn_1,2026-11-03 03:00:00,charge,usd,10.00,po_1001\n"
        "txn_1,2026-11-03 03:00:00,charge,usd,10.00,po_1001\n",
        encoding="utf-8",
    )
    tz_source = read_source("stripe", tz_csv, "stripe", timezone="America/New_York")
    assert tz_source.records[0].value_date == date(2026, 11, 2)
    assert tz_source.records[0].id == "stripe:txn_1"
    assert tz_source.records[1].id == "stripe:txn_1@3"

    # Engine deduplicates the repeated transaction line cleanly.
    bank = read_source("bank", FIXTURES / "bank_camt053.xml", "camt")
    res = reconcile(
        [tz_source, bank],
        [Rule(id="r", left="stripe", right="bank")],
    )
    breaks = {b.record_id: b for b in res.breaks}
    assert breaks["stripe:txn_1@3"].reason is BreakReason.DUPLICATE


def test_stripe_to_bank_end_to_end_with_fixtures() -> None:
    stripe = read_source("stripe", FIXTURES / "stripe_payouts.csv", "stripe")
    bank = read_source("bank", FIXTURES / "bank_camt053.xml", "camt")
    rule = Rule(
        id="payout-to-bank",
        left="stripe",
        right="bank",
        kind=RuleKind.MANY_TO_ONE,
        group=GroupSpec(by="payout_id"),
        date=DateCondition(min_days=0, max_days=3),
        references=(ReferenceCondition(left="payout_id", right="description", op="left_in_right"),),
    )
    result = reconcile([stripe, bank], [rule])
    assert len(result.matches) == 1
    match = result.matches[0]
    assert match.left_ids == ("stripe:txn_101", "stripe:txn_102", "stripe:txn_103")
    assert match.right_ids == ("bank:1",)
    assert match.difference == 0
    assert {b.record_id for b in result.breaks} == {"bank:2", "bank:4"}
