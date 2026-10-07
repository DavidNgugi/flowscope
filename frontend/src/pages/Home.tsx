import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { listVideos } from '../api/client'
import UrlSubmitForm from '../components/UrlSubmitForm'
import AnalysisCard from '../components/AnalysisCard'

export default function Home() {
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const navigate = useNavigate()

  const { data, isLoading } = useQuery({
    queryKey: ['videos'],
    queryFn: listVideos,
    refetchInterval: (query) => {
      const videos = query.state.data
      const anyActive = videos?.some((v) => v.job_status && v.job_status !== 'done' && v.job_status !== 'error')
      return anyActive ? 3000 : false
    },
  })

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <div>
      <UrlSubmitForm />

      {selected.size >= 2 && (
        <div className="card" style={{ marginBottom: 20, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span className="muted" style={{ fontSize: 13 }}>{selected.size} analyses selected</span>
          <button
            className="btn btn-primary"
            onClick={() => navigate(`/compare?ids=${Array.from(selected).join(',')}`)}
          >
            Compare selected
          </button>
        </div>
      )}

      {isLoading && <div className="grid grid-cards">{[1, 2, 3].map((i) => <div key={i} className="skeleton" style={{ height: 220 }} />)}</div>}

      {data && data.length === 0 && (
        <div className="empty-state">No analyses yet. Paste a YouTube URL above to get started.</div>
      )}

      {data && data.length > 0 && (
        <div className="grid grid-cards">
          {data.map((v) => (
            <AnalysisCard key={v.id} video={v} selected={selected.has(v.id)} onToggleSelect={toggleSelect} />
          ))}
        </div>
      )}
    </div>
  )
}
