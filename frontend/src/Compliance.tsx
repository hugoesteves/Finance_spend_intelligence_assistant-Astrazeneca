// Part 1 results: row reconciliation, data quality issues, compliance flags and the flagged invoices.
import { useEffect, useState } from 'react'
import { api, type FlaggedInvoice, type Summary } from './api'

const eur = new Intl.NumberFormat('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export function Compliance() {
  const [data, setData] = useState<{ summary: Summary; invoices: FlaggedInvoice[] } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.summary(), api.flagged()])
      .then(([summary, flagged]) => setData({ summary, invoices: flagged.invoices }))
      .catch((e: Error) => setError(e.message))
  }, [])

  if (error) return <div className="banner error" role="alert">Could not load the data. {error}</div>
  if (!data) return <div className="muted">Loading…</div>
  const { reconciliation: rec, data_quality, compliance_flags } = data.summary

  return (
    <>
      <p className={`banner ${rec.balanced ? 'ok' : 'error'}`}>
        {rec.raw_rows} raw rows = {rec.clean_rows} clean + {rec.quarantined_rows} quarantined.{' '}
        {rec.balanced ? 'No row was lost.' : 'NOT balanced: do not use the data.'}
      </p>

      <h3>Data quality issues and handling</h3>
      <table>
        <thead><tr><th>Issue</th><th>Handling</th><th className="num">Rows</th><th>What it means</th><th>Findings</th></tr></thead>
        <tbody>
          {data_quality.map((d) => (
            <tr key={d.issue_code}><td>{d.issue_code}</td><td>{d.action}</td><td className="num">{d.rows_affected}</td><td>{d.description}</td><td>{d.findings}</td></tr>
          ))}
        </tbody>
      </table>

      <h3>Compliance flags by rule</h3>
      <table>
        <thead><tr><th>Rule</th><th>Action</th><th>Policy</th><th className="num">Invoices</th><th className="num">Exposure (EUR)</th></tr></thead>
        <tbody>
          {compliance_flags.map((f) => (
            <tr key={f.rule_code + f.action_level}>
              <td>{f.rule_code}</td><td><span className={`chip ${f.action_level}`}>{f.action_level}</span></td>
              <td>{f.policy_ref}</td><td className="num">{f.invoices}</td><td className="num">{eur.format(f.exposure_eur)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted">HOLD = do not pay · ESCALATE = send to the owner in the policy · REVIEW = human check. One invoice can have several flags.</p>

      <h3>Flagged invoices ({data.invoices.length})</h3>
      <div className="table-scroll">
        <table>
          <thead><tr><th>Invoice</th><th>Date</th><th>Vendor</th><th className="num">Amount (EUR)</th><th>Status</th><th>Flags</th></tr></thead>
          <tbody>
            {data.invoices.map((inv) => (
              <tr key={inv.source_row}>
                <td>{inv.invoice_number}</td><td>{inv.invoice_date}</td>
                <td>{inv.vendor_name} <span className="muted">({inv.vendor_status})</span></td>
                <td className="num">{eur.format(inv.amount_eur)}</td>
                <td><span className={`chip ${inv.payment_action}`}>{inv.payment_action}</span></td>
                <td>{inv.flags.map((f) => f.rule_code).join(', ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
