"""Data quality and compliance summary, read from the database (one source of truth).
Used for output/dq_report.md and by the API (GET /api/summary)."""
import re
from collections import Counter, defaultdict

from .config import DATE_ORDER

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

ISSUES = {   # what each issue means and how it was handled
    "DUPLICATE_ROW": "Identical to an earlier row after cleaning. First copy kept.",
    "INVALID_DATE": "Date cannot be read (unknown format or impossible date).",
    "INVALID_AMOUNT": "Amount is not a number in a known format.",
    "UNKNOWN_CURRENCY": "Currency has no FX rate.",
    "UNKNOWN_VENDOR_ID": "Vendor id is not in the vendor master.",
    "UNMATCHED_VENDOR": "No vendor id, and the name is not in the vendor master.",
    "MISSING_INVOICE_NUMBER": "Invoice number is empty.",
    "MISSING_INVOICE_DATE": "Invoice date is empty.",
    "MISSING_AMOUNT": "Amount is empty.",
    "MISSING_CURRENCY": "Currency is empty.",
    "MISSING_COST_CENTER": "Cost center is empty.",
    "UNEXPECTED_ERROR": "A problem no rule foresaw; the row is kept in quarantine with the error message.",
    "DATE_FORMAT": "Date not in ISO format; converted to yyyy-mm-dd.",
    "AMOUNT_FORMAT": "Amount written with a thousands separator; separator removed.",
    "CURRENCY_CASE": "Currency code in lower case; upper-cased.",
    "VENDOR_ID_FORMAT": "Vendor id without hyphen (V1002); changed to V-1002.",
    "VENDOR_ID_MISSING": "Vendor id empty; filled from an exact match on the vendor name.",
    "VENDOR_NAME_MISSING": "Vendor name empty; filled from the vendor master (the id matched).",
    "VENDOR_NAME_FORMAT": "Vendor name with other capitals or spaces; replaced by the master name.",
    "DATE_AMBIGUOUS": "Day and month both 12 or less; read with the separator's order (see assumptions).",
    "VENDOR_STATUS_UNKNOWN": "Vendor master status is not ACTIVE, ON_HOLD or INACTIVE; treated as not ACTIVE.",
}

ASSUMPTIONS = [
    "Date order per separator: '.' and '/' are day-first, '-' is month-first. Every unambiguous date in the file agrees.",
    "FX: flat illustrative rates (USD 0.88, GBP 1.17), not the ECB rate on the invoice date (§5.1). All EUR totals depend on them.",
    "PO rule uses 'EUR 1,000 or above' (§2.1) on the amount after conversion to EUR, so USD and GBP invoices are judged in EUR. §2.2 says 'above'; they differ only for exactly EUR 1,000.00.",
    "Credit notes age against a fixed date (2025-12-31), so every run gives the same result.",
    "A vendor id is filled only on an exact name match. No fuzzy matching: a wrong match could pay the wrong company.",
    "Money is stored as REAL rounded to cents (prototype). Production: an exact decimal type.",
]

NOT_IMPLEMENTED = [
    ("§3 approver authority", "No table of approvers and their roles; only the required role is computed."),
    ("§7 segregation of duties", "No requester field."),
    ("§2.1 PO validity, §2.3 blanket POs", "No PO master; only checks that a PO number exists."),
    ("§5.2 payment terms", "No receipt or payment dates."),
    ("§9 emergency exceptions", "No Exception Register."),
]

ORDER = "CASE {} WHEN 'HOLD' THEN 0 WHEN 'ESCALATE' THEN 1 WHEN 'REVIEW' THEN 2 ELSE 3 END"


def _q(con, sql):
    return [dict(r) for r in con.execute(sql)]


# ---- Findings: a short, readable account of the cases found for each issue (computed from dq_log) ----
def _long_date(iso):
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{d} {MONTHS[m - 1]} {y}"


def _date_findings(events, ambiguous):
    """Group dates by how they were written. For fixed dates, also count the ones that can only be read one
    way: they are the evidence for the date order. For ambiguous ones, show the reading and the alternative."""
    by_sep = defaultdict(list)
    for e in events:
        by_sep[next((c for c in e["old_value"] if not c.isdigit()), "?")].append(e)
    parts = []
    for sep, evs in sorted(by_sep.items(), key=lambda kv: -len(kv[1])):
        order = DATE_ORDER.get(sep)
        shape = (f"dd{sep}mm{sep}yyyy" if order == "DMY" else f"mm{sep}dd{sep}yyyy") if order else "other format"
        if ambiguous:
            ex = evs[0]
            y, m, d = ex["new_value"].split("-")
            parts.append(f"{shape}: {len(evs)} (e.g. {ex['old_value']} read as {_long_date(ex['new_value'])}, "
                         f"not {_long_date(f'{y}-{d}-{m}')})")
        else:
            clear = [e for e in evs if max(int(x) for x in re.findall(r"\d+", e["old_value"])[:2]) > 12]
            ex = (clear or evs)[0]
            parts.append(f"{shape}: {len(evs)}, of which {len(clear)} can only be read this way "
                         f"(e.g. {ex['old_value']} → {ex['new_value']})")
    return "; ".join(parts)


