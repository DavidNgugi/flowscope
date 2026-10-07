import { useEffect, useRef, useState } from 'react'
import type { JobStatus } from '../types'

interface JobEvent {
  stage: string
  message: string | null
  progress: { current: number; total: number }
  ts: string
}

export interface JobSocketState {
  status: JobStatus | null
  progress: { current: number; total: number }
  message: string | null
  errorMessage: string | null
  events: JobEvent[]
  connected: boolean
}

interface WsMessage {
  type: 'snapshot' | 'progress' | 'error' | 'done'
  job_id: string
  video_id?: string
  status?: JobStatus
  progress?: { current: number; total: number }
  message?: string | null
  error_message?: string | null
  events?: JobEvent[]
  timestamp?: string
}

const MAX_BACKOFF_MS = 15000

/** Subscribes to /ws/jobs/{jobId}, auto-reconnecting with exponential backoff.
 * On (re)connect the server replays a snapshot, so a page refresh mid-job
 * needs no client-side resume logic. */
export function useJobSocket(jobId: string | null | undefined): JobSocketState {
  const [state, setState] = useState<JobSocketState>({
    status: null,
    progress: { current: 0, total: 0 },
    message: null,
    errorMessage: null,
    events: [],
    connected: false,
  })

  const attemptRef = useRef(0)
  const closedByUsRef = useRef(false)

  useEffect(() => {
    if (!jobId) return
    closedByUsRef.current = false
    attemptRef.current = 0
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null

    const connect = () => {
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${window.location.host}/ws/jobs/${jobId}`)

      ws.onopen = () => {
        attemptRef.current = 0
        setState((s) => ({ ...s, connected: true }))
      }

      ws.onmessage = (evt) => {
        const msg: WsMessage = JSON.parse(evt.data)
        if (msg.type === 'error' && !msg.status) {
          setState((s) => ({ ...s, errorMessage: msg.message ?? 'job not found' }))
          return
        }
        setState((s) => ({
          status: msg.status ?? s.status,
          progress: msg.progress ?? s.progress,
          message: msg.message ?? s.message,
          errorMessage: msg.type === 'error' ? msg.message ?? msg.error_message ?? 'error' : msg.error_message ?? s.errorMessage,
          events: msg.type === 'snapshot' ? msg.events ?? [] : s.events,
          connected: true,
        }))
      }

      ws.onclose = () => {
        setState((s) => ({ ...s, connected: false }))
        if (closedByUsRef.current) return
        const delay = Math.min(1000 * 2 ** attemptRef.current, MAX_BACKOFF_MS)
        attemptRef.current += 1
        reconnectTimer = setTimeout(connect, delay)
      }

      ws.onerror = () => {
        ws?.close()
      }
    }

    connect()

    return () => {
      closedByUsRef.current = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      ws?.close()
    }
  }, [jobId])

  return state
}
