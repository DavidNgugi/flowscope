import type { AIUsageSummary, ComparisonResult, HealthStatus, VideoDetail, VideoSummary } from '../types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`${res.status} ${res.statusText}: ${body}`)
  }
  return res.json() as Promise<T>
}

export interface SubmitResult {
  video_id: string
  job_id: string
  youtube_id: string
  status: string
  reused: boolean
}

export function getHealth(): Promise<HealthStatus> {
  return request('/api/health')
}

export function submitVideos(
  urls: string[],
  opts?: { force?: boolean; forceLocalTranscription?: boolean }
): Promise<SubmitResult[]> {
  return request('/api/videos', {
    method: 'POST',
    body: JSON.stringify({
      urls,
      force: opts?.force ?? false,
      force_local_transcription: opts?.forceLocalTranscription ?? false,
    }),
  })
}

export function listVideos(): Promise<VideoSummary[]> {
  return request('/api/videos')
}

export function getVideo(videoId: string): Promise<VideoDetail> {
  return request(`/api/videos/${videoId}`)
}

export function retryVideo(videoId: string): Promise<{ video_id: string; job_id: string; status: string }> {
  return request(`/api/videos/${videoId}/retry`, { method: 'POST' })
}

export function reanalyzeVideo(videoId: string): Promise<{ video_id: string; job_id: string; status: string }> {
  return request(`/api/videos/${videoId}/reanalyze`, { method: 'POST' })
}

export function deleteVideo(videoId: string, deleteMedia: boolean): Promise<{ deleted: boolean }> {
  return request(`/api/videos/${videoId}?delete_media=${deleteMedia}`, { method: 'DELETE' })
}

export function getComparison(videoIds: string[], forceRefresh = false): Promise<{
  video_ids: string[]
  result: ComparisonResult
  ai_usage: AIUsageSummary
}> {
  const params = new URLSearchParams({ video_ids: videoIds.join(','), force_refresh: String(forceRefresh) })
  return request(`/api/comparisons?${params.toString()}`)
}
