import json
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.db import db
from app.deps import run_cpu
from app.jobs.manager import job_manager
from app.jobs.stages.align import align_frames_to_transcript
from app.jobs.stages.download import extract_youtube_id
from app.jobs.stages.transcript import normalize_transcript_segments
from app.llm.usage import video_usage_history
from app.models import JobStatus, new_id, utcnow_iso
from app.schemas import SubmitVideosRequest, SubmitVideosResponseItem

logger = logging.getLogger("flowscope.api.videos")

router = APIRouter()


@router.post("/videos", response_model=list[SubmitVideosResponseItem])
async def submit_videos(payload: SubmitVideosRequest) -> list[SubmitVideosResponseItem]:
    results: list[SubmitVideosResponseItem] = []

    for url in payload.urls:
        url = url.strip()
        if not url:
            continue
        youtube_id = extract_youtube_id(url)

        if not payload.force:
            existing_video = await db.fetchone(
                "SELECT v.id as video_id, j.id as job_id, j.status as status "
                "FROM videos v LEFT JOIN jobs j ON j.video_id = v.id "
                "WHERE v.youtube_id = ? ORDER BY j.created_at DESC LIMIT 1",
                (youtube_id,),
            )
            if existing_video is not None and existing_video["status"] == JobStatus.DONE.value:
                results.append(
                    SubmitVideosResponseItem(
                        video_id=existing_video["video_id"],
                        job_id=existing_video["job_id"],
                        youtube_id=youtube_id,
                        status=existing_video["status"],
                        reused=True,
                    )
                )
                continue

        video_id = new_id("vid")
        job_id = new_id("job")
        now = utcnow_iso()

        async with db.write() as conn:
            await conn.execute(
                "INSERT INTO videos (id, youtube_url, youtube_id, created_at) VALUES (?,?,?,?)",
                (video_id, url, youtube_id, now),
            )
            await conn.execute(
                "INSERT INTO jobs (id, video_id, status, force_local_transcription, created_at) "
                "VALUES (?,?,?,?,?)",
                (job_id, video_id, JobStatus.QUEUED.value, int(payload.force_local_transcription), now),
            )

        await job_manager.enqueue(job_id)

        results.append(
            SubmitVideosResponseItem(
                video_id=video_id, job_id=job_id, youtube_id=youtube_id, status=JobStatus.QUEUED.value
            )
        )

    return results


@router.get("/videos")
async def list_videos() -> list[dict]:
    rows = await db.fetchall(
        "SELECT v.*, j.id as job_id, j.status as job_status, j.progress_current, j.progress_total, "
        "j.error_message as job_error "
        "FROM videos v LEFT JOIN jobs j ON j.id = ("
        "  SELECT id FROM jobs WHERE video_id = v.id ORDER BY created_at DESC LIMIT 1"
        ") "
        "ORDER BY v.created_at DESC"
    )
    return [dict(r) for r in rows]


@router.get("/videos/{video_id}")
async def get_video(video_id: str) -> dict:
    video = await db.fetchone("SELECT * FROM videos WHERE id = ?", (video_id,))
    if video is None:
        raise HTTPException(status_code=404, detail="video not found")

    job = await db.fetchone(
        "SELECT * FROM jobs WHERE video_id = ? ORDER BY created_at DESC LIMIT 1", (video_id,)
    )
    transcript = await db.fetchall(
        "SELECT * FROM transcript_segments WHERE video_id = ? ORDER BY start_ms", (video_id,)
    )
    frames = await db.fetchall(
        "SELECT * FROM frames WHERE video_id = ? ORDER BY timestamp_ms", (video_id,)
    )
    analyses = await db.fetchall(
        "SELECT * FROM frame_analyses WHERE video_id = ?", (video_id,)
    )
    synthesis = await db.fetchone(
        "SELECT * FROM video_syntheses WHERE video_id = ?", (video_id,)
    )

    normalized_transcript = normalize_transcript_segments([dict(t) for t in transcript])
    frame_excerpts = align_frames_to_transcript(
        [dict(frame) for frame in frames], normalized_transcript
    )
    analyses_by_frame = {a["frame_id"]: dict(a) for a in analyses}
    for a in analyses_by_frame.values():
        # Recompute on read so reports created before transcript cleanup are
        # corrected immediately, without requiring another vision-analysis run.
        a["transcript_excerpt"] = frame_excerpts.get(a["frame_id"], "")
        if a.get("ui_elements_json"):
            a["ui_elements"] = json.loads(a["ui_elements_json"])

    from app.config import settings

    def frame_url(file_path: str) -> str:
        from pathlib import Path

        try:
            rel = Path(file_path).relative_to(settings.media_path)
        except ValueError:
            # Stored paths are absolute on whichever machine produced them, so
            # they stop resolving as soon as the media moves -- host to
            # container, or a different DATA_DIR. The portion below the last
            # "media" segment is stable across roots, so use that instead of
            # handing the browser a path that cannot exist.
            parts = Path(file_path).parts
            if "media" not in parts:
                return file_path
            rel = Path(*parts[parts.index("media") + 1 :])
        return f"/media/{rel.as_posix()}"

    return {
        "video": dict(video),
        "job": dict(job) if job else None,
        "ai_usage_runs": await video_usage_history(video_id),
        "transcript": normalized_transcript,
        "frames": [
            {
                **dict(f),
                "url": frame_url(f["file_path"]),
                "transcript_excerpt": frame_excerpts.get(f["id"], ""),
                "analysis": analyses_by_frame.get(f["id"]),
            }
            for f in frames
        ],
        "synthesis": (
            {
                "flow_steps": json.loads(synthesis["flow_steps_json"]) if synthesis["flow_steps_json"] else [],
                "ux_insights": json.loads(synthesis["ux_insights_json"]) if synthesis["ux_insights_json"] else [],
                "screens_gallery": json.loads(synthesis["screens_gallery_json"]) if synthesis["screens_gallery_json"] else [],
            }
            if synthesis
            else None
        ),
    }


