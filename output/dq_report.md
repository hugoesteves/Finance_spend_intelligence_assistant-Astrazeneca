# Data quality and compliance report

Run `20261014T211804Z`, file `data/invoices_2025.csv` (sha256 `b41bc956f1f3…`)

## 1. Row reconciliation (no silent data loss)

Raw **145** = clean **140** + quarantined **5**: balanced.

## 2. Data quality issues and handling

Fixed and flagged rows stay in `invoices`; every change is in `dq_log` with old and new value. Quarantined rows are not used but kept, with their reason.

**Reject** applies to the whole file: the run stops and writes nothing if a file is empty, misses a column, or the vendor master has a duplicate vendor id. No file was rejected in this run. Single rows are never rejected; quarantine is used instead, so no row is lost.

| Issue | Handling | Rows | Meaning | Findings |
|---|---|---|---|---|
| DUPLICATE_ROW | QUARANTINED | 5 | Identical to an earlier row after cleaning. First copy kept. | row 72 copies row 26; row 81 copies row 69; row 85 copies row 3; row 98 copies row 79; row 114 copies row 9 |
| DATE_FORMAT | FIXED | 118 | Date not in ISO format; converted to yyyy-mm-dd. | mm-dd-yyyy: 47, of which 24 can only be read this way (e.g. 03-18-2025 → 2025-03-18); dd.mm.yyyy: 41, of which 23 can only be read this way (e.g. 22.06.2025 → 2025-06-22); dd/mm/yyyy: 30, of which 20 can only be read this way (e.g. 13/06/2025 → 2025-06-13) |
| CURRENCY_CASE | FIXED | 18 | Currency code in lower case; upper-cased. | eur → EUR ×18 |
| VENDOR_ID_FORMAT | FIXED | 10 | Vendor id without hyphen (V1002); changed to V-1002. | V1002 → V-1002 ×3; V1010 → V-1010 ×2; V1008 → V-1008 ×2; V1005 → V-1005; and 2 more |
| VENDOR_NAME_FORMAT | FIXED | 9 | Vendor name with other capitals or spaces; replaced by the master name. | all capitals and extra spaces: 9 rows, 6 vendors (e.g. "  LABTECH SOLUTIONS GMBH " → Labtech Solutions GmbH) |
| AMOUNT_FORMAT | FIXED | 4 | Amount written with a thousands separator; separator removed. | Comma used as thousands separator (e.g. 20,249.63 → 20249.63) |
| VENDOR_ID_MISSING | FIXED | 4 | Vendor id empty; filled from an exact match on the vendor name. | Matched by exact name: ClinSupply Iberia S.L. → V-1002; PharmaPrint Studios → V-1006; BioReagent Direct → V-1008; Labtech Solutions GmbH → V-1001 |
| DATE_AMBIGUOUS | FLAGGED | 49 | Day and month both 12 or less; read with the separator's order (see assumptions). | mm-dd-yyyy: 23 (e.g. 07-06-2025 read as 6 Jul 2025, not 7 Jun 2025); dd.mm.yyyy: 16 (e.g. 03.07.2025 read as 3 Jul 2025, not 7 Mar 2025); dd/mm/yyyy: 10 (e.g. 02/07/2025 read as 2 Jul 2025, not 7 Feb 2025) |

## 3. Compliance flags (FIN-POL-014)

HOLD = do not pay, ESCALATE = send to the owner named in the policy, REVIEW = human check. One invoice can have several flags; it gets only the strongest (HOLD > ESCALATE > REVIEW). Invoices counts every invoice the rule flagged; the last column shows how many of them a stronger flag on the same invoice overrides (for example, from an inactive vendor and also with no approver: HOLD).

| Rule | Action | Policy | Invoices | Exposure (EUR) | Overridden by a stronger flag |
|---|---|---|---|---|---|
| MISSING_APPROVER | HOLD | §3.1 | 25 | 1,060,280.95 | - |
| VENDOR_NOT_ACTIVE | ESCALATE | §4.1 | 29 | 1,497,797.48 | 3 → HOLD |
| MISSING_PO | REVIEW | §2.1, §2.2 | 21 | 1,132,663.88 | 3 → HOLD, 4 → ESCALATE |
| POSSIBLE_DUPLICATE | REVIEW | Not in policy (AP control) | 2 | 15,365.99 | 1 → HOLD |
| UNMATCHED_CREDIT_NOTE | REVIEW | §6.1 | 2 | 78,739.79 | - |

**POSSIBLE_DUPLICATE** is not in the policy. It is a standard accounts payable (AP) control against paying the same bill twice. It flags two invoices from the same vendor with the same invoice number, or the same date and description, or the same amount and currency. The match is broad on purpose: the flag only asks for a human check (REVIEW) and never blocks payment, so a false alarm costs a few minutes while a missed duplicate costs the full amount. Its exposure is the total of all flagged invoices, not the amount that would be paid twice. Exact copies are not flagged here; they are quarantined in section 2.

Flagged in this run:

| Invoice | Detail |
|---|---|
| INV-20075 | Looks like INV-20084 (same date and description): check before paying twice. |
| INV-20084 | Looks like INV-20075 (same date and description): check before paying twice. |

Status per invoice (each invoice counted once, under its strongest flag):

| Status | Invoices | Amount (EUR) |
|---|---|---|
| HOLD | 25 | 1,060,280.95 |
| ESCALATE | 26 | 1,362,167.91 |
| REVIEW | 17 | 880,624.53 |
| OK | 72 | 3,743,758.10 |

Spend with vendors that are not ACTIVE (§4.1): **EUR 1,497,797.48**

## 4. Assumptions

- Date order per separator: '.' and '/' are day-first, '-' is month-first. Every unambiguous date in the file agrees.
- FX: flat illustrative rates (USD 0.88, GBP 1.17), not the ECB rate on the invoice date (§5.1). All EUR totals depend on them.
- PO rule uses 'EUR 1,000 or above' (§2.1) on the amount after conversion to EUR, so USD and GBP invoices are judged in EUR. §2.2 says 'above'; they differ only for exactly EUR 1,000.00.
- Credit notes age against a fixed date (2025-12-31), so every run gives the same result.
- A vendor id is filled only on an exact name match. No fuzzy matching: a wrong match could pay the wrong company.
- Money is stored as REAL rounded to cents (prototype). Production: an exact decimal type.

## 5. Rules not implemented (no data)

| Rule | Why |
|---|---|
| §3 approver authority | No table of approvers and their roles; only the required role is computed. |
| §7 segregation of duties | No requester field. |
| §2.1 PO validity, §2.3 blanket POs | No PO master; only checks that a PO number exists. |
| §5.2 payment terms | No receipt or payment dates. |
| §9 emergency exceptions | No Exception Register. |
