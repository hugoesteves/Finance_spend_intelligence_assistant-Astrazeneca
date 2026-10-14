"""The API contract. frontend/src/api.ts mirrors these shapes."""
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from . import config


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=config.MAX_QUESTION_CHARS)


class PolicySection(BaseModel):
    id: str                 # "§2.2"
    title: str
    text: str
    score: float            # BM25 score
    query: str              # the search words the model used
    cited: bool             # does the answer cite it?


class QueryEvidence(BaseModel):
    id: str                 # "Q1"
    purpose: str
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    error: Optional[str]
    cited: bool


class AskResponse(BaseModel):
    question: str
    answer: str
    status: Literal["answered", "refused", "incomplete"]
    policy_sections: list[PolicySection]
    queries: list[QueryEvidence]
    warnings: list[str]     # grounding problems found by code
    caveats: list[str]      # assumptions behind the numbers
    meta: dict[str, Any]    # model, pipeline run, latency, tokens


class FlaggedInvoice(BaseModel):
    source_row: int
    invoice_number: str
    invoice_date: str
    vendor_name: str
    vendor_status: str
    amount_eur: float
    po_number: Optional[str]
    approved_by: Optional[str]
    payment_action: str
    flags: list[dict[str, str]]     # rule_code, action_level, policy_ref, detail


class FlaggedInvoicesResponse(BaseModel):
    total: int
    invoices: list[FlaggedInvoice]
