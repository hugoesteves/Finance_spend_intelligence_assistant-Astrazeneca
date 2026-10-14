"""Raw invoice rows -> clean rows + quarantine + a log of every change.

Rule: no row is dropped silently. Each raw row ends up in exactly one place:
  - clean       (possibly with fixes; each fix is written to the log with old and new value), or
  - quarantine  (with a reason; the raw text stays in the raw_invoices table).
Reject = the whole file is refused before any row is read (see check_files). Single rows are never
rejected: a row that cannot be used goes to quarantine instead, so it is not lost.
"""
import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from .config import APPROVAL_TIERS, DATE_ORDER, FX_TO_EUR

INVOICE_COLUMNS = ["invoice_number", "invoice_date", "vendor_id", "vendor_name", "description", "amount",
                   "currency", "cost_center", "po_number", "approved_by"]
VENDOR_COLUMNS = ["vendor_id", "vendor_name", "country", "category", "status"]
REQUIRED_FIELDS = ["invoice_number", "invoice_date", "amount", "currency", "cost_center"]   # empty -> quarantine
VENDOR_STATUSES = {"ACTIVE", "ON_HOLD", "INACTIVE"}

SEP_DATE = re.compile(r"(\d{2})([./-])(\d{2})\2(\d{4})")      # 22.06.2025, 22/06/2025, 03-18-2025
AMOUNT = re.compile(r"-?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?")      # 1234.50 or 1,234.50
VENDOR_ID = re.compile(r"V-?(\d{4})")                          # V1002 or V-1002


class QuarantineRow(Exception):
    """The row cannot be cleaned safely. It goes to quarantine with this reason."""

    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code


def parse_date(raw):
    """Return (ISO date, ambiguous). The day/month order per separator is a checked assumption (config.py)."""
    s = raw.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return date.fromisoformat(s), False
    m = SEP_DATE.fullmatch(s)
    if not m:
        raise QuarantineRow("INVALID_DATE", f"unrecognised date '{raw}'")
    a, sep, b, year = int(m[1]), m[2], int(m[3]), int(m[4])
    day, month = (a, b) if DATE_ORDER[sep] == "DMY" else (b, a)
    try:
        parsed = date(year, month, day)
    except ValueError:
        raise QuarantineRow("INVALID_DATE", f"impossible date '{raw}'")
    return parsed, (a <= 12 and b <= 12 and a != b)      # could be read both ways


def parse_amount(raw):
    if not AMOUNT.fullmatch(raw.strip()):
        raise QuarantineRow("INVALID_AMOUNT", f"unrecognised amount '{raw}'")
    return Decimal(raw.strip().replace(",", ""))


def norm_name(name):
    """Trim, collapse spaces, ignore case. Used only to match names."""
    return " ".join(name.split()).casefold()


def required_approver(amount_eur):
    """Policy §3: who must approve an invoice of this size."""
    for upper, role in APPROVAL_TIERS:
        if upper is None or abs(amount_eur) <= upper:
            return role


def check_files(raw_rows, vendors):
    """REJECT the whole file, with a clear message, if it does not have the expected shape. Nothing is written."""
    for name, rows, columns in (("invoices", raw_rows, INVOICE_COLUMNS), ("vendor master", vendors, VENDOR_COLUMNS)):
        if not rows:
            raise ValueError(f"The {name} file has no rows.")
        if missing := [c for c in columns if c not in rows[0]]:
            raise ValueError(f"The {name} file is missing column(s): {', '.join(missing)}")
    ids = [v["vendor_id"] for v in vendors]
    if dupes := sorted({i for i in ids if ids.count(i) > 1}):
        raise ValueError(f"The vendor master has duplicate vendor ids: {', '.join(dupes)}. Fix it at the source.")


