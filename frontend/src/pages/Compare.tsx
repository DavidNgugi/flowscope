import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getComparison } from '../api/client'
import ComparisonMatrix from '../components/ComparisonMatrix'
import { CompactAIUsage } from '../components/AIUsagePanel'

export default function Compare() {
  const [searchParams] = useSearchParams()
  const ids = (searchParams.get('ids') ?? '').split(',').filter(Boolean)

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['comparison', ids],
    queryFn: () => getComparison(ids),
    enabled: ids.length >= 2,
  })

  if (ids.length < 2) {
    return <div className="empty-state">Select at least two completed analyses from the home page to compare.</div>
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Comparison</h1>
        <button className="btn" onClick={() => refetch()} disabled={isFetching}>
          {isFetching ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {isLoading && <div className="skeleton" style={{ height: 200 }} />}

      {error && <div className="banner banner-error">{(error as Error).message}</div>}

      {data && (
        <>
          <CompactAIUsage usage={data.ai_usage} />
          <ComparisonMatrix result={data.result} />
        </>
      )}
    </div>
  )
}
