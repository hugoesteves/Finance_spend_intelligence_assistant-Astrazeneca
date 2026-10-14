// Chat: one question at a time. Each answer shows its evidence: the policy clauses and the SQL that
// were really used (recorded by the backend), plus warnings from the citation check.
import { useState, type FormEvent } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, type AskResponse, type QueryEvidence } from './api'

export type Turn =
  | { question: string; state: 'pending' }
  | { question: string; state: 'done'; response: AskResponse }
  | { question: string; state: 'error'; error: string }

const EXAMPLES = [
  'Which invoices over EUR 1,000 are missing a PO, and what does the policy say we should do about them?',
  "What's our total 2025 spend with vendors that are not ACTIVE?",
  'Who is allowed to approve an invoice of EUR 60,000?',
]

export function Chat({ turns, setTurns }: { turns: Turn[]; setTurns: (update: (t: Turn[]) => Turn[]) => void }) {
  const [draft, setDraft] = useState('')
  const busy = turns.some((t) => t.state === 'pending')

  async function ask(question: string) {
    const i = turns.length
    const settle = (turn: Turn) => setTurns((ts) => ts.map((t, j) => (j === i ? turn : t)))
    setTurns((ts) => [...ts, { question, state: 'pending' }])
    try {
      settle({ question, state: 'done', response: await api.ask(question) })
    } catch (e) {
      settle({ question, state: 'error', error: (e as Error).message })
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    if (draft.trim().length < 3 || busy) return
    void ask(draft.trim())
    setDraft('')
  }

  return (
    <div className="chat">
      {turns.length === 0 && (
        <div className="empty">
          <p>Ask about the 2025 invoices or the procurement policy. Each question is answered on its own.</p>
          {EXAMPLES.map((q) => (
            <button key={q} className="example" onClick={() => void ask(q)} disabled={busy}>
              {q}
            </button>
          ))}
        </div>
      )}

      {turns.map((t, i) => (
        <article key={i} className="turn">
          <div className="question">{t.question}</div>
          {t.state === 'pending' && <div className="answer pending">Searching the policy and the invoice data…</div>}
          {t.state === 'error' && (
            <div className="answer error" role="alert">
              <strong>No answer.</strong> {t.error}
            </div>
          )}
          {t.state === 'done' && <Answer r={t.response} />}
        </article>
      ))}

      <form className="composer" onSubmit={submit}>
        <input value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={1000}
          placeholder="Ask a question about invoices or the policy" aria-label="Question" autoFocus />
        <button type="submit" disabled={busy || draft.trim().length < 3}>Ask</button>
      </form>
    </div>
  )
}

function Answer({ r }: { r: AskResponse }) {
  return (
    <div className={`answer ${r.warnings.length || r.status !== 'answered' ? 'warn' : ''}`}>
      {r.status !== 'answered' && <div className="banner warning">{r.status === 'refused' ? 'Refused' : 'Incomplete answer'}</div>}
      {r.warnings.map((w) => <div key={w} className="banner warning">⚠ {w}</div>)}
      <div className="markdown"><Markdown remarkPlugins={[remarkGfm]}>{r.answer}</Markdown></div>

      {(r.policy_sections.length > 0 || r.queries.length > 0) && (
        <details className="evidence">
          <summary>Evidence: {r.policy_sections.length} policy clauses, {r.queries.length} queries</summary>
          {r.policy_sections.map((s) => (
            <div key={s.id} className={`item ${s.cited ? 'cited' : ''}`}>
              <strong>{s.id}</strong> {s.title} <span className="tag">{s.cited ? 'cited' : 'retrieved, not cited'}</span>
              <div className="markdown"><Markdown remarkPlugins={[remarkGfm]}>{s.text}</Markdown></div>
            </div>
          ))}
          {r.queries.map((q) => <Query key={q.id} q={q} />)}
          {r.caveats.length > 0 && <ul className="muted">{r.caveats.map((c) => <li key={c}>{c}</li>)}</ul>}
          <div className="muted">Pipeline run {r.meta.pipeline_run_id} · {r.meta.model} · {(r.meta.latency_ms / 1000).toFixed(1)} s</div>
        </details>
      )}
    </div>
  )
}

function Query({ q }: { q: QueryEvidence }) {
  return (
    <div className={`item ${q.error ? 'failed' : q.cited ? 'cited' : ''}`}>
      <strong>{q.id}</strong> {q.purpose} <span className="tag">{q.error ? 'failed' : q.cited ? 'cited' : 'run, not cited'}</span>
      <pre>{q.sql}</pre>
      {q.error ? <div className="error-text">{q.error}</div> : (
        <div className="table-scroll">
          <table>
            <thead><tr>{q.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
            <tbody>
              {q.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{cell === null ? 'NULL' : String(cell)}</td>)}</tr>)}
            </tbody>
          </table>
          <div className="muted">{q.row_count} rows{q.truncated && ' (more exist; only these were returned)'}</div>
        </div>
      )}
    </div>
  )
}
