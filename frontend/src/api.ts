// The only file that calls the backend. Types mirror backend/schemas.py.
// Every failure becomes an Error with a message the UI can show.

export interface PolicySection { id: string; title: string; text: string; score: number; query: string; cited: boolean }
export interface QueryEvidence {
  id: string; purpose: string; sql: string; columns: string[]; rows: unknown[][]
  row_count: number; truncated: boolean; error: string | null; cited: boolean
}
export interface AskResponse {
  question: string; answer: string; status: 'answered' | 'refused' | 'incomplete'
  policy_sections: PolicySection[]; queries: QueryEvidence[]; warnings: string[]; caveats: string[]
  meta: { model: string; pipeline_run_id: string; latency_ms: number }
}
export interface Summary {
  reconciliation: { raw_rows: number; clean_rows: number; quarantined_rows: number; balanced: boolean }
  data_quality: { issue_code: string; action: string; rows_affected: number; description: string; findings: string }[]
  compliance_flags: { rule_code: string; action_level: string; policy_ref: string; invoices: number; exposure_eur: number }[]
}
export interface FlaggedInvoice {
  source_row: number; invoice_number: string; invoice_date: string; vendor_name: string; vendor_status: string
  amount_eur: number; payment_action: string; flags: { rule_code: string }[]
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch {
    throw new Error('Cannot reach the backend. Is it running on port 8000?')
  }
  const body = await response.json().catch(() => null)
  if (response.ok) return body as T
  // The backend sends errors as {error: {code, message}}; anything else gets a generic message.
  throw new Error(body?.error?.message ?? `Request failed (HTTP ${response.status}). Is the backend running?`)
}

export const api = {
  ask: (question: string) =>
    request<AskResponse>('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    }),
  summary: () => request<Summary>('/api/summary'),
  flagged: () => request<{ total: number; invoices: FlaggedInvoice[] }>('/api/invoices/flagged'),
}
