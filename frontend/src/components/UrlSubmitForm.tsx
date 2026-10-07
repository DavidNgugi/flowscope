import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { submitVideos } from '../api/client'
import { useNavigate } from 'react-router-dom'

export default function UrlSubmitForm() {
  const [text, setText] = useState('')
  const [forceLocal, setForceLocal] = useState(false)
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const mutation = useMutation({
    mutationFn: (urls: string[]) => submitVideos(urls, { forceLocalTranscription: forceLocal }),
    onSuccess: (results) => {
      queryClient.invalidateQueries({ queryKey: ['videos'] })
      setText('')
      if (results.length === 1) {
        navigate(`/videos/${results[0].video_id}`)
      }
    },
  })

  const urls = text
    .split('\n')
    .map((u) => u.trim())
    .filter(Boolean)

  return (
    <form
      className="card"
      onSubmit={(e) => {
        e.preventDefault()
        if (urls.length > 0) mutation.mutate(urls)
      }}
      style={{ marginBottom: 24 }}
    >
      <div style={{ marginBottom: 10, fontWeight: 600, fontSize: 15 }}>Analyze a product demo</div>
      <textarea
        rows={3}
        placeholder={'Paste one or more YouTube URLs, one per line\nhttps://www.youtube.com/watch?v=...'}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 12 }}>
        <label className="checkbox-row">
          <input type="checkbox" checked={forceLocal} onChange={(e) => setForceLocal(e.target.checked)} />
          Force local transcription (skip YouTube captions)
        </label>
        <button type="submit" className="btn btn-primary" disabled={urls.length === 0 || mutation.isPending}>
          {mutation.isPending ? 'Submitting…' : `Analyze ${urls.length > 0 ? `(${urls.length})` : ''}`}
        </button>
      </div>
      {mutation.isError && (
        <div className="banner banner-error" style={{ marginTop: 12, marginBottom: 0 }}>
          {(mutation.error as Error).message}
        </div>
      )}
    </form>
  )
}