@router.post("/videos/{video_id}/retry")
async def retry_video(video_id: str) -> dict:
    video = await db.fetchone("SELECT id FROM videos WHERE id = ?", (video_id,))
    if video is None:
        raise HTTPException(status_code=404, detail="video not found")

    job = await db.fetchone(
        "SELECT * FROM jobs WHERE video_id = ? ORDER BY created_at DESC LIMIT 1", (video_id,)
    )
    now = utcnow_iso()
    if job is not None and job["status"] != JobStatus.DONE.value:
        job_id = job["id"]
        async with db.write() as conn:
            await conn.execute(
                "UPDATE jobs SET status=?, error_message=NULL, progress_current=0, progress_total=0, "
                "started_at=NULL, finished_at=NULL WHERE id=?",
                (JobStatus.QUEUED.value, job_id),
            )
    else:
        job_id = new_id("job")
        force_local = bool(job["force_local_transcription"]) if job else False
        async with db.write() as conn:
            await conn.execute(
                "INSERT INTO jobs (id, video_id, status, force_local_transcription, created_at) "
                "VALUES (?,?,?,?,?)",
                (job_id, video_id, JobStatus.QUEUED.value, int(force_local), now),
            )

    await job_manager.enqueue(job_id)
    return {"video_id": video_id, "job_id": job_id, "status": JobStatus.QUEUED.value}


@router.post("/videos/{video_id}/reanalyze")
async def reanalyze_video(video_id: str) -> dict:
    """Discard derived report artifacts and rerun extraction/analysis for this video."""
    video = await db.fetchone("SELECT id FROM videos WHERE id = ?", (video_id,))
    if video is None:
        raise HTTPException(status_code=404, detail="video not found")

    latest_job = await db.fetchone(
        "SELECT * FROM jobs WHERE video_id = ? ORDER BY created_at DESC LIMIT 1", (video_id,)
    )
    if latest_job is not None and latest_job["status"] not in (
        JobStatus.DONE.value,
        JobStatus.ERROR.value,
    ):
        raise HTTPException(status_code=409, detail="This video is already being analyzed")

    comparison_rows = await db.fetchall("SELECT id, video_ids_json FROM comparisons")
    comparison_ids = []
    for row in comparison_rows:
        try:
            if video_id in json.loads(row["video_ids_json"]):
                comparison_ids.append(row["id"])
        except (json.JSONDecodeError, TypeError):
            continue

    job_id = new_id("job")
    now = utcnow_iso()
    force_local = bool(latest_job["force_local_transcription"]) if latest_job else False
    async with db.write() as conn:
        await conn.execute("DELETE FROM frame_analyses WHERE video_id = ?", (video_id,))
        await conn.execute("DELETE FROM video_syntheses WHERE video_id = ?", (video_id,))
        await conn.execute("DELETE FROM frames WHERE video_id = ?", (video_id,))
        if comparison_ids:
            await conn.executemany(
                "DELETE FROM ai_usage_records WHERE analysis_type='comparison' AND analysis_id=?",
                [(item_id,) for item_id in comparison_ids],
            )
            await conn.executemany(
                "DELETE FROM comparisons WHERE id = ?", [(item_id,) for item_id in comparison_ids]
            )
        await conn.execute(
            "INSERT INTO jobs (id, video_id, status, force_local_transcription, created_at) "
            "VALUES (?,?,?,?,?)",
            (job_id, video_id, JobStatus.QUEUED.value, int(force_local), now),
        )

    from app.storage import paths

    frames_dir = paths.video_dir(video_id) / "frames"
    if frames_dir.exists():
        for frame_path in frames_dir.glob("frame_*.jpg"):
            frame_path.unlink(missing_ok=True)
    candidates_dir = paths.video_dir(video_id) / "candidates"
    if candidates_dir.exists():
        for candidate_path in candidates_dir.iterdir():
            if candidate_path.is_file():
                candidate_path.unlink(missing_ok=True)
        candidates_dir.rmdir()

    await job_manager.enqueue(job_id)
    return {"video_id": video_id, "job_id": job_id, "status": JobStatus.QUEUED.value}


