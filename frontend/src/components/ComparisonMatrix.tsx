import type { ComparisonResult } from '../types'

export default function ComparisonMatrix({ result }: { result: ComparisonResult }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <div>
        <h3 style={{ fontSize: 15, marginBottom: 10 }}>Common patterns</h3>
        <ul className="insight-list">
          {result.common_patterns.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      </div>

      <div>
        <h3 style={{ fontSize: 15, marginBottom: 10 }}>Divergences</h3>
        <ul className="insight-list">
          {result.divergences.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      </div>

      {result.stage_matrix.length > 0 && (
        <div>
          <h3 style={{ fontSize: 15, marginBottom: 10 }}>Stage-by-stage matrix</h3>
          <div className="card" style={{ overflowX: 'auto' }}>
            <pre style={{ margin: 0, fontSize: 12.5, whiteSpace: 'pre-wrap', fontFamily: 'ui-monospace, monospace' }}>
              {JSON.stringify(result.stage_matrix, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}
