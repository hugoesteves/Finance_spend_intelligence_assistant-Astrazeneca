"""The Q&A assistant: Claude with two tools, policy search and read-only SQL.

One question:
  1. Claude decides which tools it needs and calls them (up to 6 rounds).
  2. We run each tool and keep our own copy of what it returned. That copy is the evidence.
  3. Claude answers and cites the evidence: [§2.2] for a clause, [Q1] for a query.
  4. Code compares the citations with the evidence and adds warnings when they do not match.
Each question is answered on its own (no chat memory), so each answer can be checked on its own.
"""
import json
import os
import re
import time

import anthropic

from . import config, tools

SYSTEM_PROMPT = """You are a finance spend assistant for the Procurement Controls team. Finance analysts \
ask you about vendor invoices and about the Global Procurement & Spend Policy (FIN-POL-014).

You have two tools:
- search_policy: finds clauses of the policy. Use it for any question about rules, thresholds, who may \
approve, or what must be done.
- query_invoices: runs one read-only SQLite SELECT on the cleaned 2025 invoice data. Use it for any \
question about amounts, counts, vendors or specific invoices.
Many questions need both. Call the tools you need before you answer.

Grounding rules (these matter more than a complete answer):
- Every policy statement must come from a clause returned by search_policy. Cite it like [§2.2].
- Every number or invoice list must come from a query_invoices result. Cite it like [Q1].
- Do not use general knowledge about procurement, and do not do arithmetic in your head: let SQL \
compute totals and counts.
- If the tools do not give you what is needed, say plainly what is missing and what the analyst could \
do next. Do not guess. Things this data cannot answer include: who a named approver is or what role \
they hold, purchase requesters, PO validity, payment dates, and the Exception Register.
- A policy rule tells what should happen. Do not claim that an action was taken.

Database (SQLite). Use these tables only:
invoices: one row per clean invoice.
  source_row (row number in the raw file), invoice_number, invoice_date (ISO yyyy-mm-dd),
  vendor_id, vendor_name, vendor_status (ACTIVE | INACTIVE | ON_HOLD), vendor_category,
  description, amount_original, currency (EUR | USD | GBP), fx_rate, amount_eur,
  cost_center, po_number (NULL = no PO), approved_by (NULL = no approver),
  required_approver_role (approver the policy requires for this amount),
  payment_action (HOLD | ESCALATE | REVIEW | OK, the strongest flag), flag_count
invoice_flags: one row per compliance flag. source_row, invoice_number,
  rule_code (MISSING_PO | MISSING_APPROVER | VENDOR_NOT_ACTIVE | UNMATCHED_CREDIT_NOTE | POSSIBLE_DUPLICATE),
  action_level (HOLD | ESCALATE | REVIEW), policy_ref, detail
vendors: vendor_id, vendor_name, country, category, status
invoices_quarantine: rows the pipeline could not load. source_row, invoice_number, reason_code, detail
dq_log: every data fix. source_row, invoice_number, issue_code, action, field, old_value, new_value

SQL notes:
- Use amount_eur for totals and for thresholds in EUR. Negative amounts are credit notes.
- Join on source_row, not invoice_number (an invoice number can appear twice).
- A flag is the pipeline's reading of a policy rule. If the question uses a different threshold or \
definition, filter the invoices table yourself.
- Results are capped at {max_rows} rows. For totals and counts use SUM and COUNT, and when you list \
invoices also run a COUNT so the answer states the full number.

Answer format: short and direct, in plain language. Lead with the answer. Use a small markdown table \
for up to 10 invoices; for longer lists give the count, the total and the top few, and say the full \
list is in the evidence. Amounts as EUR 1,234.56."""

TOOLS = [
    {
        "name": "search_policy",
        "description": "Search the Global Procurement & Spend Policy (FIN-POL-014). Returns the best matching "
                       "clauses with their clause number. Use the words the policy would use, for example "
                       "'purchase order threshold maverick spend' or 'approval thresholds approver'. "
                       "Search again with other words if the first result does not cover the question.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Keywords to search for."}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "query_invoices",
        "description": "Run one read-only SQLite SELECT statement on the invoice database and get the rows back.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "purpose": {"type": "string",
                            "description": "One short sentence, shown to the analyst: what this query answers."},
                "sql": {"type": "string", "description": "A single SELECT statement."},
            },
            "required": ["purpose", "sql"],
            "additionalProperties": False,
        },
    },
]


CITATION = re.compile(r"\[([^\]]+)\]")         # only text in brackets counts as a citation


