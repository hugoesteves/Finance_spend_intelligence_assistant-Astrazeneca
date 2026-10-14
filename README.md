# Finance spend intelligence assistant

A prototype for the Procurement Controls team: a pipeline that cleans the invoice feed and flags policy breaches
(Part 1), and a small web app where an analyst asks questions answered from the policy and the invoice data,
with the evidence shown next to every answer (Part 2). Stretch item: an eval set of 10 questions.

## Run it

```bash
pip install -r requirements.txt
python -m pipeline.run                 # writes output/spend.db and output/dq_report.md
python -m pytest -q                    # tests (no API key needed)

cp .env.example .env                   # put your ANTHROPIC_API_KEY here
uvicorn backend.main:app --reload      # API on http://localhost:8000
cd frontend && npm install && npm run dev   # UI on http://localhost:5173
```

**LLM path:** Claude (`claude-opus-5-5`) through the Anthropic API, no mock. The tests replace the model with a
scripted fake. Without a key the data quality page still works and questions return a clear error.

## Part 1 - Data pipeline

```
data/*.csv --> raw_invoices (as received) --> clean + join vendor master --> invoices (140)
                                                                         --> invoices_quarantine (5)
                                                                         --> dq_log (every fix: old -> new)
                                          --> policy rules               --> invoice_flags
```

- **No silent data loss.** Every raw row ends in `invoices` or `invoices_quarantine`, never neither. The run stops
  with an error if `raw != clean + quarantined` (145 = 140 + 5).
- **Fix, flag or quarantine.** Mechanical problems are fixed and logged with the old and new value (dates, amounts
  with commas, currency case, vendor ids). Doubts are flagged (49 dates where day and month are both 12 or less).
  Rows that cannot be used safely are quarantined with a reason (here: 5 exact duplicates).
- **Reject = the whole file.** The run stops with a clear message, and writes nothing, if a file is empty, misses
  a column, or the vendor master has a duplicate vendor id. No file was rejected in this run. Single rows are never
  rejected: quarantine is used instead, so no row is lost.
- **Prepared for problems nobody foresaw.** An empty required field (invoice number, date, amount,
  currency, cost center) sends the row to quarantine as `MISSING_<FIELD>`. Any other error on a row sends that row
  to quarantine as `UNEXPECTED_ERROR` with the message, and the run continues. A vendor status other than ACTIVE,
  ON_HOLD or INACTIVE is logged and treated as not ACTIVE (the safe side). None of these happen in this file.
- **Vendor join on purpose.** By id (normalised `V1002` -> `V-1002`); if the id is empty, only an exact name match.
  No fuzzy matching: a wrong match could pay the wrong company.
- **Why SQLite:** standard library, one file, easy to inspect, and Part 2 can query it with plain SQL.
- The full list of issues, the handling of each and what was found (for example how many dates were written
  each way) is in `output/dq_report.md`.

**Compliance rules.** I kept the rules where the company could pay what it should not, or pay more than it should:

| Rule | Policy | Action | Invoices |
|---|---|---|---|
| MISSING_APPROVER | §3.1 | HOLD (may not be paid) | 25 |
| VENDOR_NOT_ACTIVE | §4.1 | ESCALATE to Procurement Excellence | 29 (EUR 1,497,797.48) |
| MISSING_PO (EUR 1,000 or above) | §2.1, §2.2 | REVIEW, report quarterly | 21 |
| POSSIBLE_DUPLICATE | Not in policy (AP control) | REVIEW (risk of paying twice) | 2 |
| UNMATCHED_CREDIT_NOTE | §6.1 | REVIEW, ESCALATE after 90 days | 2 |

Each invoice gets one status: its strongest flag (HOLD > ESCALATE > REVIEW), or OK. Result: 25 HOLD, 26 ESCALATE,
17 REVIEW, 72 OK.

## Part 2 - Q&A assistant

| Endpoint | Returns |
|---|---|
| `POST /api/ask {question}` | `answer`, `status`, `policy_sections[]`, `queries[]` (SQL + rows), `warnings[]`, `caveats[]`, `meta` |
| `GET /api/summary` | The Part 1 summary, computed from the database on each call |
| `GET /api/invoices/flagged` | Flagged invoices, each with its flags |

Every error has one shape: `{"error": {"code", "message"}}`.

```
question --> Claude, two read-only retrieval tools:  search_policy(query)          BM25 search, one chunk per policy clause
                                                     query_invoices(purpose, sql)  one read-only SELECT on spend.db
         --> answer citing [§2.2] (clause) and [Q1] (query)
         --> code compares the citations with what the tools returned --> warnings
```

The UI is a chat where each answer has an "Evidence" box with the clauses and the SQL and rows that were really
used, and a second page with the data quality summary and the flagged invoices.

```
pipeline/  config.py (policy constants, assumptions) | clean.py | rules.py | report.py | run.py
backend/   config.py | tools.py (policy search, read-only SQL) | assistant.py (prompt, tool loop, citation check)
           schemas.py (API contract) | main.py (FastAPI)
frontend/  src/api.ts | App.tsx | Chat.tsx | Compliance.tsx
evals/     eval_set.json (10 questions) | run_eval.py
```

## Eval set (stretch item)

`python -m evals.run_eval` sends 10 golden questions to the real model. They cover five types: SQL totals, data
plus policy, policy lookups, a pipeline fact, and a refusal where the data does not exist. Each answer is scored by
code, not by a model: the expected numbers must be in the text (taken from the database, matched as whole numbers),
the expected clauses must be retrieved and cited, and there must be no grounding warnings.

