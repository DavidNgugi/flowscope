import { useQuery } from '@tanstack/react-query'
import { getHealth } from '../api/client'

export default function HealthBanner() {
  const { data } = useQuery({ queryKey: ['health'], queryFn: getHealth, refetchInterval: 15000 })

  if (!data) return null

  const problems: string[] = []
  if (!data.ffmpeg_available) problems.push('ffmpeg is not installed (run `brew install ffmpeg`)')
  if (!data.yt_dlp_available) problems.push('yt-dlp failed to import (check backend dependencies)')
  if (!data.llm_vision_model) problems.push('No model configured for screen analysis - add a provider key (e.g. OPENAI_API_KEY) to backend/.env and restart')

  if (problems.length === 0) return null

  return (
    <div className="banner banner-warning">
      <span>⚠</span>
      <span>{problems.join(' · ')}</span>
    </div>
  )
}
