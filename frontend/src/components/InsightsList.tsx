export default function InsightsList({ insights }: { insights: string[] }) {
  if (insights.length === 0) {
    return <div className="empty-state">Insights will appear once synthesis completes.</div>
  }
  return (
    <ul className="insight-list">
      {insights.map((insight, i) => (
        <li key={i}>{insight}</li>
      ))}
    </ul>
  )
}
