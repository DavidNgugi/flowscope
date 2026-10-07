"""Stage: per-frame Claude vision analysis.

Frames are analyzed concurrently (bounded by the global LLM semaphore in
app/llm/client.py, not per-job), which is where real parallelism pays off
most -- each frame call is independent. Progress is reported after every
completed frame, not just at stage end.
"""

import asyncio
import json
import logging

from app.db import db
from app.jobs.manager import JobContext
from app.llm.client import call_tool
from app.llm.usage import AIUsageContext
from app.llm.prompts import FRAME_ANALYSIS_SYSTEM, build_frame_analysis_message, frame_analysis_tool_schema
from app.models import JobStatus, new_id, utcnow_iso

logger = logging.getLogger("flowscope.stages.vision")


async def _analyze_one(
    ctx: JobContext, frame: dict, transcript_excerpt: str, position: int, total: int
) -> dict:
    messages = build_frame_analysis_message(frame["file_path"], transcript_excerpt, position, total)
    return await call_tool(
        system=FRAME_ANALYSIS_SYSTEM,
        messages=messages,
        tool_name="record_frame_analysis",
        tool_schema=frame_analysis_tool_schema(),
        usage_context=AIUsageContext(
            analysis_type="video",
            analysis_id=ctx.job_id,
            job_id=ctx.job_id,
            video_id=ctx.video_id,
            stage="frame_analysis",
            operation_id=frame["id"],
        ),
        max_tokens=1200,
        purpose="vision",
    )


async def analyze_frames(ctx: JobContext, frames: list[dict], frame_excerpts: dict[str, str]) -> None:
    total = len(frames)
    if total == 0:
        return

    await ctx.report_progress(
        JobStatus.ANALYZING_FRAMES, current=0, total=total, message=f"Analyzing {total} frames"
    )

    completed = 0
    completed_lock = asyncio.Lock()
    errors: list[str] = []

    async def run_one(idx: int, frame: dict) -> None:
        nonlocal completed
        try:
            outcome = await _analyze_one(ctx, frame, frame_excerpts.get(frame["id"], ""), idx, total)
        except Exception as exc:
            logger.exception("frame analysis failed for frame %s", frame["id"])
            errors.append(str(exc))
            outcome = None

        if outcome is not None:
            data = outcome.data
            async with db.write() as conn:
                await conn.execute(
                    "INSERT INTO frame_analyses (id, frame_id, video_id, screen_name, flow_step_label, "
                    "purpose, ux_notes, ui_elements_json, raw_llm_json, transcript_excerpt, model_used, "
                    "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        new_id("fa"), frame["id"], ctx.video_id,
                        data.get("screen_name"), data.get("flow_step_label"), data.get("purpose"),
                        data.get("ux_notes"), json.dumps(data.get("ui_elements", [])),
                        json.dumps(data), frame_excerpts.get(frame["id"], ""),
                        outcome.model, utcnow_iso(),
                    ),
                )

        async with completed_lock:
            completed += 1
            n = completed
        await ctx.report_progress(
            JobStatus.ANALYZING_FRAMES, current=n, total=total,
            message=f"Analyzed frame {n}/{total}",
        )

    await asyncio.gather(*(run_one(i, f) for i, f in enumerate(frames)))

    if errors and len(errors) == total:
        raise RuntimeError(f"all {total} frame analyses failed; first error: {errors[0]}")
