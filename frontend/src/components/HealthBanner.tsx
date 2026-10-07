import { useQuery } from '@tanstack/react-query'
import { getHealth } from '../api/client'

export default function HealthBanner() {
  const { data } = useQuery({ queryKey: ['health'], queryFn: getHealth, refetchInterval: 15000 })

  if (!data) return null

  const problems: string[] = []
  if (!data.ffmpeg_available) problems.push('ffmpeg is not installed (run `brew install ffmpeg`)')
  if (!data.yt_dlp_available) problems.push('yt-dlp failed to import (check backend dependencies)')
  if (!data.anthropic_key_present) problems.push('ANTHROPIC_API_KEY is not set (add it to backend/.env and restart) — screen analysis will fail')

  if (problems.length === 0) return null

  return (
    <div className="banner banner-warning">
      <span>⚠</span>
      <span>{problems.join(' · ')}</span>
    </div>
  )
}
