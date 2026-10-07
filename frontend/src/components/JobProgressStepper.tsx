import { useEffect } from 'react'
import { useJobSocket } from '../hooks/useJobSocket'
import { STAGE_LABELS, STAGE_ORDER, type JobStatus } from '../types'

export default function JobProgressStepper({
  jobId,
  onDone,
}: {
  jobId: string
  onDone?: () => void
}) {
  const state = useJobSocket(jobId)
  const status = state.status ?? 'queued'
  const currentIdx = STAGE_ORDER.indexOf(status)

  useEffect(() => {
    if (state.status === 'done') onDone?.()
  }, [state.status, onDone])

  const pct = state.progress.total > 0 ? Math.round((state.progress.current / state.progress.total) * 100) : null

  return (
    <div className="card">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
        <div style={{ fontWeight: 700, fontSize: 15 }}>Analyzing video</div>
        <span className={`badge ${state.connected ? 'badge-progress' : 'badge-queued'}`}>
          <span className="badge-dot" />
          {state.connected ? 'live' : 'reconnecting…'}
        </span>
      </div>

      <div className="muted" style={{ fontSize: 13, marginBottom: 14 }}>
        {state.message ?? STAGE_LABELS[status]}
        {pct !== null && ` — ${state.progress.current}/${state.progress.total} (${pct}%)`}
      </div>

      {pct !== null && (
        <div className="progress-track" style={{ marginBottom: 18 }}>
          <div className="progress-fill" style={{ width: `${pct}%` }} />
        </div>
      )}

      {state.errorMessage && <div className="banner banner-error">{state.errorMessage}</div>}

      <div className="stage-list">
        {STAGE_ORDER.map((stage, idx) => {
          const cls = idx < currentIdx ? 'past' : idx === currentIdx ? 'active' : ''
          return (
            <div key={stage} className={`stage-item ${cls}`}>
              <span className="stage-dot" />
              {STAGE_LABELS[stage as JobStatus]}
            </div>
          )
        })}
      </div>
    </div>
  )
}
