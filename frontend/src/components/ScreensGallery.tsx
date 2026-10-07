import { useState } from 'react'
import type { Frame, GalleryEntry } from '../types'

function stageFor(frameId: string, gallery: GalleryEntry[]): string | null {
  return gallery.find((g) => g.frame_id === frameId)?.flow_stage ?? null
}

function compactExcerpt(text: string, limit = 150): string {
  if (text.length <= limit) return text
  return `${text.slice(0, limit).replace(/\s+\S*$/, '')}…`
}

export default function ScreensGallery({ frames, gallery }: { frames: Frame[]; gallery: GalleryEntry[] }) {
  const [active, setActive] = useState<Frame | null>(null)

  if (frames.length === 0) {
    return <div className="empty-state">No screens extracted yet.</div>
  }

  return (
    <>
      <div className="gallery-grid">
        {frames.map((f) => {
          const stage = stageFor(f.id, gallery)
          const narration = f.transcript_excerpt || f.analysis?.transcript_excerpt
          return (
            <button key={f.id} className="gallery-item" onClick={() => setActive(f)}>
              <img src={f.url} alt={f.analysis?.screen_name ?? ''} loading="lazy" />
              <div className="gallery-item-body">
                <div className="gallery-item-title">{f.analysis?.screen_name ?? 'Analyzing…'}</div>
                <div className="faint" style={{ fontSize: 11 }}>{(f.timestamp_ms / 1000).toFixed(0)}s</div>
                {stage && <span className="gallery-item-stage">{stage}</span>}
                {narration && (
                  <div className="gallery-item-narration">
                    <span>What they’re saying</span>
                    “{compactExcerpt(narration)}”
                  </div>
                )}
              </div>
            </button>
          )
        })}
      </div>

      {active && (
        <div className="modal-backdrop" onClick={() => setActive(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <img src={active.url} alt="" />
            <div className="modal-body">
              <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 4 }}>
                {active.analysis?.screen_name ?? 'Not yet analyzed'}
              </div>
              <div className="faint" style={{ fontSize: 12, marginBottom: 14 }}>
                {(active.timestamp_ms / 1000).toFixed(1)}s · {active.analysis?.flow_step_label}
              </div>
              {active.analysis?.purpose && (
                <div style={{ marginBottom: 12 }}>
                  <div className="muted" style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>PURPOSE</div>
                  <div style={{ fontSize: 14, lineHeight: 1.5 }}>{active.analysis.purpose}</div>
                </div>
              )}
              {(active.transcript_excerpt || active.analysis?.transcript_excerpt) && (
                <div className="narration-block" style={{ marginBottom: 12 }}>
                  <div className="narration-label">WHAT THEY’RE SAYING</div>
                  <div>“{active.transcript_excerpt || active.analysis?.transcript_excerpt}”</div>
                </div>
              )}
              {active.analysis?.ux_notes && (
                <div style={{ marginBottom: 12 }}>
                  <div className="muted" style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>UX NOTES</div>
                  <div style={{ fontSize: 14, lineHeight: 1.5 }}>{active.analysis.ux_notes}</div>
                </div>
              )}
              {active.analysis?.ui_elements && active.analysis.ui_elements.length > 0 && (
                <div>
                  <div className="muted" style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>UI ELEMENTS</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {active.analysis.ui_elements.map((el, i) => (
                      <span
                        key={i}
                        title={el.notes ?? ''}
                        style={{
                          fontSize: 12,
                          padding: '4px 9px',
                          borderRadius: 6,
                          background: 'var(--bg-elevated)',
                          border: '1px solid var(--border)',
                        }}
                      >
                        {el.type}
                        {el.label ? `: ${el.label}` : ''}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              <button className="btn" style={{ marginTop: 18 }} onClick={() => setActive(null)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
