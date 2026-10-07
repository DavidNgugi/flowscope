import { STAGE_LABELS, type JobStatus } from '../types'

export default function StatusBadge({ status }: { status: JobStatus | null | undefined }) {
  if (!status) return null
  const cls = status === 'done' ? 'badge-done' : status === 'error' ? 'badge-error' : status === 'queued' ? 'badge-queued' : 'badge-progress'
  return (
    <span className={`badge ${cls}`}>
      <span className="badge-dot" />
      {STAGE_LABELS[status] ?? status}
    </span>
  )
}
