# Finance spend intelligence assistant

A prototype for the Procurement Controls team: a pipeline that cleans the invoice feed and flags policy breaches,
and a small web app where an analyst asks questions answered from the policy and the invoice data, with the
evidence shown next to every answer. Stretch item: an eval set of 10 questions.

**Outputs:**

- [Data quality and compliance report](output/dq_report.md) (`output/dq_report.md`): every issue found, how it was
  handled, the compliance flags and the assumptions.
- [Eval results](output/eval_results.json) (`output/eval_results.json`): the 10 eval questions, the answers and
  their scores (10 of 10).

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
- **Assistant (answer quality):** today 10 golden questions (`python -m evals.run_eval`), scored by code: the
  expected numbers must be in the answer and the expected clauses cited, with no grounding warnings. Result: 10 of
  10, see [output/eval_results.json](output/eval_results.json). Next: grow them to 50-100 real analyst questions,
  with expected numbers taken from the database, and run them on every prompt or model change as a release gate.
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
