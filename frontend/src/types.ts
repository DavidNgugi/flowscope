export type JobStatus =
  | 'queued'
  | 'downloading'
  | 'fetching_captions'
  | 'transcribing'
  | 'detecting_scenes'
  | 'extracting_frames'
  | 'deduping_frames'
  | 'aligning_transcript'
  | 'analyzing_frames'
  | 'synthesizing'
  | 'done'
  | 'error'

export const STAGE_LABELS: Record<JobStatus, string> = {
  queued: 'Queued',
  downloading: 'Downloading video',
  fetching_captions: 'Checking captions',
  transcribing: 'Transcribing (Whisper)',
  detecting_scenes: 'Detecting scene changes',
  extracting_frames: 'Extracting frames',
  deduping_frames: 'Deduplicating frames',
  aligning_transcript: 'Aligning transcript',
  analyzing_frames: 'Analyzing screens (Claude)',
  synthesizing: 'Synthesizing flow & insights',
  done: 'Done',
  error: 'Error',
}

export const STAGE_ORDER: JobStatus[] = [
  'queued',
  'downloading',
  'fetching_captions',
  'transcribing',
  'detecting_scenes',
  'extracting_frames',
  'deduping_frames',
  'aligning_transcript',
  'analyzing_frames',
  'synthesizing',
  'done',
]

export interface VideoSummary {
  id: string
  youtube_url: string
  youtube_id: string
  title: string | null
  channel: string | null
  duration_seconds: number | null
  thumbnail_url: string | null
  transcript_source: string | null
  created_at: string
  job_id: string | null
  job_status: JobStatus | null
  progress_current: number | null
  progress_total: number | null
  job_error: string | null
}

export interface UIElement {
  type: string
  label?: string | null
  notes?: string | null
}

export interface FrameAnalysis {
  id: string
  frame_id: string
  screen_name: string | null
  flow_step_label: string | null
  purpose: string | null
  ux_notes: string | null
  ui_elements?: UIElement[]
  transcript_excerpt: string | null
}

export interface Frame {
  id: string
  video_id: string
  timestamp_ms: number
  file_path: string
  url: string
  width: number | null
  height: number | null
  transcript_excerpt: string
  analysis: FrameAnalysis | null
}

export interface TranscriptSegment {
  id: string
  start_ms: number
  end_ms: number
  text: string
  source: string
}

export interface FlowStep {
  step_index: number
  screen_name: string
  frame_id: string
  description: string
}

export interface GalleryEntry {
  frame_id: string
  flow_stage: string
}

export interface VideoSynthesis {
  flow_steps: FlowStep[]
  ux_insights: string[]
  screens_gallery: GalleryEntry[]
}

export interface AIUsageStage {
  stage: string
  call_count: number
  input_tokens: number
  output_tokens: number
  cache_creation_input_tokens: number
  cache_read_input_tokens: number
  estimated_cost_usd: number | null
  cost_complete: boolean
}

export interface AIUsageSummary {
  call_count: number
  input_tokens: number
  output_tokens: number
  cache_creation_input_tokens: number
  cache_read_input_tokens: number
  estimated_cost_usd: number | null
  cost_complete: boolean
  by_stage: AIUsageStage[]
  models: string[]
}

export interface AIUsageRun extends AIUsageSummary {
  job_id: string
  status: JobStatus
  created_at: string
  finished_at: string | null
}

export interface VideoDetail {
  video: {
    id: string
    youtube_url: string
    youtube_id: string
    title: string | null
    channel: string | null
    duration_seconds: number | null
    thumbnail_url: string | null
    transcript_source: string | null
    error_message: string | null
  }
  job: {
    id: string
    status: JobStatus
    progress_current: number
    progress_total: number
    stage_detail: string | null
    error_message: string | null
  } | null
  transcript: TranscriptSegment[]
  frames: Frame[]
  synthesis: VideoSynthesis | null
  ai_usage_runs: AIUsageRun[]
}

export interface HealthStatus {
  ffmpeg_available: boolean
  ffmpeg_version: string | null
  yt_dlp_available: boolean
  anthropic_key_present: boolean
  anthropic_model: string
}

export interface ComparisonResult {
  common_patterns: string[]
  divergences: string[]
  stage_matrix: Record<string, unknown>[]
}
