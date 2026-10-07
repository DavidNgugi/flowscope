import type { AIUsageRun, AIUsageStage, AIUsageSummary } from '../types'

const STAGE_LABELS: Record<string, string> = {
  frame_analysis: 'Screen analysis',
  synthesis: 'Report synthesis',
  comparison: 'Cross-video comparison',
}

function formatTokens(value: number): string {
  return new Intl.NumberFormat().format(value)
}

function formatCost(value: number | null): string {
  if (value === null) return 'Rate unavailable'
  if (value < 0.01) return `$${value.toFixed(5)}`
  return `$${value.toFixed(3)}`
}

function UsageTotals({ usage }: { usage: AIUsageSummary }) {
  return (
    <div className="usage-summary-grid">
      <div className="usage-metric">
        <span>Estimated cost</span>
        <strong>{formatCost(usage.estimated_cost_usd)}</strong>
      </div>
      <div className="usage-metric">
        <span>API calls</span>
        <strong>{formatTokens(usage.call_count)}</strong>
      </div>
      <div className="usage-metric">
        <span>Input tokens</span>
        <strong>{formatTokens(usage.input_tokens)}</strong>
      </div>
      <div className="usage-metric">
        <span>Output tokens</span>
        <strong>{formatTokens(usage.output_tokens)}</strong>
      </div>
    </div>
  )
}

function StageTable({ stages }: { stages: AIUsageStage[] }) {
  return (
    <div className="usage-table-wrap">
      <table className="usage-table">
        <thead>
          <tr>
            <th>AI-powered step</th>
            <th>Calls</th>
            <th>Input</th>
            <th>Cache write</th>
            <th>Cache read</th>
            <th>Output</th>
            <th>Estimated cost</th>
          </tr>
        </thead>
        <tbody>
          {stages.map((stage) => (
            <tr key={stage.stage}>
              <td>{STAGE_LABELS[stage.stage] ?? stage.stage.replaceAll('_', ' ')}</td>
              <td>{formatTokens(stage.call_count)}</td>
              <td>{formatTokens(stage.input_tokens)}</td>
              <td>{formatTokens(stage.cache_creation_input_tokens)}</td>
              <td>{formatTokens(stage.cache_read_input_tokens)}</td>
              <td>{formatTokens(stage.output_tokens)}</td>
              <td>{formatCost(stage.estimated_cost_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function CompactAIUsage({ usage }: { usage: AIUsageSummary }) {
  if (usage.call_count === 0) return null
  return (
    <div className="usage-compact card">
      <div>
        <div className="muted" style={{ fontSize: 11, fontWeight: 700 }}>AI API USAGE</div>
        <div style={{ fontSize: 13 }}>
          {formatTokens(usage.call_count)} calls · {formatTokens(usage.input_tokens + usage.output_tokens)} tokens
        </div>
      </div>
      <strong>{formatCost(usage.estimated_cost_usd)}</strong>
    </div>
  )
}

export default function AIUsagePanel({ runs }: { runs: AIUsageRun[] }) {
  if (runs.length === 0) {
    return <div className="empty-state">No analysis runs found.</div>
  }

  return (
    <div className="usage-runs">
      {runs.map((run, index) => (
        <section className="card usage-run" key={run.job_id}>
          <div className="usage-run-header">
            <div>
              <div style={{ fontWeight: 700 }}>{index === 0 ? 'Latest analysis' : 'Previous analysis'}</div>
              <div className="faint" style={{ fontSize: 12 }}>
                {new Date(run.created_at).toLocaleString()} · {run.models.join(', ') || 'No AI calls recorded'}
              </div>
            </div>
            <span className="badge badge-queued">{run.status}</span>
          </div>

          {run.call_count > 0 ? (
            <>
              <UsageTotals usage={run} />
              <StageTable stages={run.by_stage} />
              <div className="faint" style={{ fontSize: 11 }}>
                Costs are estimates based on the model’s stored per-token rate at request time.
              </div>
            </>
          ) : (
            <div className="muted" style={{ fontSize: 13 }}>
              No usage was recorded for this run. It may predate usage tracking or contain no completed AI calls.
            </div>
          )}
        </section>
      ))}
    </div>
  )
}
