import { Link } from 'react-router-dom'
import type { VideoSummary } from '../types'
import StatusBadge from './StatusBadge'

function formatDuration(seconds: number | null): string {
  if (!seconds) return ''
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

export default function AnalysisCard({
  video,
  selected,
  onToggleSelect,
}: {
  video: VideoSummary
  selected: boolean
  onToggleSelect: (id: string) => void
}) {
  const inProgress = video.job_status && video.job_status !== 'done' && video.job_status !== 'error'
  const pct =
    video.progress_total && video.progress_total > 0
      ? Math.round(((video.progress_current ?? 0) / video.progress_total) * 100)
      : null

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10, position: 'relative' }}>
      {video.job_status === 'done' && (
        <label
          className="checkbox-row"
          style={{ position: 'absolute', top: 14, right: 14 }}
          onClick={(e) => e.stopPropagation()}
        >
          <input type="checkbox" checked={selected} onChange={() => onToggleSelect(video.id)} />
        </label>
      )}
      <Link to={`/videos/${video.id}`} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {video.thumbnail_url ? (
          <img
            src={video.thumbnail_url}
            alt=""
            style={{ width: '100%', aspectRatio: '16/9', objectFit: 'cover', borderRadius: 8, background: '#000' }}
          />
        ) : (
          <div className="skeleton" style={{ width: '100%', aspectRatio: '16/9' }} />
        )}
        <div style={{ fontSize: 14, fontWeight: 600, lineHeight: 1.35 }}>
          {video.title ?? 'Fetching title…'}
        </div>
        <div className="muted" style={{ fontSize: 12, display: 'flex', gap: 8 }}>
          {video.channel && <span>{video.channel}</span>}
          {video.duration_seconds && <span>· {formatDuration(video.duration_seconds)}</span>}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 4 }}>
          <StatusBadge status={video.job_status} />
          {video.job_error && <span style={{ fontSize: 11, color: 'var(--error)' }}>failed</span>}
        </div>
        {inProgress && (
          <div className="progress-track">
            <div className="progress-fill" style={{ width: `${pct ?? 8}%` }} />
          </div>
        )}
      </Link>
    </div>
  )
}