def _pairs(events, limit=4):
    """Most common 'old → new' changes, e.g. 'eur → EUR ×15; usd → USD ×3'."""
    c = Counter((e["old_value"] or "(empty)", e["new_value"]) for e in events)
    items = [f"{o} → {n}" + (f" ×{k}" if k > 1 else "") for (o, n), k in c.most_common(limit)]
    return "; ".join(items) + (f"; and {len(c) - limit} more" if len(c) > limit else "")


def _name_findings(events):
    """Say what was wrong with each vendor name: capitals, extra spaces or both."""
    def problem(o):
        traits = []
        if o.strip().isupper():
            traits.append("all capitals")
        if o != " ".join(o.split()):
            traits.append("extra spaces")
        return " and ".join(traits) or "other capitals"
    groups = defaultdict(list)
    for e in events:
        groups[problem(e["old_value"])].append(e)
    return "; ".join(f"{t}: {len(evs)} rows, {len({e['new_value'] for e in evs})} vendors "
                     f"(e.g. \"{evs[0]['old_value']}\" → {evs[0]['new_value']})" for t, evs in groups.items())


def _findings(code, events, vendor_names):
    if code in ("DATE_FORMAT", "DATE_AMBIGUOUS"):
        return _date_findings(events, ambiguous=code == "DATE_AMBIGUOUS")
    if code == "VENDOR_NAME_FORMAT":
        return _name_findings(events)
    if code == "AMOUNT_FORMAT":
        return f"Comma used as thousands separator (e.g. {events[0]['old_value']} → {events[0]['new_value']})"
    if code == "VENDOR_ID_MISSING":
        return "Matched by exact name: " + "; ".join(
            f"{vendor_names.get(e['new_value'], '?')} → {e['new_value']}" for e in events)
    if code == "DUPLICATE_ROW":
        return "; ".join(re.sub(r"identical to source_row (\d+) after cleaning", rf"row {e['source_row']} copies row \1",
                                e["detail"] or "") for e in events)
    if any(e["old_value"] or e["new_value"] for e in events):
        return _pairs(events)
    c = Counter(e["detail"] or "" for e in events)    # quarantine reasons and unexpected errors
    return "; ".join(d + (f" ×{k}" if k > 1 else "") for d, k in c.most_common(4))


def summarise(con):
    run = _q(con, "SELECT * FROM pipeline_runs")[0]
    rec = dict(raw_rows=run["raw_rows"], clean_rows=run["clean_rows"], quarantined_rows=run["quarantined_rows"])
    rec["balanced"] = rec["raw_rows"] == rec["clean_rows"] + rec["quarantined_rows"]

    # Vendor master issues have no invoice row: they count once per vendor (the vendor id is in `detail`).
    data_quality = _q(con, """SELECT issue_code, action,
                                     COUNT(DISTINCT COALESCE(source_row, 'vendor ' || detail)) AS rows_affected
                              FROM dq_log GROUP BY issue_code, action
                              ORDER BY CASE action WHEN 'QUARANTINED' THEN 0 WHEN 'FIXED' THEN 1 ELSE 2 END,
                                       rows_affected DESC""")
    events = defaultdict(list)
    for e in _q(con, "SELECT * FROM dq_log ORDER BY source_row"):
        events[(e["issue_code"], e["action"])].append(e)
    vendor_names = {v["vendor_id"]: v["vendor_name"] for v in _q(con, "SELECT vendor_id, vendor_name FROM vendors")}
    for d in data_quality:
        d["description"] = ISSUES.get(d["issue_code"], "")
        d["findings"] = _findings(d["issue_code"], events[(d["issue_code"], d["action"])], vendor_names)

    compliance = _q(con, f"""SELECT f.rule_code, f.action_level, f.policy_ref, COUNT(*) AS invoices,
                                    ROUND(SUM(ABS(i.amount_eur)), 2) AS exposure_eur
                             FROM invoice_flags f JOIN invoices i USING (source_row)
                             GROUP BY 1, 2, 3 ORDER BY {ORDER.format('f.action_level')}, invoices DESC""")
    # Flagged invoices whose final status comes from a stronger flag on the same invoice.
    overridden = defaultdict(list)
    for o in _q(con, f"""SELECT f.rule_code, f.action_level, i.payment_action, COUNT(*) AS invoices
                         FROM invoice_flags f JOIN invoices i USING (source_row)
                         WHERE i.payment_action <> f.action_level
                         GROUP BY 1, 2, 3 ORDER BY {ORDER.format('i.payment_action')}"""):
        overridden[(o["rule_code"], o["action_level"])].append(f"{o['invoices']} → {o['payment_action']}")
    for c in compliance:
        c["overridden"] = ", ".join(overridden[(c["rule_code"], c["action_level"])]) or "-"
    duplicates = _q(con, """SELECT invoice_number, detail FROM invoice_flags
                            WHERE rule_code = 'POSSIBLE_DUPLICATE' ORDER BY invoice_number""")
    by_status = _q(con, f"""SELECT payment_action, COUNT(*) AS invoices, ROUND(SUM(ABS(amount_eur)), 2) AS amount_eur
                            FROM invoices GROUP BY 1 ORDER BY {ORDER.format('payment_action')}""")
    non_active = _q(con, """SELECT vendor_name, vendor_status, COUNT(*) AS invoices, ROUND(SUM(amount_eur), 2) AS amount_eur
                            FROM invoices WHERE vendor_status <> 'ACTIVE' GROUP BY 1, 2 ORDER BY amount_eur DESC""")
    return dict(
        run=run, reconciliation=rec, data_quality=data_quality, compliance_flags=compliance,
        possible_duplicates=duplicates, payment_actions=by_status,
        non_active_spend=dict(total_eur=round(sum(v["amount_eur"] for v in non_active), 2), by_vendor=non_active),
        assumptions=ASSUMPTIONS,
        not_implemented=[dict(rule=r, reason=t) for r, t in NOT_IMPLEMENTED],
    )


