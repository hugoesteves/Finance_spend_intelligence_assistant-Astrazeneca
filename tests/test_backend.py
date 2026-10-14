"""Backend tests where they matter: the SQL guard, grounding checks and the API contract.
The model is replaced by a scripted fake, so the tests need no API key and no network."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import assistant, config, main, tools
from pipeline.run import run_pipeline

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module", autouse=True)
def database(tmp_path_factory):
    out = tmp_path_factory.mktemp("out")
    run_pipeline(ROOT / "data/invoices_2025.csv", ROOT / "data/vendor_master.csv", out / "spend.db", out)
    old, config.DB_PATH = config.DB_PATH, out / "spend.db"
    yield
    config.DB_PATH = old


@pytest.fixture(scope="module")
def index():
    return tools.PolicyIndex(config.POLICY_PATH)


# ---- Scripted model --------------------------------------------------------------------
def tool_call(id, name, **input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def reply(*blocks, stop="tool_use"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop, model="fake-model",
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5))


def text(value):
    return reply(SimpleNamespace(type="text", text=value), stop="end_turn")


class FakeClient:
    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.replies.pop(0)


# ---- Tests ------------------------------------------------------------------------------
@pytest.mark.parametrize("sql", ["DELETE FROM invoices", "DROP TABLE invoices", "SELECT 1; DROP TABLE invoices",
                                 "ATTACH DATABASE 'x.db' AS x", "PRAGMA table_info(invoices)"])
def test_sql_guard_blocks_anything_but_reading(sql):
    with pytest.raises(tools.QueryError):
        tools.run_select(sql)
    assert tools.fetch_all("SELECT COUNT(*) n FROM invoices")[0]["n"] == 140


def test_search_finds_the_approval_table_for_an_approval_question(index):
    assert "§3" in [c["id"] for c, _ in index.search("who can approve an invoice of 60000", 3)]


def test_answer_carries_the_real_tool_results_and_citations_are_checked(index):
    client = FakeClient(
        reply(tool_call("t1", "search_policy", query="vendor status ACTIVE payments"),
              tool_call("t2", "query_invoices", purpose="Non-active spend",
                        sql="SELECT ROUND(SUM(amount_eur), 2) FROM invoices WHERE vendor_status <> 'ACTIVE'")),
        text("Spend is EUR 1,497,797.48 [Q1] and must be escalated [§4.1]. I did not read §3.1."))
    result = assistant.answer_question("Spend with non-active vendors?", index, client)
    assert result["status"] == "answered" and result["warnings"] == []     # "§3.1" in prose is not a citation
    assert result["queries"][0]["rows"] == [[1497797.48]]
    assert [s["id"] for s in result["policy_sections"] if s["cited"]] == ["§4.1"]


def test_ungrounded_answers_get_warnings(index):
    no_tools = assistant.answer_question("Who approves?", index, FakeClient(text("The Finance Director.")))
    assert any("no tool was used" in w for w in no_tools["warnings"])
    client = FakeClient(reply(tool_call("t1", "search_policy", query="credit notes")),
                        text("Payment terms are 60 days [§5.2]."))
    wrong_cite = assistant.answer_question("Payment terms?", index, client)
    assert any("§5.2" in w for w in wrong_cite["warnings"])


def test_api_contract_and_one_error_shape(monkeypatch):
    api = TestClient(main.app)
    monkeypatch.setattr(assistant, "_client", lambda: FakeClient(
        reply(tool_call("t1", "query_invoices", purpose="count", sql="SELECT COUNT(*) FROM invoices")),
        text("140 invoices [Q1].")))
    r = api.post("/api/ask", json={"question": "How many invoices?"})
    assert r.status_code == 200 and r.json()["queries"][0]["rows"] == [[140]]
    assert api.get("/api/invoices/flagged").json()["total"] == 68
    assert api.get("/api/summary").json()["reconciliation"]["balanced"] is True

    r = api.post("/api/ask", json={"question": ""})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"

    def no_key():
        raise assistant.AssistantError("llm_not_configured", "No key.", 503)
    monkeypatch.setattr(assistant, "_client", no_key)
    r = api.post("/api/ask", json={"question": "How many invoices?"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "llm_not_configured"


def test_eval_numbers_match_as_whole_numbers():
    from evals.run_eval import found, norm
    assert found("5", norm("The pipeline quarantined 5 rows."))
    assert not found("5", norm("All 145 rows were read."))
    assert found("1497797.48", norm("Spend is **EUR 1,497,797.48** [Q1]."))