def clean_row(raw, vendors_by_id, vendors_by_name, log):
    """Clean one row. Calls log(issue, action, field, old, new) for every change. Raises QuarantineRow."""
    for field in REQUIRED_FIELDS:
        if not raw[field].strip():
            raise QuarantineRow(f"MISSING_{field.upper()}", f"{field} is empty")

    parsed, ambiguous = parse_date(raw["invoice_date"])
    iso = parsed.isoformat()
    if iso != raw["invoice_date"].strip():
        log("DATE_FORMAT", "FIXED", "invoice_date", raw["invoice_date"], iso)
    if ambiguous:
        log("DATE_AMBIGUOUS", "FLAGGED", "invoice_date", raw["invoice_date"], iso)

    amount = parse_amount(raw["amount"])
    if "," in raw["amount"]:
        log("AMOUNT_FORMAT", "FIXED", "amount", raw["amount"], str(amount))

    currency = raw["currency"].strip().upper()
    if currency not in FX_TO_EUR:
        raise QuarantineRow("UNKNOWN_CURRENCY", f"'{raw['currency']}' has no FX rate")
    if currency != raw["currency"]:
        log("CURRENCY_CASE", "FIXED", "currency", raw["currency"], currency)

    # Vendor join: by id when there is one, else by exact (normalised) name. Never a guess.
    raw_id = raw["vendor_id"].strip()
    if raw_id:
        m = VENDOR_ID.fullmatch(raw_id.upper())
        vendor = vendors_by_id.get(f"V-{m[1]}") if m else None
        if vendor is None:
            raise QuarantineRow("UNKNOWN_VENDOR_ID", f"'{raw_id}' is not in the vendor master")
        if raw_id != vendor["vendor_id"]:
            log("VENDOR_ID_FORMAT", "FIXED", "vendor_id", raw_id, vendor["vendor_id"])
    else:
        vendor = vendors_by_name.get(norm_name(raw["vendor_name"]))
        if vendor is None:
            raise QuarantineRow("UNMATCHED_VENDOR", f"no vendor id, and '{raw['vendor_name']}' is not in the master")
        log("VENDOR_ID_MISSING", "FIXED", "vendor_id", "", vendor["vendor_id"])
    if not raw["vendor_name"].strip():
        log("VENDOR_NAME_MISSING", "FIXED", "vendor_name", "", vendor["vendor_name"])
    elif raw["vendor_name"] != vendor["vendor_name"]:
        log("VENDOR_NAME_FORMAT", "FIXED", "vendor_name", raw["vendor_name"], vendor["vendor_name"])

    rate = FX_TO_EUR[currency]
    amount_eur = (amount * rate).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return dict(
        invoice_number=raw["invoice_number"].strip(), invoice_date=iso,
        vendor_id=vendor["vendor_id"], vendor_name=vendor["vendor_name"],
        vendor_status=vendor["status"], vendor_category=vendor["category"],
        description=raw["description"].strip(),
        amount_original=amount, currency=currency, fx_rate=rate, amount_eur=amount_eur,
        cost_center=raw["cost_center"].strip(),
        po_number=raw["po_number"].strip() or None,          # empty -> NULL; judged by the rules
        approved_by=raw["approved_by"].strip() or None,
        required_approver_role=required_approver(amount_eur),
    )


def clean_all(raw_rows, vendors):
    """Returns (clean, quarantine, dq_log). source_row = 1-based row number in the raw file."""
    check_files(raw_rows, vendors)
    by_id = {v["vendor_id"]: v for v in vendors}
    by_name = {norm_name(v["vendor_name"]): v for v in vendors}
    clean, quarantine, dq_log, seen = [], [], [], {}

    # Vendor master: a status we do not know is logged, and the vendor is treated as not ACTIVE (the safe side).
    for v in vendors:
        if v["status"] not in VENDOR_STATUSES:
            dq_log.append(dict(source_row=None, invoice_number=None, issue_code="VENDOR_STATUS_UNKNOWN",
                               action="FLAGGED", field="status", old_value=v["status"],
                               new_value="treated as not ACTIVE", detail=v["vendor_id"]))

    for source_row, raw in enumerate(raw_rows, start=1):
        raw = {k: (v or "") for k, v in raw.items() if k is not None}     # short rows: missing cells -> ""
        number = raw["invoice_number"].strip()
        events = []

        def log(issue, action, field=None, old=None, new=None, detail=None):
            events.append(dict(source_row=source_row, invoice_number=number, issue_code=issue,
                               action=action, field=field, old_value=old, new_value=new, detail=detail))

        try:
            row = clean_row(raw, by_id, by_name, log)
            # Exact duplicate after cleaning: keep the first copy, quarantine the later one.
            key = tuple(row[k] for k in ("invoice_number", "invoice_date", "vendor_id", "amount_original",
                                         "currency", "cost_center", "po_number", "approved_by", "description"))
            if key in seen:
                raise QuarantineRow("DUPLICATE_ROW", f"identical to source_row {seen[key]} after cleaning")
            seen[key] = source_row
        except Exception as e:
            # A known problem (QuarantineRow) or one nobody foresaw: either way the row is kept, with the reason.
            code = e.code if isinstance(e, QuarantineRow) else "UNEXPECTED_ERROR"
            detail = str(e) if isinstance(e, QuarantineRow) else f"{type(e).__name__}: {e}"
            quarantine.append(dict(source_row=source_row, invoice_number=number, reason_code=code, detail=detail))
            dq_log.append(dict(source_row=source_row, invoice_number=number, issue_code=code,
                               action="QUARANTINED", field=None, old_value=None, new_value=None, detail=detail))
            continue
        clean.append({**row, "source_row": source_row})
        dq_log.extend(events)

    return clean, quarantine, dq_log
