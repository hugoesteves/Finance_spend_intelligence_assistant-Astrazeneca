"""Pipeline tests where they matter: no silent data loss, safe cleaning, and the rule thresholds."""
from decimal import Decimal
from pathlib import Path

import pytest

from pipeline.clean import clean_all, parse_date
from pipeline.config import AS_OF
from pipeline.rules import evaluate
from pipeline.run import run_pipeline

ROOT = Path(__file__).resolve().parent.parent
VENDORS = [dict(vendor_id="V-1001", vendor_name="Alpha GmbH", country="DE", category="X", status="ACTIVE")]


def raw(**kw):
    return dict(dict(invoice_number="INV-20001", invoice_date="2025-03-01", vendor_id="V-1001", vendor_name="Alpha GmbH",
                     description="Cloud hosting", amount="100.00", currency="EUR", cost_center="CC-1",
                     po_number="PO-70001", approved_by="j.smith"), **kw)


def test_real_data_reconciles_and_flag_counts_match(tmp_path):
    s = run_pipeline(ROOT / "data/invoices_2025.csv", ROOT / "data/vendor_master.csv", tmp_path / "s.db", tmp_path)
    assert s["reconciliation"] == dict(raw_rows=145, clean_rows=140, quarantined_rows=5, balanced=True)
    counts = {f["rule_code"]: f["invoices"] for f in s["compliance_flags"]}
    assert counts == {"MISSING_APPROVER": 25, "VENDOR_NOT_ACTIVE": 29, "MISSING_PO": 21,
                      "POSSIBLE_DUPLICATE": 2, "UNMATCHED_CREDIT_NOTE": 2}
    assert s["non_active_spend"]["total_eur"] == 1497797.48
    findings = {d["issue_code"]: d["findings"] for d in s["data_quality"]}   # the report explains each case
    assert "02/07/2025 read as 2 Jul 2025, not 7 Feb 2025" in findings["DATE_AMBIGUOUS"]


def test_dates_follow_the_separator_order_and_ambiguous_ones_are_flagged():
    assert parse_date("22.06.2025") == (parse_date("2025-06-22")[0], False)
    assert parse_date("03-18-2025")[0].isoformat() == "2025-03-18"     # '-' is month first
    assert parse_date("07.06.2025") == (parse_date("2025-06-07")[0], True)


def test_bad_rows_are_quarantined_not_dropped_and_fixes_are_logged():
    rows = [raw(), raw(), raw(invoice_number="INV-20002", vendor_id="V9999"),
            raw(invoice_number="INV-20003", vendor_id="", vendor_name="  ALPHA gmbh ", amount="1,250.00", currency="eur")]
    clean, quarantine, log = clean_all(rows, VENDORS)
    assert len(clean) + len(quarantine) == len(rows)
    assert [q["reason_code"] for q in quarantine] == ["DUPLICATE_ROW", "UNKNOWN_VENDOR_ID"]
    fixed = clean[1]
    assert fixed["vendor_id"] == "V-1001" and fixed["amount_original"] == Decimal("1250.00") and fixed["currency"] == "EUR"
    assert {e["issue_code"] for e in log if e["source_row"] == 4} == {
        "VENDOR_ID_MISSING", "VENDOR_NAME_FORMAT", "AMOUNT_FORMAT", "CURRENCY_CASE"}
    assert all(e["old_value"] is not None for e in log if e["action"] == "FIXED")


def test_unforeseen_problems_are_caught_not_crashing():
    rows = [raw(invoice_number=""), raw(invoice_number="INV-2", cost_center=" "),
            raw(invoice_number="INV-3", vendor_name=""),                       # id is fine: name filled from master
            raw(invoice_number="INV-4", amount=None),                          # short CSV row
            {**raw(invoice_number="INV-5"), "currency": 7}]                     # nobody foresaw this one
    clean, quarantine, log = clean_all(rows, VENDORS)
    assert len(clean) + len(quarantine) == len(rows)
    assert [q["reason_code"] for q in quarantine] == [
        "MISSING_INVOICE_NUMBER", "MISSING_COST_CENTER", "MISSING_AMOUNT", "UNEXPECTED_ERROR"]
    assert "VENDOR_NAME_MISSING" in {e["issue_code"] for e in log}

    odd_master = [dict(VENDORS[0], status="Active")]
    _, _, log = clean_all([raw()], odd_master)
    assert log[0]["issue_code"] == "VENDOR_STATUS_UNKNOWN"
    for bad_vendors, message in ((VENDORS * 2, "duplicate vendor ids"),
                                 ([{"vendor_id": "V-1001", "status": "ACTIVE"}], "missing column")):
        with pytest.raises(ValueError, match=message):
            clean_all([raw()], bad_vendors)


def test_po_threshold_is_1000_eur_inclusive():
    clean, _, _ = clean_all([raw(invoice_number="INV-1", amount="1000.00", po_number=""),
                             raw(invoice_number="INV-2", amount="999.99", po_number="", description="other")], VENDORS)
    flagged = {f["invoice_number"] for f in evaluate(clean, AS_OF) if f["rule_code"] == "MISSING_PO"}
    assert flagged == {"INV-1"}


def test_po_threshold_is_applied_after_fx_conversion():
    clean, _, _ = clean_all([raw(invoice_number="USD-1", amount="1100.00", currency="USD", po_number=""),   # EUR 968
                             raw(invoice_number="GBP-1", amount="900.00", currency="GBP", po_number="",      # EUR 1,053
                                 description="other")], VENDORS)
    flagged = {f["invoice_number"] for f in evaluate(clean, AS_OF) if f["rule_code"] == "MISSING_PO"}
    assert flagged == {"GBP-1"}
