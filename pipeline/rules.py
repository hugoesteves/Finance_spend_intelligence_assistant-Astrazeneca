"""Compliance flags from the policy (FIN-POL-014).

Chosen rules: the ones where the company could pay what it should not, or pay more than it should.
Action levels: HOLD (do not pay) > ESCALATE (send to the owner named in the policy) > REVIEW (human check).
"""
from datetime import date

from .config import CREDIT_NOTE_ESCALATION_DAYS, PO_THRESHOLD_EUR

RANK = {"REVIEW": 1, "ESCALATE": 2, "HOLD": 3}


def evaluate(clean, as_of):
    flags = []

    def flag(r, rule, action, policy_ref, detail):
        flags.append(dict(source_row=r["source_row"], invoice_number=r["invoice_number"],
                          rule_code=rule, action_level=action, policy_ref=policy_ref, detail=detail))

    for r in clean:
        if r["approved_by"] is None:
            flag(r, "MISSING_APPROVER", "HOLD", "§3.1", "No approver recorded: the invoice may not be paid.")
        if r["vendor_status"] != "ACTIVE":
            flag(r, "VENDOR_NOT_ACTIVE", "ESCALATE", "§4.1",
                 f"Vendor is {r['vendor_status']}: escalate to Procurement Excellence before paying.")
        if r["po_number"] is None and abs(r["amount_eur"]) >= PO_THRESHOLD_EUR:
            flag(r, "MISSING_PO", "REVIEW", "§2.1, §2.2",
                 f"No PO on EUR {abs(r['amount_eur']):,.2f}: flag to the cost center owner, report quarterly.")

    # §6.1 Credit note (negative amount) with no original invoice: same vendor, currency and amount, not later.
    for r in clean:
        if r["amount_original"] >= 0:
            continue
        if any(p["vendor_id"] == r["vendor_id"] and p["currency"] == r["currency"]
               and p["amount_original"] == -r["amount_original"] and p["invoice_date"] <= r["invoice_date"]
               for p in clean):
            continue
        age = (as_of - date.fromisoformat(r["invoice_date"])).days
        flag(r, "UNMATCHED_CREDIT_NOTE", "ESCALATE" if age > CREDIT_NOTE_ESCALATION_DAYS else "REVIEW", "§6.1",
             f"Credit note with no original invoice, {age} days old (Finance Director after "
             f"{CREDIT_NOTE_ESCALATION_DAYS}).")

    # Possible duplicate payment (not in the policy; a standard accounts payable control): two invoices from
    # the same vendor with the same number, or the same date and description, or the same amount and currency.
    # Broad on purpose: REVIEW never blocks payment. Exact copies were already quarantined.
    for r in clean:
        twins = []
        for o in sorted(clean, key=lambda o: o["invoice_number"]):
            if o is r or o["vendor_id"] != r["vendor_id"]:
                continue
            same = [what for what, a, b in (
                ("number", o["invoice_number"], r["invoice_number"]),
                ("date and description", (o["invoice_date"], o["description"]), (r["invoice_date"], r["description"])),
                ("amount and currency", (o["amount_original"], o["currency"]), (r["amount_original"], r["currency"])),
            ) if a == b]
            if same:
                twins.append(f"{o['invoice_number']} (same {', '.join(same)})")
        if twins:
            flag(r, "POSSIBLE_DUPLICATE", "REVIEW", "Not in policy (AP control)",
                 f"Looks like {'; '.join(twins)}: check before paying twice.")
    return flags


def set_payment_actions(clean, flags):
    """One status per invoice: its strongest flag, or OK."""
    for r in clean:
        mine = [f["action_level"] for f in flags if f["source_row"] == r["source_row"]]
        r["payment_action"] = max(mine, key=RANK.get) if mine else "OK"
        r["flag_count"] = len(mine)
