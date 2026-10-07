"""Stage: align each kept frame's timestamp to the transcript being spoken then.

Pure in-memory logic, no I/O -- O(frames * segments), fine at this scale
(tens of frames, hundreds of segments per video).
"""

from app.jobs.stages.transcript import normalize_transcript_segments

DEFAULT_WINDOW_MS = 6000
MAX_EXCERPT_CHARS = 600


def align_frames_to_transcript(
    frames: list[dict], transcript_segments: list[dict], window_ms: int = DEFAULT_WINDOW_MS
) -> dict[str, str]:
    """Returns {frame_id: transcript_excerpt} for the narration active around each frame's timestamp."""
    segs = normalize_transcript_segments(transcript_segments)
    excerpts: dict[str, str] = {}
    for frame in frames:
        t = frame["timestamp_ms"]
        lo, hi = t - window_ms, t + window_ms
        texts = [s["text"] for s in segs if s["end_ms"] >= lo and s["start_ms"] <= hi]
        excerpt = " ".join(texts).strip()
        if len(excerpt) > MAX_EXCERPT_CHARS:
            excerpt = excerpt[:MAX_EXCERPT_CHARS].rsplit(" ", 1)[0].rstrip() + "…"
        excerpts[frame["id"]] = excerpt
    return excerpts