@router.delete("/videos/{video_id}")
async def delete_video(video_id: str, delete_media: bool = False) -> dict:
    video = await db.fetchone("SELECT id FROM videos WHERE id = ?", (video_id,))
    if video is None:
        raise HTTPException(status_code=404, detail="video not found")

    async with db.write() as conn:
        await conn.execute("DELETE FROM frame_analyses WHERE video_id = ?", (video_id,))
        await conn.execute("DELETE FROM frames WHERE video_id = ?", (video_id,))
        await conn.execute("DELETE FROM transcript_segments WHERE video_id = ?", (video_id,))
        await conn.execute("DELETE FROM video_syntheses WHERE video_id = ?", (video_id,))
        await conn.execute("DELETE FROM ai_usage_records WHERE video_id = ?", (video_id,))
        await conn.execute("DELETE FROM job_events WHERE job_id IN (SELECT id FROM jobs WHERE video_id = ?)", (video_id,))
        await conn.execute("DELETE FROM jobs WHERE video_id = ?", (video_id,))
        await conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))

    if delete_media:
        import shutil

        from app.storage import paths

        shutil.rmtree(paths.video_dir(video_id), ignore_errors=True)

    return {"video_id": video_id, "deleted": True}


@router.get("/videos/{video_id}/export/pdf")
async def export_pdf(video_id: str) -> FileResponse:
    video = await db.fetchone("SELECT * FROM videos WHERE id = ?", (video_id,))
    if video is None:
        raise HTTPException(status_code=404, detail="video not found")

    synthesis = await db.fetchone("SELECT * FROM video_syntheses WHERE video_id = ?", (video_id,))
    if synthesis is None:
        raise HTTPException(status_code=409, detail="This video has no completed analysis yet")

    frames = await db.fetchall(
        "SELECT f.*, fa.transcript_excerpt FROM frames f "
        "LEFT JOIN frame_analyses fa ON fa.frame_id = f.id "
        "WHERE f.video_id = ?",
        (video_id,),
    )
    transcript = await db.fetchall(
        "SELECT start_ms, end_ms, text FROM transcript_segments "
        "WHERE video_id = ? ORDER BY start_ms",
        (video_id,),
    )
    normalized_transcript = normalize_transcript_segments([dict(row) for row in transcript])
    frames_list = [dict(frame) for frame in frames]
    frame_excerpts = align_frames_to_transcript(frames_list, normalized_transcript)
    for frame in frames_list:
        frame["transcript_excerpt"] = frame_excerpts.get(frame["id"], "")
    frames_by_id = {frame["id"]: frame for frame in frames_list}
    flow_steps = json.loads(synthesis["flow_steps_json"] or "[]")
    ux_insights = json.loads(synthesis["ux_insights_json"] or "[]")

    from app.export.pdf import build_case_study_pdf

    out_path = Path(tempfile.gettempdir()) / f"flowscope_{video_id}.pdf"
    await run_cpu(
        build_case_study_pdf,
        video=dict(video),
        ux_insights=ux_insights,
        flow_steps=flow_steps,
        frames_by_id=frames_by_id,
        output_path=out_path,
    )

    safe_title = "".join(c for c in (video["title"] or video_id) if c.isalnum() or c in " -_").strip()
    filename = f"{safe_title[:80] or video_id}.pdf"
    return FileResponse(out_path, media_type="application/pdf", filename=filename)
