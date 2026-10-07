"""Stage: parse a downloaded VTT caption file into transcript_segments.

Applies a simple, intentionally-transparent "low quality" heuristic (average
words per segment) to decide whether auto-captions are worth keeping or
whether we should fall back to local Whisper -- callers can also force the
Whisper path regardless (force_local_transcription).
"""

import logging

import webvtt

from app.db import db
from app.jobs.manager import JobContext
from app.jobs.stages.download import DownloadResult
from app.jobs.stages.transcript import normalize_transcript_segments
from app.models import TranscriptSource, new_id

logger = logging.getLogger("flowscope.stages.captions")

MIN_AVG_WORDS_PER_SEGMENT = 2.0
MIN_SEGMENT_COUNT = 3


def _ts_to_ms(ts: str) -> int:
    h, m, rest = ts.split(":")
    s, ms = rest.split(".")
    return ((int(h) * 3600 + int(m) * 60 + int(s)) * 1000) + int(ms)


def _parse_vtt(path) -> list[tuple[int, int, str]]:
    raw_segments: list[dict] = []
    for caption in webvtt.read(str(path)):
        text = " ".join(caption.text.strip().splitlines()).strip()
        if not text:
            continue
        raw_segments.append(
            {"start_ms": _ts_to_ms(caption.start), "end_ms": _ts_to_ms(caption.end), "text": text}
        )
    return [
        (segment["start_ms"], segment["end_ms"], segment["text"])
        for segment in normalize_transcript_segments(raw_segments)
    ]


def _is_good_quality(segments: list[tuple[int, int, str]]) -> bool:
    if len(segments) < MIN_SEGMENT_COUNT:
        return False
    total_words = sum(len(text.split()) for _, _, text in segments)
    avg_words = total_words / len(segments)
    return avg_words >= MIN_AVG_WORDS_PER_SEGMENT


async def try_use_captions(
    ctx: JobContext, download_result: DownloadResult, force_local_transcription: bool
) -> bool:
    """Returns True if captions were accepted and stored; False means fall back to Whisper."""
    if force_local_transcription or download_result.caption_file is None:
        return False

    try:
        segments = _parse_vtt(download_result.caption_file)
    except Exception:
        logger.exception("failed to parse VTT for video %s", ctx.video_id)
        return False

    if not _is_good_quality(segments):
        logger.info("captions for video %s judged low quality, falling back to Whisper", ctx.video_id)
        return False

    source = download_result.caption_source or TranscriptSource.AUTO_CAPTION
    async with db.write() as conn:
        # Replacing makes this stage safe to retry and prevents partial/duplicate
        # transcripts if a previous attempt stopped during ingestion.
        await conn.execute("DELETE FROM transcript_segments WHERE video_id = ?", (ctx.video_id,))
        for start_ms, end_ms, text in segments:
            await conn.execute(
                "INSERT INTO transcript_segments (id, video_id, start_ms, end_ms, text, source) "
                "VALUES (?,?,?,?,?,?)",
                (new_id("seg"), ctx.video_id, start_ms, end_ms, text, source.value),
            )
        await conn.execute(
            "UPDATE videos SET transcript_source=? WHERE id=?", (source.value, ctx.video_id)
        )
    return True
