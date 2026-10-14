"""Policy constants and assumptions. Everything a reviewer may want to challenge is in this file."""
from datetime import date
from decimal import Decimal

# ---- Policy constants (FIN-POL-014) ------------------------------------------------
PO_THRESHOLD_EUR = Decimal("1000")   # §2.1: a PO is required at EUR 1,000 or above
CREDIT_NOTE_ESCALATION_DAYS = 90     # §6.1: unmatched credit notes older than 90 days go to the Finance Director

# §3: (upper limit in EUR, required approver). None = no upper limit.
APPROVAL_TIERS = [
    (Decimal("5000"), "Cost Center Owner"),
    (Decimal("50000"), "Department Head"),
    (Decimal("250000"), "Finance Director"),
    (None, "CFO + Procurement Excellence review"),
]

# ---- Assumptions (NOT in the data) --------------------------------------------------
# Day/month order per separator. Checked on the raw file (145 rows): every date that can only be read one way
# agrees (24 with '.' and 20 with '/' are day-first, 25 with '-' are month-first). The DQ report counts
# the 140 clean rows only, so it shows slightly lower numbers (duplicates are not counted twice).
# Dates that could be read both ways follow this order and are flagged DATE_AMBIGUOUS.
DATE_ORDER = {".": "DMY", "/": "DMY", "-": "MDY"}

# Reference date for ageing rules (credit notes). Fixed, so every run gives the same result.
AS_OF = date(2025, 12, 31)

# Flat rates close to 2025 averages. Policy §5.1 asks for the ECB rate on the invoice date.
FX_TO_EUR = {"EUR": Decimal("1"), "USD": Decimal("0.88"), "GBP": Decimal("1.17")}
FX_SOURCE = "STATIC ASSUMPTION (flat rates, not ECB daily rates)"
