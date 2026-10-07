"""Stage: local Whisper transcription fallback.

Audio is extracted from the already-downloaded source.mp4 via ffmpeg (no
second network fetch needed). faster-whisper (CTranslate2, int8 on CPU) runs
in the cpu_executor since it's a long CPU-bound blocking call.
"""

import logging
import subprocess

from faster_whisper import WhisperModel

from app.config import settings
from app.db import db
from app.deps import run_cpu
from app.jobs.manager import JobContext
from app.jobs.stages.transcript import normalize_transcript_segments
from app.models import TranscriptSource, new_id
from app.storage import paths

logger = logging.getLogger("flowscope.stages.transcribe")

_model: WhisperModel | None = None


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        logger.info("loading faster-whisper model '%s' (this happens once per process)", settings.whisper_model_size)
        _model = WhisperModel(settings.whisper_model_size, device="auto", compute_type="int8")
    return _model


def _extract_audio_sync(video_path, audio_path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video_path),
            "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(audio_path),
        ],
        capture_output=True, check=True, timeout=600,
    )


def _transcribe_sync(audio_path) -> list[tuple[int, int, str]]:
    model = _get_model()
    segments_iter, _info = model.transcribe(str(audio_path), beam_size=5, vad_filter=True)
    out: list[tuple[int, int, str]] = []
    for seg in segments_iter:
        text = seg.text.strip()
        if text:
            out.append((int(seg.start * 1000), int(seg.end * 1000), text))
    return [
        (segment["start_ms"], segment["end_ms"], segment["text"])
        for segment in normalize_transcript_segments(
            [{"start_ms": start, "end_ms": end, "text": text} for start, end, text in out]
        )
    ]


async def transcribe_with_whisper(ctx: JobContext) -> None:
    video_path = paths.source_video_path(ctx.video_id)
    audio_path = paths.audio_path(ctx.video_id)

    try:
        await run_cpu(_extract_audio_sync, video_path, audio_path)
        segments = await run_cpu(_transcribe_sync, audio_path)
    finally:
        audio_path.unlink(missing_ok=True)

    async with db.write() as conn:
        await conn.execute("DELETE FROM transcript_segments WHERE video_id = ?", (ctx.video_id,))
        for start_ms, end_ms, text in segments:
            await conn.execute(
                "INSERT INTO transcript_segments (id, video_id, start_ms, end_ms, text, source) "
                "VALUES (?,?,?,?,?,?)",
                (new_id("seg"), ctx.video_id, start_ms, end_ms, text, TranscriptSource.WHISPER.value),
            )
        await conn.execute(
            "UPDATE videos SET transcript_source=? WHERE id=?",
            (TranscriptSource.WHISPER.value, ctx.video_id),
        )
