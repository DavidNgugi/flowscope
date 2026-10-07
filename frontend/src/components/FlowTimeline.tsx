import type { Frame, FlowStep } from '../types'

export default function FlowTimeline({ steps, frames }: { steps: FlowStep[]; frames: Frame[] }) {
  if (steps.length === 0) {
    return <div className="empty-state">Flow will appear once synthesis completes.</div>
  }

  const frameById = new Map(frames.map((f) => [f.id, f]))

  return (
    <div className="flow-timeline">
      {steps
        .slice()
        .sort((a, b) => a.step_index - b.step_index)
        .map((step) => {
          const frame = frameById.get(step.frame_id)
          const narration = frame?.transcript_excerpt || frame?.analysis?.transcript_excerpt
          return (
            <div className="flow-step" key={`${step.step_index}-${step.frame_id}`}>
              <div className="flow-step-marker">{step.step_index}</div>
              {frame && <img className="flow-step-thumb" src={frame.url} alt="" />}
              <div>
                <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 3 }}>{step.screen_name}</div>
                <div className="muted" style={{ fontSize: 13, lineHeight: 1.5 }}>{step.description}</div>
                {narration && (
                  <div className="flow-step-narration">
                    <span>At this moment:</span> “{narration}”
                  </div>
                )}
              </div>
            </div>
          )
        })}
    </div>
  )
}
