"""Orchestrates pipeline stages for a single job, in order.

Each stage is idempotent-ish: it checks existing DB/disk state before doing
work, so re-running via Retry after a failure doesn't redo completed stages.
Stages that are not yet implemented are commented with TODO and the pipeline
currently ends after transcript acquisition (frame extraction/analysis land
in a later change).
"""

import logging

from app.db import db
from app.jobs.manager import JobContext
from app.jobs.stages.align import align_frames_to_transcript
from app.jobs.stages.captions import try_use_captions
from app.jobs.stages.dedupe import dedupe_frames
from app.jobs.stages.download import download_video
from app.jobs.stages.scenes import detect_scene_timestamps, extract_candidate_frames
from app.jobs.stages.synthesis import synthesize_video
from app.jobs.stages.transcribe import transcribe_with_whisper
from app.jobs.stages.vision_analysis import analyze_frames
from app.llm.registry import get_registry
from app.models import JobStatus

logger = logging.getLogger("flowscope.pipeline")


async def run_pipeline(ctx: JobContext) -> None:
    job_row = await db.fetchone(
        "SELECT force_local_transcription FROM jobs WHERE id = ?", (ctx.job_id,)
    )
    video_row = await db.fetchone(
        "SELECT youtube_url FROM videos WHERE id = ?", (ctx.video_id,)
    )
    force_local_transcription = bool(job_row["force_local_transcription"])
    youtube_url = video_row["youtube_url"]

    # --- download ---
    from app.storage import paths

    existing = paths.source_video_path(ctx.video_id)
    if existing.exists() and existing.stat().st_size > 0:
        await ctx.report_progress(JobStatus.DOWNLOADING, message="Using previously downloaded video")
        download_result = None
        segments_row = await db.fetchone(
            "SELECT COUNT(*) as n FROM transcript_segments WHERE video_id = ?", (ctx.video_id,)
        )
        have_transcript = segments_row["n"] > 0
    else:
        download_result = await download_video(ctx, youtube_url)
        have_transcript = False

    # --- captions / transcription ---
    if not have_transcript:
        await ctx.report_progress(JobStatus.FETCHING_CAPTIONS, message="Checking for captions")
        used_captions = False
        if download_result is not None:
            used_captions = await try_use_captions(ctx, download_result, force_local_transcription)
        if not used_captions:
            await ctx.report_progress(JobStatus.TRANSCRIBING, message="Transcribing locally with Whisper")
            await transcribe_with_whisper(ctx)

    # --- frame extraction / dedup / alignment ---
    existing_frames = await db.fetchall(
        "SELECT * FROM frames WHERE video_id = ? ORDER BY timestamp_ms", (ctx.video_id,)
    )
    if existing_frames:
        frames = [dict(f) for f in existing_frames]
    else:
        video_row2 = await db.fetchone(
            "SELECT duration_seconds FROM videos WHERE id = ?", (ctx.video_id,)
        )
        duration_seconds = video_row2["duration_seconds"] if video_row2 else None

        timestamps = await detect_scene_timestamps(ctx, duration_seconds)
        candidates = await extract_candidate_frames(ctx, timestamps)
        frames = await dedupe_frames(ctx, candidates)

    await ctx.report_progress(JobStatus.ALIGNING_TRANSCRIPT, message="Aligning frames to transcript")
    transcript_rows = await db.fetchall(
        "SELECT start_ms, end_ms, text FROM transcript_segments WHERE video_id = ? ORDER BY start_ms",
        (ctx.video_id,),
    )
    transcript_segments = [dict(t) for t in transcript_rows]
    frame_excerpts = align_frames_to_transcript(frames, transcript_segments)

    # Keep previously analyzed frames in sync on retries too. This lets an old
    # report benefit from transcript cleanup without paying for vision calls again.
    existing_excerpt_updates = [
        (frame_excerpts.get(frame["id"], ""), frame["id"], ctx.video_id) for frame in frames
    ]
    if existing_excerpt_updates:
        async with db.write() as conn:
            await conn.executemany(
                "UPDATE frame_analyses SET transcript_excerpt=? WHERE frame_id=? AND video_id=?",
                existing_excerpt_updates,
            )

    # --- vision analysis (provider-neutral) ---
    # A bare resolve is the gate: it raises ProviderNotConfigured, naming the
    # env var to set for whichever provider this purpose selected.
    get_registry().resolve("vision")

    analyzed_frame_ids = {
        row["frame_id"]
        for row in await db.fetchall(
            "SELECT frame_id FROM frame_analyses WHERE video_id = ?", (ctx.video_id,)
        )
    }
    frames_to_analyze = [f for f in frames if f["id"] not in analyzed_frame_ids]
    await analyze_frames(ctx, frames_to_analyze, frame_excerpts)

    # --- synthesis ---
    await synthesize_video(ctx)

    await ctx.complete(message=f"Analysis complete: {len(frames)} screens, flow reconstructed")