def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    return "\n".join(lines + ["| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |" for r in rows])


def markdown(s):
    run, rec = s["run"], s["reconciliation"]
    return "\n".join([
        "# Data quality and compliance report", "",
        f"Run `{run['run_id']}`, file `{run['invoices_file']}` (sha256 `{run['invoices_sha256'][:12]}…`)", "",
        "## 1. Row reconciliation (no silent data loss)", "",
        f"Raw **{rec['raw_rows']}** = clean **{rec['clean_rows']}** + quarantined **{rec['quarantined_rows']}**: "
        f"{'balanced' if rec['balanced'] else 'NOT BALANCED'}.", "",
        "## 2. Data quality issues and handling", "",
        "Fixed and flagged rows stay in `invoices`; every change is in `dq_log` with old and new value. "
        "Quarantined rows are not used but kept, with their reason.", "",
        "**Reject** applies to the whole file: the run stops and writes nothing if a file is empty, misses a column, "
        "or the vendor master has a duplicate vendor id. No file was rejected in this run. "
        "Single rows are never rejected; quarantine is used instead, so no row is lost.", "",
        _table(["Issue", "Handling", "Rows", "Meaning", "Findings"],
               [(d["issue_code"], d["action"], d["rows_affected"], d["description"], d["findings"])
                for d in s["data_quality"]]), "",
        "## 3. Compliance flags (FIN-POL-014)", "",
        "HOLD = do not pay, ESCALATE = send to the owner named in the policy, REVIEW = human check. "
        "One invoice can have several flags; it gets only the strongest (HOLD > ESCALATE > REVIEW). "
        "Invoices counts every invoice the rule flagged; the last column shows how many of them a stronger flag "
        "on the same invoice overrides (for example, from an inactive vendor and also with no approver: HOLD).", "",
        _table(["Rule", "Action", "Policy", "Invoices", "Exposure (EUR)", "Overridden by a stronger flag"],
               [(c["rule_code"], c["action_level"], c["policy_ref"], c["invoices"], f"{c['exposure_eur']:,.2f}",
                 c["overridden"]) for c in s["compliance_flags"]]), "",
        "**POSSIBLE_DUPLICATE** is not in the policy. It is a standard accounts payable (AP) control against "
        "paying the same bill twice. It flags two invoices from the same vendor with the same invoice number, "
        "or the same date and description, or the same amount and currency. The match is broad on purpose: "
        "the flag only asks for a human check (REVIEW) and never blocks payment, so a false alarm costs a few "
        "minutes while a missed duplicate costs the full amount. Its exposure is the total of all flagged "
        "invoices, not the amount that would be paid twice. Exact copies are not flagged here; they are "
        "quarantined in section 2.", "",
        *(["Flagged in this run:", "",
           _table(["Invoice", "Detail"], [(d["invoice_number"], d["detail"]) for d in s["possible_duplicates"]]), ""]
          if s["possible_duplicates"] else []),
        "Status per invoice (each invoice counted once, under its strongest flag):", "",
        _table(["Status", "Invoices", "Amount (EUR)"],
               [(p["payment_action"], p["invoices"], f"{p['amount_eur']:,.2f}") for p in s["payment_actions"]]), "",
        f"Spend with vendors that are not ACTIVE (§4.1): **EUR {s['non_active_spend']['total_eur']:,.2f}**", "",
        "## 4. Assumptions", "", *[f"- {a}" for a in s["assumptions"]], "",
        "## 5. Rules not implemented (no data)", "",
        _table(["Rule", "Why"], [(n["rule"], n["reason"]) for n in s["not_implemented"]]), "",
    ])
