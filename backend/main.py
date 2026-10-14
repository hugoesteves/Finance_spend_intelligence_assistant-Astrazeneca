"""FastAPI app.  Run:  uvicorn backend.main:app --reload

  POST /api/ask               answer a question, with the evidence behind it
  GET  /api/summary           data quality and compliance summary from Part 1
  GET  /api/invoices/flagged  invoices with at least one compliance flag

Every error has one shape: {"error": {"code": "...", "message": "..."}}.
"""
import logging
from functools import lru_cache

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from pipeline.report import summarise

from . import assistant, config, tools
from .schemas import AskRequest, AskResponse, FlaggedInvoicesResponse

app = FastAPI(title="Finance spend intelligence assistant")


@lru_cache
def policy_index():
    return tools.PolicyIndex(config.POLICY_PATH)


def error(status, code, message):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@app.exception_handler(assistant.AssistantError)
def on_assistant_error(_: Request, e: assistant.AssistantError):
    return error(e.status, e.code, e.message)


@app.exception_handler(FileNotFoundError)
def on_missing_data(_: Request, e: FileNotFoundError):
    return error(503, "data_not_ready", f"{e}. Run the pipeline first: python -m pipeline.run")


@app.exception_handler(RequestValidationError)
def on_invalid_request(_: Request, e: RequestValidationError):
    first = e.errors()[0]
    return error(422, "invalid_request", f"{'.'.join(str(p) for p in first['loc'][1:])}: {first['msg']}")


@app.exception_handler(Exception)
def on_unexpected(request: Request, e: Exception):
    logging.getLogger("uvicorn.error").exception("Unhandled error on %s", request.url.path, exc_info=e)
    return error(500, "internal_error", "Unexpected server error. See the server log.")


@app.post("/api/ask", response_model=AskResponse)
def ask(body: AskRequest):
    return assistant.answer_question(body.question.strip(), policy_index())


@app.get("/api/summary")
def summary():
    """Computed from the database on every call, so it always matches the data."""
    con = tools.connect()
    try:
        return summarise(con)
    finally:
        con.close()


@app.get("/api/invoices/flagged", response_model=FlaggedInvoicesResponse)
def flagged():
    """Flagged invoices, strongest action first, each with its flags."""
    invoices = tools.fetch_all("""
        SELECT source_row, invoice_number, invoice_date, vendor_name, vendor_status, amount_eur,
               po_number, approved_by, payment_action
        FROM invoices WHERE flag_count > 0
        ORDER BY CASE payment_action WHEN 'HOLD' THEN 0 WHEN 'ESCALATE' THEN 1 ELSE 2 END, ABS(amount_eur) DESC""")
    flags = {}
    for f in tools.fetch_all("SELECT source_row, rule_code, action_level, policy_ref, detail FROM invoice_flags"):
        flags.setdefault(f.pop("source_row"), []).append(f)
    for inv in invoices:
        inv["flags"] = flags[inv["source_row"]]
    return {"total": len(invoices), "invoices": invoices}
