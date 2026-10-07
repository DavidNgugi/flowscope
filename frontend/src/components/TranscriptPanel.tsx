import type { TranscriptSegment } from '../types'

function formatTs(ms: number): string {
  const totalSec = Math.floor(ms / 1000)
  const m = Math.floor(totalSec / 60)
  const s = totalSec % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

export default function TranscriptPanel({ segments, source }: { segments: TranscriptSegment[]; source: string | null }) {
  if (segments.length === 0) {
    return <div className="empty-state">No transcript available.</div>
  }

  return (
    <div>
      {source && (
        <div className="faint" style={{ fontSize: 12, marginBottom: 12 }}>
          Source: {source.replace('_', ' ')}
        </div>
      )}
      <div className="transcript-list">
        {segments.map((seg) => (
          <div className="transcript-row" key={seg.id}>
            <span className="transcript-ts">{formatTs(seg.start_ms)}</span>
            <span>{seg.text}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
