"""Settings for the API. Paths can be overridden with environment variables (used by the tests)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")          # ANTHROPIC_API_KEY lives here (the file is git-ignored)

DB_PATH = Path(os.getenv("SPEND_DB", ROOT / "output" / "spend.db"))
POLICY_PATH = Path(os.getenv("POLICY_FILE", ROOT / "data" / "procurement_policy.md"))

# ---- LLM -----------------------------------------------------------------------------
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("ANTHROPIC_EFFORT", "medium")
MAX_TOKENS = 16000
MAX_TOOL_ROUNDS = 6        # stop a question that keeps calling tools without answering

# ---- Tools ---------------------------------------------------------------------------
POLICY_TOP_K = 4           # policy chunks returned per search
MAX_SQL_ROWS = 100         # rows returned per query (to the model and to the UI)
SQL_TIMEOUT_SECONDS = 5
MAX_QUESTION_CHARS = 1000
