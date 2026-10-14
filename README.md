# Finance spend intelligence assistant

A prototype for the Procurement Controls team: a pipeline cleans the invoice feed and flags policy breaches
and a small web app where an analyst asks questions answered from the policy and the invoice data. The answers always have
evidence shown next to it. I also created an eval set of 10 questions with a sinmple python-based scoring script.

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

**LLM path:** Claude (`claude-opus-5-5`) through the Anthropic API. The tests replace the model with a
scripted fake. Without a key the data quality page still works and questions return a clear error.

## Part 3 - Engineering notes

### Architecture and key decisions

1. **Overview of what was built:** a pipeline that keeps every raw row and logs every change; a 5 money-related policy rules (the ones that would
  have more impact in the team operations), and an assistant where Claude answers using two read-only retrieval tools (policy search and data search/SQL).
  Usually 2 rounds, one for the model to fetch the evidence and one to write the answer based on the data fetched, capped at 6 rounds; it takes no other action.
  The UI was built to show each answer with its evidence, always, no matter if the data came from the policy or from the cleaned data in the DB.

2. **The model writes the SQL**: people ask more questions than fixed queries can cover.
  The risk is controlled by code, not by the prompt: the database file is opened read-only, a SQLite authorizer allows only SELECT,
  and each query is limited to 5 seconds and 100 rows. The SQL can still be wrong, so it is always shown with its rows for every question/answer.

4. **RAG by keyword search (BM25) and not embeddings:** the procurement policy is short and relies on exact terms and clause
  numbers (§2.1, §4.1, "PO", "ON_HOLD", "maverick", etc.), so keyword search finds the right clause, is easy to explain and needs no
  extra service. Disadvantages of a non semantic search are, of course, the synonyms, but the model can search again with other words.
  If the system grows to hundreds of global policies and handbooks, the next step is hybrid search (BM25 + embeddings) with a cross-encoder
  reranker, to handle synonyms and references across documents.

5. **Grounding is checked by code:**: the prompt requires every number to come from a SQL result, and code compares each citation with what the tools really returned
  (a cited clause that was never retrieved, or a query that was never run, gets a warning). Caveats such as the FX source and the quarantined rows are added by code,
  not by the model. Not yet checked: that each number in the answer always appears in a query result.

7. **Not built for the sake of time or because if was not needed for this specific challenge:** login, chat memory, streaming, a stored log of questions, vector database, Docker,
  filters in the UI. Furthermore, it was not possible to do with the data provided: approver authority (§3, no approver roles), segregation of
  duties (§7, no requester), PO validity (no PO master data), late payment (no payment dates). A real ECB rates was not done as well (a flat
  table is used).

### How I would evaluate it

- **Pipeline:** the run now stops if a row is lost, i.e. every raw row must end up either clean or in quarantine; thus, if the counts do not add up (145 = 140 + 5),
  the run stops and writes nothing. On real data I would track the quarantine rate and the count per issue on every run, alert on jumps, and review a sample of
  fixed rows (`dq_log` keeps old and new values). 

- **Assistant:** 10 golden questions (`python -m evals.run_eval`), scored by code: the expected numbers must be in the answer, the expected clauses cited,
  and no grounding warnings. Result: 10 of 10 ([output/eval_results.json](output/eval_results.json)). I would go for a 100+ real questions,
  run on every prompt or model change before release.
  
- **Hallucination risk:** tracking the share of answers with 1) warnings, 2) with no citation or 3) with a number found in
  no query result, 4) with no answers because there was not enough data (to check that it refused to answer). This task must be done by hand periodicaly.
  An LLM judge may score wording but not the numbers.

### Path to production (200 finance users, real data)

- **Access control and security:** single sign-on; row-level access by company and cost center or team; SQL tool uses 
  read-only database with user limited to approved views. Security checks in the question, treating it as untrusted input.

- **Auditability and lineage (§8.2):** versioned policy documents; every question, its SQL, the clauses used and the answer stored in an append-only log with user id,
  pipeline run, policy version and model version, kept for 10 years (§8.1). Today the evidence is returned with
  each answer but not stored.

- **Monitoring:** track latency, cost per question, error and refusal rates, share of answers with warnings, and the eval pass rate on a scheduled run,
  with different test sets as the usage grows. 

- **Cost:** model routing with a smaller model for simple questions and heavier models for complex SQL generation; cache for repeated questions and semantic caching;
  implement per-user token and request quotas to prevent runaway API costs or abuse.

- **Governance:** ownership of the correct team, people should act on HOLD and ESCALATE. There shoukd be a check for the model provider's contract and
  its data region before real invoice data is sent to the LLM.

### Time spent and next steps

- Time spent: 7-8 hours
- What I would do next: ECB rates by invoice date; approver, requester and PO tables to check §3, §7 and PO validity; work on a semantic
  layer to have agreement on common variables definition; create a larger eval set with the Procurement Controls team.