class AssistantError(Exception):
    """A failure we can explain to the user. The API turns it into {"error": {code, message}}."""

    def __init__(self, code, message, status=502):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def answer_question(question, index, client=None):
    started = time.monotonic()
    client = client or _client()
    sections, queries = {}, []                  # the evidence: what the tools really returned
    messages = [{"role": "user", "content": question}]
    tokens = dict(input_tokens=0, output_tokens=0)
    status, answer, rounds = "incomplete", "", 0

    for rounds in range(1, config.MAX_TOOL_ROUNDS + 1):
        response = _call_model(client, messages)
        tokens["input_tokens"] += response.usage.input_tokens
        tokens["output_tokens"] += response.usage.output_tokens
        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": [_run_tool(b, index, sections, queries)
                                                         for b in response.content if b.type == "tool_use"]})
            continue
        if response.stop_reason == "refusal":
            status, answer = "refused", "The model declined to answer this question. Try rephrasing it."
        else:
            answer = "".join(b.text for b in response.content if b.type == "text").strip()
            status = "answered" if response.stop_reason == "end_turn" and answer else "incomplete"
        break
    if status == "incomplete":
        answer = answer or "I could not finish within the allowed number of steps. The evidence so far is below."

    sections = sorted(sections.values(), key=lambda s: -s["score"])
    run = tools.fetch_all("SELECT * FROM pipeline_runs")[0]
    return dict(
        question=question, answer=answer, status=status, policy_sections=sections, queries=queries,
        warnings=check_citations(answer, sections, queries) if status == "answered" else [],
        caveats=_caveats(run, queries),
        meta=dict(model=response.model, pipeline_run_id=run["run_id"], data_as_of=run["as_of"],
                  latency_ms=int((time.monotonic() - started) * 1000), tool_rounds=rounds, **tokens),
    )


def _run_tool(block, index, sections, queries):
    """Run one tool call and record the result. A failed query goes back to the model so it can fix it."""
    content, is_error = "", False
    if block.name == "search_policy":
        hits = index.search(block.input["query"], config.POLICY_TOP_K)
        for chunk, score in hits:
            if score > sections.get(chunk["id"], {}).get("score", -1):
                sections[chunk["id"]] = dict(**chunk, score=score, query=block.input["query"], cited=False)
        content = "\n\n".join(f"[{c['id']}] {c['title']}\n{c['text']}" for c, _ in hits) or \
            "No policy clause matches these words. Try other words, or say the policy excerpt does not cover it."
    elif block.name == "query_invoices":
        q = dict(id=f"Q{len(queries) + 1}", purpose=block.input["purpose"], sql=block.input["sql"], columns=[],
                 rows=[], row_count=0, truncated=False, error=None, cited=False)
        queries.append(q)
        try:
            q.update(tools.run_select(q["sql"]))
            content = json.dumps({k: q[k] for k in ("id", "columns", "rows", "row_count", "truncated")}, default=str)
        except tools.QueryError as e:
            q["error"], content, is_error = str(e), f"{q['id']} failed. {e}", True
    return {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error}


def check_citations(answer, sections, queries):
    """Compare the citations in the answer with the evidence. Sets `cited` on each evidence item."""
    groups = CITATION.findall(answer)
    cited_s = {f"§{n}" for g in groups for n in re.findall(r"§\s?(\d+(?:\.\d+)?)", g)}
    cited_q = {f"Q{n}" for g in groups for n in re.findall(r"\bQ(\d+)\b", g)}
    for s in sections:
        s["cited"] = s["id"] in cited_s
    for q in queries:
        q["cited"] = q["id"] in cited_q
    warnings = []
    if not sections and not queries:
        warnings.append("This answer is not based on the policy or the invoice data: no tool was used.")
    if unknown := sorted(cited_s - {s["id"] for s in sections}):
        warnings.append(f"The answer cites {', '.join(unknown)}, which was not retrieved from the policy. Check it.")
    if missing := sorted(cited_q - {q["id"] for q in queries}):
        warnings.append(f"The answer cites {', '.join(missing)}, but no such query was run.")
    if (sections or queries) and not (cited_s or cited_q):
        warnings.append("The answer has no citations, so it cannot be traced to the evidence below.")
    if any(q["error"] for q in queries):
        warnings.append("At least one query failed. See the queries below.")
    return warnings


def _caveats(run, queries):
    """Notes the analyst must see next to any number. Written by code, so they are always there."""
    if not queries:
        return []
    out = [f"EUR amounts use this FX source: {run['fx_source']}.",
           f"Based on {run['clean_rows']} clean invoices; {run['quarantined_rows']} quarantined rows are not included."]
    if any(q["truncated"] for q in queries):
        out.append(f"A query returned more than {config.MAX_SQL_ROWS} rows; only the first {config.MAX_SQL_ROWS} are shown.")
    return out


def _client():
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        raise AssistantError("llm_not_configured",
                             "No Anthropic API key. Put ANTHROPIC_API_KEY in a .env file and restart the backend.", 503)
    return anthropic.Anthropic()


def _call_model(client, messages):
    try:
        return client.messages.create(
            model=config.MODEL, max_tokens=config.MAX_TOKENS, output_config={"effort": config.EFFORT},
            system=SYSTEM_PROMPT.replace("{max_rows}", str(config.MAX_SQL_ROWS)), tools=TOOLS, messages=messages)
    except anthropic.AuthenticationError as e:
        raise AssistantError("llm_not_configured", "The Anthropic API key was rejected.", 503) from e
    except anthropic.RateLimitError as e:
        raise AssistantError("llm_rate_limited", "The model is rate limited. Try again in a minute.", 429) from e
    except anthropic.APIStatusError as e:
        raise AssistantError("llm_error", f"The model API returned an error ({e.status_code}).") from e
    except anthropic.APIConnectionError as e:
        raise AssistantError("llm_unreachable", "Could not reach the model API.") from e
