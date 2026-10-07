"""Run the LLM stages over frames that were already extracted.

FlowScope's pipeline downloads, extracts frames and then pays for vision
analysis in one pass. When the frames already exist -- because an earlier run
stopped at the LLM stage, or because the analysis is being re-run against a
different model -- this script does just the expensive part:

    phase 1  frame analyses (vision), all videos, bounded by the global semaphore
    phase 2  per-video synthesis, all videos

The phases are separate on purpose. Analysing and synthesising one video at a
time leaves the frame pipeline idle during every synthesis call, which on a
multi-video corpus costs more than the synthesis work itself. Running each phase
across all videos keeps the vision calls saturated and lets the (fewer, larger)
synthesis calls overlap.

Nothing is re-downloaded, re-transcribed or re-extracted, and both phases skip
work that is already done, so the script is safe to interrupt and re-run.

Examples
--------
    python scripts/evaluate_existing_frames.py --data-dir ./data
    python scripts/evaluate_existing_frames.py --data-dir ./data --limit 20
    python scripts/evaluate_existing_frames.py --data-dir ./data --synthesis-only
    python scripts/evaluate_existing_frames.py --data-dir ./data --sequential
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# The data directory must be known before app.config is imported, because
# Settings reads it at import time.
_parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
_parser.add_argument("--data-dir", required=True, help="FlowScope DATA_DIR holding the SQLite DB and media")
_parser.add_argument("--limit", type=int, default=0, help="Max videos to process (0 = all)")
_parser.add_argument("--force", action="store_true", help="Re-analyse frames that already have results")
_parser.add_argument("--synthesis-only", action="store_true", help="Skip vision, only synthesise videos")
_parser.add_argument("--no-synthesis", action="store_true", help="Only run vision analysis")
_parser.add_argument("--sequential", action="store_true", help="One video at a time (lower peak load)")
_pre = _parser.parse_args()

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DATA_DIR"] = str(Path(_pre.data_dir).expanduser())

from app.db import db  # noqa: E402
from app.jobs.manager import JobContext  # noqa: E402
from app.jobs.stages.align import align_frames_to_transcript  # noqa: E402
from app.jobs.stages.synthesis import synthesize_video  # noqa: E402
from app.jobs.stages.vision_analysis import analyze_frames  # noqa: E402
from app.llm.registry import get_registry  # noqa: E402
from app.llm.usage import summarize_usage_rows  # noqa: E402
from app.models import new_id, utcnow_iso  # noqa: E402

_progress_lock = asyncio.Lock()
_state: dict[str, dict] = {}


async def _job_for(video_id: str) -> str:
    row = await db.fetchone(
        "SELECT id FROM jobs WHERE video_id = ? ORDER BY created_at DESC LIMIT 1", (video_id,)
    )
    if row is not None:
        return row["id"]
    job_id = new_id("job")
    async with db.write() as conn:
        await conn.execute(
            "INSERT INTO jobs (id, video_id, status, created_at) VALUES (?,?,?,?)",
            (job_id, video_id, "queued", utcnow_iso()),
        )
    return job_id


async def _finish(ctx: JobContext, message: str) -> None:
    """Mark the job complete.

    The vision and synthesis stages only report *progress*, so a run that ends
    after the last synthesis would otherwise leave the job in a non-terminal
    state -- which the server rewrites to "interrupted by restart" on the next
    boot. Completing here is what makes the UI show the run as finished.
    """
    await ctx.complete(message=message)


async def analyse_video(video: dict) -> None:
    """Vision phase for one video: analyse whatever frames are missing."""
    video_id, title = video["id"], video["title"] or video["id"]
    ctx = JobContext(await _job_for(video_id), video_id)
    frames = [dict(f) for f in await db.fetchall(
        "SELECT * FROM frames WHERE video_id = ? ORDER BY timestamp_ms", (video_id,)
    )]
    analysed = {
        row["frame_id"]
        for row in await db.fetchall("SELECT frame_id FROM frame_analyses WHERE video_id = ?", (video_id,))
    }
    todo = frames if _pre.force else [f for f in frames if f["id"] not in analysed]
    _state[video_id].update(frames=len(frames), already=len(analysed))

    if todo:
        segments = [dict(s) for s in await db.fetchall(
            "SELECT start_ms, end_ms, text FROM transcript_segments WHERE video_id = ? ORDER BY start_ms",
            (video_id,),
        )]
        excerpts = align_frames_to_transcript(frames, segments)
        await analyze_frames(ctx, todo, excerpts)

    _state[video_id].update(analysed=len(todo), vision_done=True)
    if _pre.no_synthesis:
        await _finish(ctx, f"Analysis complete: {len(todo)} frame analyses")
    async with _progress_lock:
        done = sum(1 for s in _state.values() if s.get("vision_done"))
        print(f"  vision {done:>3}/{len(_state)} videos  {title[:44]}  +{len(todo)} frames", flush=True)


async def synthesise_video(video: dict) -> None:
    """Synthesis phase for one video."""
    video_id, title = video["id"], video["title"] or video["id"]
    existing = await db.fetchone("SELECT id FROM video_syntheses WHERE video_id = ?", (video_id,))
    if existing is not None and not _pre.force:
        # Already synthesised by an earlier run: do not pay for it again. Still
        # mark the job done, which also repairs runs left non-terminal by an
        # earlier version of this script.
        ctx = JobContext(await _job_for(video_id), video_id)
        await _finish(ctx, "Analysis complete: frame analyses + synthesis")
        _state[video_id]["synthesis_done"] = True
        _state[video_id]["synthesis_cached"] = True
        async with _progress_lock:
            done = sum(1 for s in _state.values() if s.get("synthesis_done"))
            print(f"  synth  {done:>3}/{len(_state)} videos  {title[:44]}  (cached)", flush=True)
        return
    ctx = JobContext(await _job_for(video_id), video_id)
    await synthesize_video(ctx)
    await _finish(ctx, "Analysis complete: frame analyses + synthesis")
    _state[video_id]["synthesis_done"] = True
    async with _progress_lock:
        done = sum(1 for s in _state.values() if s.get("synthesis_done"))
        print(f"  synth  {done:>3}/{len(_state)} videos  {title[:44]}", flush=True)


async def _safe(coro, video: dict, phase: str) -> None:
    try:
        await coro
    except Exception as exc:  # noqa: BLE001 - one bad video must not stop the corpus
        _state[video["id"]][f"{phase}_error"] = f"{type(exc).__name__}: {exc}"[:200]
        print(f"  {phase} FAILED for {(video['title'] or video['id'])[:44]}: {type(exc).__name__}", flush=True)


async def main() -> int:
    await db.connect()
    registry = get_registry()
    print(f"data dir : {os.environ['DATA_DIR']}")
    print(f"providers: {', '.join(registry.available()) or 'none configured'}")
    for purpose in ("vision", "synthesis"):
        try:
            print(f"{purpose:9}: {registry.resolve(purpose).describe()}")
        except Exception as exc:
            print(f"{purpose:9}: unavailable ({exc})")
    mode = "sequential" if _pre.sequential else "two-phase, parallel per phase"
    print(f"mode     : {mode}\n")

    videos = [dict(v) for v in await db.fetchall("SELECT id, title FROM videos ORDER BY created_at")]
    counts = {}
    for video in videos:
        row = await db.fetchone("SELECT COUNT(*) AS n FROM frames WHERE video_id = ?", (video["id"],))
        counts[video["id"]] = row["n"]
    videos = [v for v in sorted(videos, key=lambda v: -counts[v["id"]]) if counts[v["id"]]]
    if _pre.limit:
        videos = videos[: _pre.limit]
    for video in videos:
        _state[video["id"]] = {"title": video["title"]}

    print(f"{len(videos)} video(s) with frames\n")

    async def run_phase(phase: str, worker) -> None:
        print(f"[{phase}]", flush=True)
        if _pre.sequential:
            for video in videos:
                await _safe(worker(video), video, phase)
        else:
            await asyncio.gather(*(_safe(worker(v) , v, phase) for v in videos))

    if not _pre.synthesis_only:
        await run_phase("vision", analyse_video)
    if not _pre.no_synthesis:
        await run_phase("synthesis", synthesise_video)

    total_analysed = sum(s.get("analysed", 0) for s in _state.values())
    usage = summarize_usage_rows([dict(r) for r in await db.fetchall("SELECT * FROM ai_usage_records")])
    print(f"\nframes analysed this run: {total_analysed}")
    print(f"calls={usage['call_count']} in={usage['input_tokens']} out={usage['output_tokens']} "
          f"cost={'$%.4f' % usage['estimated_cost_usd'] if usage['estimated_cost_usd'] is not None else 'unknown'}"
          f"{'' if usage['cost_complete'] else ' (some models have no published rate)'}")
    for stage in usage["by_stage"]:
        print(f"  {stage['stage']:<16} calls={stage['call_count']:<5} "
              f"in={stage['input_tokens']:<9} out={stage['output_tokens']}")

    failures = {vid: s for vid, s in _state.items() if s.get("vision_error") or s.get("synthesis_error")}
    if failures:
        print(f"\n{len(failures)} video(s) reported an error:")
        for vid, state in list(failures.items())[:10]:
            print(f"  ! {str(state.get('title'))[:44]}: "
                  f"{state.get('vision_error') or state.get('synthesis_error')}")

    await db.close()
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
