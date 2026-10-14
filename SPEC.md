# Spec: Finance spend intelligence assistant

Starting spec for building this with Claude, step by step. Each step is built, tested and reviewed before
the next one starts. When something here is unclear or wrong, stop and ask; do not guess.

## 1. Goal

The Procurement Controls team reviews vendor invoices by hand against the procurement policy (FIN-POL-014).
Build a prototype that:

- **Part 1:** cleans the raw invoice feed, joins it to the vendor master and flags policy breaches.
- **Part 2:** lets a finance analyst ask questions in plain language and get answers built from the policy text
  and the invoice data, with the evidence shown next to every answer.

An analyst will only use an answer they can check. Every number and every policy statement must be traceable.

## 2. Inputs

| File | What it is |
|---|---|
| `data/invoices_2025.csv` | Raw AP extract, about 145 rows, messy on purpose |
| `data/vendor_master.csv` | Vendor id, name, country, category, status |
| `data/procurement_policy.md` | Policy excerpt, numbered clauses (§2.1, §3, §4.1, ...) |

The input files are never changed. The policy markdown stays as it is.

## 3. Constraints

- Time box: about 6 hours. Simple beats complete. Cut scope before cutting quality.
- Stack: Python + FastAPI backend, React + TypeScript frontend (Vite).
- Part 1 uses the Python standard library only.
- No frameworks for RAG or orchestration (no LangChain, no LlamaIndex, no vector DB).
- LLM: Claude through the Anthropic API. No mock mode. The API key lives only in `.env`, which is never committed.
- Tests where they protect a promise, not for coverage.
- No login, no Docker, no deployment.

## 4. Approach (build in this order)

1. **Explore the data first.** Before writing the pipeline, profile both files and list every kind of noise
   (date formats, amounts, currency codes, vendor ids and names, duplicates, empty fields). This list drives step 3.
2. **Store in SQLite.** One file, standard library, easy to inspect, and Part 2 can query it with plain SQL.
3. **Pipeline with clear handling per issue.** Each issue class gets one handling:
   - **Fix:** mechanical and certain (e.g. date format). Log old and new value.
   - **Flag:** keep the row, but a person should know (e.g. a date that can be read two ways).
   - **Quarantine:** the row cannot be used safely. Keep it in a separate table with a reason. Never delete.
   - **Reject:** the whole file is broken (empty, missing column). Stop the run, write nothing.
   - Rule: raw rows = clean rows + quarantined rows, checked on every run. No silent data loss.
4. **Policy rules as compliance flags, separate from data quality.** Pick the rules where money is at risk
   (pay what we should not, or pay twice). Each flag carries its policy clause and an action (HOLD, ESCALATE,
   REVIEW). Each invoice gets one status: its strongest flag.
5. **Policy retrieval: one chunk per clause.** Write a small splitter on the markdown headings, so each chunk has a
   clause number to cite. Keep the parent section title with each chunk.
6. **Keyword search (BM25), not embeddings.** The policy is short and uses exact terms and clause numbers.
   No vector DB.
7. **Grounded answers.** Claude gets two read-only retrieval tools: `search_policy` and `query_invoices`
   (one SELECT). Numbers come from SQL, never from the model. Answers cite clauses as `[§x.y]` and queries as
   `[Qn]`. Code, not the prompt, checks the citations against what the tools really returned.
8. **Frontend.** I describe the screens; Claude builds them; I review and iterate.
9. **Eval set.** 10 questions with expected facts, scored by a simple deterministic script.

## 5. Part 1 - Pipeline requirements

- One command: `python -m pipeline.run`. Rebuilds the database from scratch, so two runs give the same result.
- Tables: raw invoices as received, vendors, clean invoices, quarantine, a change log (`dq_log`) and compliance
  flags. Every table keeps `source_row` (the line in the raw file) for lineage (policy §8.2).
