"""Stage: per-video synthesis pass.

Operates on the text of all frame_analyses for one video (no re-sending
images) to reconstruct an ordered flow, surface UX insights, and tag a
screens gallery by flow stage.
"""

import json
import logging

from app.db import db
from app.jobs.manager import JobContext
from app.llm.client import call_tool
from app.llm.usage import AIUsageContext
from app.llm.prompts import SYNTHESIS_SYSTEM, build_synthesis_message, video_synthesis_tool_schema
from app.models import JobStatus, new_id, utcnow_iso

logger = logging.getLogger("flowscope.stages.synthesis")


async def synthesize_video(ctx: JobContext) -> None:
    await ctx.report_progress(JobStatus.SYNTHESIZING, message="Synthesizing flow and insights")

    rows = await db.fetchall(
        "SELECT fa.*, f.timestamp_ms FROM frame_analyses fa "
        "JOIN frames f ON f.id = fa.frame_id "
        "WHERE fa.video_id = ? ORDER BY f.timestamp_ms",
        (ctx.video_id,),
    )
    if not rows:
        logger.warning("no frame analyses for video %s, skipping synthesis", ctx.video_id)
        return

    entries = [
        {
            "frame_id": r["frame_id"],
            "timestamp_ms": r["timestamp_ms"],
            "screen_name": r["screen_name"],
            "flow_step_label": r["flow_step_label"],
            "purpose": r["purpose"],
            "ux_notes": r["ux_notes"],
            "transcript_excerpt": r["transcript_excerpt"] or "",
        }
        for r in rows
    ]

    messages = build_synthesis_message(entries)
    # Each screen needs a flow_steps entry, a possible screens_gallery entry, and
    # contributes to ux_insights -- budget generously so a busy flow (many screens)
    # doesn't get its later fields (ux_insights, screens_gallery) truncated to empty.
    max_tokens = min(16000, 3000 + len(entries) * 200)
    result = await call_tool(
        system=SYNTHESIS_SYSTEM,
        messages=messages,
        tool_name="record_video_synthesis",
        tool_schema=video_synthesis_tool_schema(),
        usage_context=AIUsageContext(
            analysis_type="video",
            analysis_id=ctx.job_id,
            job_id=ctx.job_id,
            video_id=ctx.video_id,
            stage="synthesis",
        ),
        max_tokens=max_tokens,
        purpose="synthesis",
    )
    data = result.data

    async with db.write() as conn:
        await conn.execute(
            "INSERT INTO video_syntheses "
            "(id, video_id, flow_steps_json, ux_insights_json, screens_gallery_json, model_used, created_at) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(video_id) DO UPDATE SET "
            "flow_steps_json=excluded.flow_steps_json, ux_insights_json=excluded.ux_insights_json, "
            "screens_gallery_json=excluded.screens_gallery_json, model_used=excluded.model_used, "
            "created_at=excluded.created_at",
            (
                new_id("syn"), ctx.video_id,
                json.dumps(data.get("flow_steps", [])),
                json.dumps(data.get("ux_insights", [])),
                json.dumps(data.get("screens_gallery", [])),
                result.model, utcnow_iso(),
            ),
        )
