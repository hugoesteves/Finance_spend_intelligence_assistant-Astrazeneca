"""Run the pipeline:  python -m pipeline.run

Reads the two CSV files, cleans and checks every invoice, writes one SQLite file and the report.
The database is rebuilt from scratch on every run, so running twice gives the same result.
"""
import csv
import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

from . import report
from .clean import clean_all
from .config import AS_OF, FX_SOURCE
from .rules import evaluate, set_payment_actions

SCHEMA = """
CREATE TABLE pipeline_runs (run_id TEXT, started_at TEXT, as_of TEXT, invoices_file TEXT, invoices_sha256 TEXT,
                            raw_rows INTEGER, clean_rows INTEGER, quarantined_rows INTEGER, fx_source TEXT);
-- Raw layer: the feed exactly as received (all text). Quarantined rows stay here, so nothing is deleted.
CREATE TABLE raw_invoices (source_row INTEGER PRIMARY KEY, invoice_number TEXT, invoice_date TEXT, vendor_id TEXT,
                           vendor_name TEXT, description TEXT, amount TEXT, currency TEXT, cost_center TEXT,
                           po_number TEXT, approved_by TEXT);
CREATE TABLE vendors (vendor_id TEXT PRIMARY KEY, vendor_name TEXT, country TEXT, category TEXT, status TEXT);
-- Clean layer: typed, joined to the vendor master. source_row links back to raw_invoices (lineage).
CREATE TABLE invoices (source_row INTEGER PRIMARY KEY, invoice_number TEXT, invoice_date TEXT, vendor_id TEXT,
                       vendor_name TEXT, vendor_status TEXT, vendor_category TEXT, description TEXT,
                       amount_original REAL, currency TEXT, fx_rate REAL, amount_eur REAL, cost_center TEXT,
                       po_number TEXT, approved_by TEXT, required_approver_role TEXT,
                       payment_action TEXT, flag_count INTEGER);
CREATE TABLE invoices_quarantine (source_row INTEGER PRIMARY KEY, invoice_number TEXT, reason_code TEXT, detail TEXT);
-- Every fix, flag and quarantine decision, with old and new value.
CREATE TABLE dq_log (source_row INTEGER, invoice_number TEXT, issue_code TEXT, action TEXT,
                     field TEXT, old_value TEXT, new_value TEXT, detail TEXT);
-- Compliance layer.
CREATE TABLE invoice_flags (source_row INTEGER, invoice_number TEXT, rule_code TEXT, action_level TEXT,
                            policy_ref TEXT, detail TEXT);
"""


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def insert(con, table, rows):
    if rows:
        cols = list(rows[0])
        con.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [[float(r[c]) if isinstance(r[c], Decimal) else r[c] for c in cols] for r in rows])


def run_pipeline(invoices_path, vendors_path, db_path, out_dir, as_of=AS_OF):
    raw_rows, vendors = read_csv(invoices_path), read_csv(vendors_path)
    clean, quarantine, dq_log = clean_all(raw_rows, vendors)
    flags = evaluate(clean, as_of)
    set_payment_actions(clean, flags)

    # No silent data loss: every raw row is in exactly one place, or nothing is written.
    if len(raw_rows) != len(clean) + len(quarantine):
        raise RuntimeError(f"Row count mismatch: {len(raw_rows)} raw != {len(clean)} clean + {len(quarantine)} quarantined")

    now = datetime.now(timezone.utc)
    with open(invoices_path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    run = dict(run_id=now.strftime("%Y%m%dT%H%M%SZ"), started_at=now.isoformat(timespec="seconds"),
               as_of=as_of.isoformat(), invoices_file=str(invoices_path), invoices_sha256=sha,
               raw_rows=len(raw_rows), clean_rows=len(clean), quarantined_rows=len(quarantine), fx_source=FX_SOURCE)

    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.executescript(SCHEMA)
        insert(con, "pipeline_runs", [run])
        insert(con, "raw_invoices", [{"source_row": i, **r} for i, r in enumerate(raw_rows, start=1)])
        insert(con, "vendors", vendors)
        insert(con, "invoices", clean)
        insert(con, "invoices_quarantine", quarantine)
        insert(con, "dq_log", dq_log)
        insert(con, "invoice_flags", flags)
        con.commit()
        con.row_factory = sqlite3.Row
        summary = report.summarise(con)
    finally:
        con.close()
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "dq_report.md"), "w", encoding="utf-8") as f:
        f.write(report.markdown(summary))
    return summary


def main():
    s = run_pipeline("data/invoices_2025.csv", "data/vendor_master.csv", "output/spend.db", "output")
    rec = s["reconciliation"]
    print(f"Rows: raw {rec['raw_rows']} = clean {rec['clean_rows']} + quarantined {rec['quarantined_rows']}")
    for f in s["compliance_flags"]:
        print(f"  {f['rule_code']:<22} {f['action_level']:<9} {f['invoices']:>3} invoices")
    print("Wrote output/spend.db and output/dq_report.md")


if __name__ == "__main__":
    main()