| ID | Question | Type | What it proves |
|---|---|---|---|
| E01 ★ | Total 2025 spend with non-ACTIVE vendors? | Data: SQL total | Exact EUR 1,497,797.48, cites §4.1 |
| E02 ★ | Invoices over EUR 1,000 with no PO, and what does the policy say? | Data + policy | Count 21, cost center owner, quarterly report, cites §2.1 and §2.2 |
| E03 ★ | Who can approve EUR 60,000? | Policy lookup | Finance Director, finds the §3 table |
| E04 | Who approves above EUR 250,000? | Policy lookup (top band) | CFO + Procurement Excellence, §3 |
| E05 | How many invoices have no approver, can they be paid? | Data + policy | 25, must be held, §3.1 |
| E06 | Spend with INACTIVE vendors, and what must happen? | Data + policy | EUR 1,084,083.07, escalate, §4.1 |
| E07 | ON_HOLD vendors: how many invoices, what total? | Data: count + total | 12 and EUR 413,714.41, §4.1 |
| E08 | Standard payment terms? | Policy lookup | 60 days, §5.2 |
| E09 | How many rows were quarantined, and why? | Pipeline fact | 5 duplicates |
| E10 | Which invoices were paid late, who requested j.smith's approvals? | Refusal: data not available | Says it cannot answer, invents nothing |

★ = the brief's own example questions.

**Result: 10 of 10** on the current version (`output/eval_results.json`). The first run, on an earlier version,
scored 9/10: the citation check counted a clause the answer said it had not read, so now only bracketed references
count as citations.

## Part 3 - Engineering notes

### Architecture and key decisions

- **Built:** a pipeline that keeps every raw row and logs every change, 5 money-related policy rules, and an
  assistant where Claude answers using two read-only retrieval tools (policy search and SQL). Usually 2 rounds, one to
  fetch the evidence and one to write the answer, capped at 6; it takes no actions. The UI shows each answer with its evidence.
- **The model writes the SQL**, because analysts ask more questions than fixed queries can cover. The risk is
  controlled by code, not by the prompt: read-only file, SELECT-only authorizer, 5-second and 100-row limits.
  The SQL can still be wrong, so it is always shown with its rows.
- **Keyword search (BM25), not embeddings:** the procurement policy is short and relies on exact terms and clause
  numbers (§2.1, §4.1, "PO", "ON_HOLD"), so keyword search finds the right clause, is easy to explain and needs no
  extra service. Weak point: synonyms; the model can search again with other words. If the system grows to hundreds
  of global policies and handbooks, the next step is hybrid search (BM25 + embeddings) with a cross-encoder
  reranker, to handle synonyms and references across documents.
- **Grounding is checked by code:** numbers must come from SQL, citations are compared with the tool results,
  and caveats (FX source, quarantined rows) are added by code, not by the model.
- **Not built, on purpose:** login, chat memory, streaming, a stored log of questions, vector database, Docker,
  filters in the UI. **Not possible with this data:** approver authority (§3, no approver roles), segregation of
  duties (§7, no requester), PO validity (no PO master), late payment (no payment dates), real ECB rates (a flat
  table is used and stated next to every number).

### How I would evaluate it

- **Pipeline (data quality):** the reconciliation check already fails the run if a row is lost. Tests fix the
  flag counts per rule on the real file. On real data: track the quarantine rate and the count per issue on every
  run and alert on jumps; review a sample of fixed rows (`dq_log` has old and new values). A finance owner must
  confirm the two biggest assumptions: the date order for ambiguous dates (49 invoices) and the FX table.
- **A check I tested and left out: invoice number vs. date order.** Invoice numbers are written by the supplier,
  so a sequence can only be expected per vendor. In this file, sorted by invoice number, the next invoice has an
  earlier date in 60 of 130 pairs per vendor (46%), so a rule would flag about half the invoices and tell the
  analyst nothing. On real data I would run it per vendor and alert only on large jumps.
- **Assistant (answer quality):** grow the 10 questions to 50-100 real analyst questions, with expected numbers
  taken from the database, and run them on every prompt or model change as a release gate.
- **Hallucination risk:** measure the share of answers with grounding warnings, with no citation, and with numbers
  that appear in no query result. Keep refusal cases (data that does not exist). Review a weekly sample by hand.
  Use an LLM judge only for wording, never for numbers.

### Path to production (200 finance users, real data)

- **Access control and security:** single sign-on; row-level access by company and cost center; the SQL tool uses a
  read-only database user limited to approved views. Treat the question text as untrusted input.
- **Auditability and lineage (§8.2):** a managed warehouse with scheduled, incremental runs; versioned policy
  documents; every question, its SQL, the clauses used and the answer stored in an append-only log with user id,
  pipeline run, policy version and model version, kept for 10 years (§8.1). Today the evidence is returned with
  each answer but not stored.
- **Monitoring:** latency, cost per question, error and refusal rates, share of answers with warnings, and the eval
  pass rate on a schedule.
- **Cost:** a smaller model for simple questions, a cache for repeated questions, a token cap per user.
- **Governance:** the assistant advises; people act on HOLD and ESCALATE. Check the model provider's contract and
  data region before real invoice data is sent.

### Time spent and next steps

- **Time spent:** [fill in: hours]
- **Next:** ECB rates by invoice date; approver, requester and PO tables to check §3, §7 and PO validity; a semantic
  layer so common numbers ("spend", "maverick spend") have one agreed definition; a larger eval set built with the
  Procurement Controls team.
