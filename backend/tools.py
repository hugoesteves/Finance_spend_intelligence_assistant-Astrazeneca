"""The two evidence sources the assistant can use: policy search and read-only SQL.

Policy search: one chunk per numbered clause (§2.1, §3.1, ...), because a clause is what an analyst cites.
A found clause brings the text at the top of its section (§3.1 brings the §3 approval table).
Ranking is BM25, a classic keyword score. No embeddings: the policy is one page of exact terms
("PO", "ON_HOLD", "credit note"). Weak point: synonyms; the model can search again with other words.

SQL: the model writes the SELECT, code limits what it can do:
  1. the file is opened read-only,  2. SQLite's authorizer allows reading only,
  3. a 5-second time limit,          4. at most 100 rows come back.
"""
import math
import re
import sqlite3
import time

from . import config

# ---- Policy search -----------------------------------------------------------------------
STOPWORDS = set("a an and are as at be by for from in is it may must of on or the to with what who which "
                "does do we our about say says should".split())


def tokenize(text):
    words = [w for w in re.findall(r"[a-z0-9_]+", text.lower()) if w not in STOPWORDS]
    # Cut one common ending, so "approver", "approval" and "approved" match each other.
    return [next((w[:-len(s)] for s in ("ing", "als", "ers", "al", "er", "ed", "es", "s", "e")
                  if w.endswith(s) and len(w) - len(s) >= 4), w) for w in words]


def load_chunks(path):
    """Returns [{id, title, text}]: one per clause, plus one per section intro (e.g. the §3 table)."""
    chunks, section, title, intro = [], None, "", []
    for line in open(path, encoding="utf-8").read().splitlines() + ["## 99. end"]:
        if m := re.match(r"^## (\d+)\. (.+)$", line):
            if section and intro:
                chunks.append(dict(id=f"§{section}", title=title, text="\n".join(intro)))
            section, title, intro = m[1], m[2], []
        elif m := re.match(r"^(\d+\.\d+) (.+)$", line):
            chunks.append(dict(id=f"§{m[1]}", title=title, text=m[2]))
        elif section and line.strip():
            intro.append(line)
        elif line.strip() and not line.startswith("# "):
            chunks.append(dict(id="§0", title="Document header", text=line))
    return sorted(chunks, key=lambda c: [int(n) for n in c["id"][1:].split(".")])


class PolicyIndex:
    K1, B = 1.5, 0.75      # standard BM25 parameters

    def __init__(self, path):
        self.chunks = load_chunks(path)
        self.docs = [tokenize(f"{c['title']} {c['text']}") for c in self.chunks]
        self.avg_len = sum(map(len, self.docs)) / len(self.docs)
        self.df = {}
        for doc in self.docs:
            for term in set(doc):
                self.df[term] = self.df.get(term, 0) + 1

    def search(self, query, k):
        """Best k chunks as (chunk, score), plus the section text above each found clause."""
        n, hits = len(self.docs), []
        for chunk, doc in zip(self.chunks, self.docs):
            score = 0.0
            for term in set(tokenize(query)):
                if tf := doc.count(term):
                    idf = math.log(1 + (n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                    score += idf * tf * (self.K1 + 1) / (tf + self.K1 * (1 - self.B + self.B * len(doc) / self.avg_len))
            if score > 0:
                hits.append((chunk, round(score, 3)))
        hits = sorted(hits, key=lambda h: -h[1])[:k]
        by_id, found = {c["id"]: c for c in self.chunks}, {c["id"] for c, _ in hits}
        for chunk, score in list(hits):
            parent = by_id.get(chunk["id"].split(".")[0])          # "§3.1" -> "§3"
            if parent and parent["id"] not in found:
                hits.append((parent, score))
                found.add(parent["id"])
        return hits


# ---- Read-only SQL -----------------------------------------------------------------------
class QueryError(Exception):
    """The SQL was rejected or failed. The message is safe to show to the model and the user."""


ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}


def connect():
    if not config.DB_PATH.exists():
        raise FileNotFoundError(f"{config.DB_PATH} not found")
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)       # guard 1: read-only file
    con.row_factory = sqlite3.Row
    return con


def fetch_all(sql):
    """Run one of our own (trusted) queries."""
    con = connect()
    try:
        return [dict(r) for r in con.execute(sql)]
    finally:
        con.close()


def run_select(sql):
    """Run a query written by the model. Returns columns, rows, row_count, truncated."""
    con = connect()
    con.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in ALLOWED else sqlite3.SQLITE_DENY)  # guard 2
    deadline = time.monotonic() + config.SQL_TIMEOUT_SECONDS
    con.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)                              # guard 3
    try:
        cur = con.execute(sql.strip().rstrip(";"))              # execute() refuses more than one statement
        rows = cur.fetchmany(config.MAX_SQL_ROWS + 1)                                                         # guard 4
        columns = [c[0] for c in cur.description]
    except (sqlite3.Error, sqlite3.Warning) as e:
        raise QueryError(f"SQL error: {e}") from e
    finally:
        con.close()
    return dict(columns=columns, rows=[list(r) for r in rows[:config.MAX_SQL_ROWS]],
                row_count=min(len(rows), config.MAX_SQL_ROWS), truncated=len(rows) > config.MAX_SQL_ROWS)