- Vendor join: by vendor id first (normalise the format); if the id is empty, only an exact name match.
  No fuzzy matching: a wrong match could pay the wrong company.
- Currencies converted to EUR with a stated FX table. Policy thresholds are applied to the EUR amount.
- All assumptions (FX rates, date order, as-of date, threshold rules) live in one config file.
- Unexpected errors on one row must not stop the run: the row goes to quarantine with the error message.
- Output: `output/dq_report.md` with the reconciliation, each issue with its handling, count and what was found,
  the compliance flags, the assumptions and the rules not implemented (and why).

## 6. Part 2 - Assistant requirements

**API (FastAPI)**

| Endpoint | Returns |
|---|---|
| `POST /api/ask {question}` | answer, status, policy sections used (cited or not), queries (SQL + rows), warnings, caveats |
| `GET /api/summary` | the Part 1 summary, read from the database |
| `GET /api/invoices/flagged` | flagged invoices with their flags |

One error shape everywhere: `{"error": {"code", "message"}}`.

**SQL guards (enforced by code)**

- Database opened read-only.
- Only SELECT allowed.
- Time limit and row limit per query.
- The SQL and its rows are always returned to the user.

**Tool loop**

- A normal question takes 2 rounds: fetch the evidence, then answer.
- Extra rounds let Claude fix a failed query or search again.
- Hard cap on rounds. When the cap is hit, the status is "incomplete" and nothing is invented.

**Grounding**

- The prompt says: use only tool results, no arithmetic in the head, and say so when the data cannot answer.
- Code adds warnings when:
  - no tool was used;
  - a cited clause was never retrieved;
  - a `[Qn]` has no query;
  - there are no citations.
- Caveats (FX source, quarantined rows left out, results cut at the row limit) are written by code, not the model.

**Frontend (React)**

- Chat page: question box, answer, and an "Evidence" panel with the clauses and the SQL with its rows.
- Clear loading and error states. One question at a time. No chat memory.
- Second page: data quality summary and the list of flagged invoices.

## 7. Evaluation

- `evals/eval_set.json`: 10 questions, including the three from the brief. Cover SQL totals, data + policy,
  policy lookups, a pipeline fact, and one question the data cannot answer (expect a refusal).
- `python -m evals.run_eval` scores each answer by code:
  - the expected numbers appear, matched as whole numbers;
  - the expected clauses were retrieved and cited;
  - there are no grounding warnings.
- No LLM judge for numbers.

## 8. Acceptance criteria

- [ ] Pipeline runs with one command and the row reconciliation holds.
- [ ] Every issue class in the data is documented in the report with its handling.
- [ ] Compliance flags carry a clause and an action; counts are checked by tests.
- [ ] `/api/ask` answers the three brief questions with correct numbers and cited clauses.
- [ ] Writes and unsafe SQL are blocked (tested).
- [ ] The UI shows the evidence for each answer.
- [ ] Tests pass without an API key (the model is replaced by a scripted fake).
- [ ] README: how to run, decisions, how to evaluate, path to production, time spent.

## 9. Out of scope

Login and access control, chat memory, streaming, a stored question log, vector DB, Docker, CI/CD,
real ECB rates, rules that need data we do not have (approver roles, requesters, PO master, payment dates).

## 10. Open questions (decide during step 1)

- Date order for dates like 03/04/2025: which convention does each format follow?
- FX: which rates, and how to state the source next to every EUR number?
- PO threshold: "EUR 1,000 or above" (§2.1) vs "above" (§2.2): which one, and is it inclusive?
- Duplicates: exact copies vs look-alikes; which ones to quarantine and which only to flag?
- Credit notes (negative amounts): how to age them and against which date?

## 11. How to work with Claude on this

- One step from section 4 at a time. Show a short plan, then the code, then the tests.
- Ask before choosing between two reasonable options.
- Keep files small and the code easy to explain line by line.
- After each step, update this spec if a decision changed.
