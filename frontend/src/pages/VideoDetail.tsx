import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { deleteVideo, getVideo, reanalyzeVideo, retryVideo } from '../api/client'
import JobProgressStepper from '../components/JobProgressStepper'
import ScreensGallery from '../components/ScreensGallery'
import FlowTimeline from '../components/FlowTimeline'
import TranscriptPanel from '../components/TranscriptPanel'
import InsightsList from '../components/InsightsList'
import StatusBadge from '../components/StatusBadge'
import AIUsagePanel from '../components/AIUsagePanel'

type Tab = 'screens' | 'flow' | 'insights' | 'transcript' | 'usage'

export default function VideoDetail() {
  const { videoId } = useParams<{ videoId: string }>()
  const [tab, setTab] = useState<Tab>('screens')
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const { data, isLoading } = useQuery({
    queryKey: ['video', videoId],
    queryFn: () => getVideo(videoId!),
    enabled: !!videoId,
    refetchInterval: (query) => {
      const status = query.state.data?.job?.status
      return status && status !== 'done' && status !== 'error' ? 3000 : false
    },
  })

  const retryMutation = useMutation({
    mutationFn: () => retryVideo(videoId!),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['video', videoId] }),
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteVideo(videoId!, true),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['videos'] })
      navigate('/')
    },
  })

  const reanalyzeMutation = useMutation({
    mutationFn: () => reanalyzeVideo(videoId!),
    onSuccess: () => {
      setTab('screens')
      queryClient.invalidateQueries({ queryKey: ['video', videoId] })
      queryClient.invalidateQueries({ queryKey: ['videos'] })
    },
  })

  if (isLoading || !data) {
    return <div className="skeleton" style={{ height: 300 }} />
  }

  const { video, job, transcript, frames, synthesis, ai_usage_runs } = data
  const isRunning = job && job.status !== 'done' && job.status !== 'error'
  const isError = job?.status === 'error'

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16, marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 6px' }}>{video.title ?? 'Untitled video'}</h1>
          <div className="muted" style={{ fontSize: 13, display: 'flex', gap: 10, alignItems: 'center' }}>
            {video.channel && <span>{video.channel}</span>}
            <a href={video.youtube_url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>
              View on YouTube ↗
            </a>
            {job && <StatusBadge status={job.status} />}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
          {isError && (
            <button className="btn btn-primary" onClick={() => retryMutation.mutate()} disabled={retryMutation.isPending}>
              Retry
            </button>
          )}
          {synthesis && (
            <a className="btn" href={`/api/videos/${video.id}/export/pdf`}>
              Export PDF
            </a>
          )}
          <button
            className="btn btn-primary"
            onClick={() => reanalyzeMutation.mutate()}
            disabled={!!isRunning || reanalyzeMutation.isPending}
            title="Extract screens again and rebuild this report"
          >
            {reanalyzeMutation.isPending ? 'Starting…' : 'Re-analyze'}
          </button>
          <button className="btn btn-danger" onClick={() => deleteMutation.mutate()} disabled={deleteMutation.isPending}>
            Delete
          </button>
        </div>
      </div>

      {isError && (
        <div className="banner banner-error">{job?.error_message ?? 'Job failed'}</div>
      )}

      {reanalyzeMutation.isError && (
        <div className="banner banner-error">{reanalyzeMutation.error.message}</div>
      )}

      {isRunning && job && (
        <div style={{ marginBottom: 24 }}>
          <JobProgressStepper jobId={job.id} onDone={() => queryClient.invalidateQueries({ queryKey: ['video', videoId] })} />
        </div>
      )}

      {(job?.status === 'done' || frames.length > 0) && (
        <>
          <div className="tabs">
            {(['screens', 'flow', 'insights', 'transcript', 'usage'] as Tab[]).map((t) => (
              <button key={t} className={`tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>
                {t === 'screens' && `Screens (${frames.length})`}
                {t === 'flow' && `Flow (${synthesis?.flow_steps.length ?? 0})`}
                {t === 'insights' && `Insights (${synthesis?.ux_insights.length ?? 0})`}
                {t === 'transcript' && `Transcript (${transcript.length})`}
                {t === 'usage' && `AI Usage (${ai_usage_runs[0]?.call_count ?? 0})`}
              </button>
            ))}
          </div>

          {tab === 'screens' && <ScreensGallery frames={frames} gallery={synthesis?.screens_gallery ?? []} />}
          {tab === 'flow' && <FlowTimeline steps={synthesis?.flow_steps ?? []} frames={frames} />}
          {tab === 'insights' && <InsightsList insights={synthesis?.ux_insights ?? []} />}
          {tab === 'transcript' && <TranscriptPanel segments={transcript} source={video.transcript_source} />}
          {tab === 'usage' && <AIUsagePanel runs={ai_usage_runs} />}
        </>
      )}
    </div>
  )
}
