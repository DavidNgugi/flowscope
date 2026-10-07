"""Cross-video comparison, computed on demand (not part of the per-video
pipeline) and cached by the sorted set of video_ids so repeat views don't
re-pay for an LLM call.
"""

import json

from app.db import db
from app.llm.client import call_tool
from app.llm.usage import AIUsageContext
from app.llm.prompts import COMPARISON_SYSTEM, build_comparison_message, comparison_tool_schema
from app.models import new_id, utcnow_iso


async def compare_videos(video_ids: list[str], force_refresh: bool = False) -> tuple[dict, str]:
    key_ids = sorted(video_ids)
    cache_key = json.dumps(key_ids)

    if not force_refresh:
        existing = await db.fetchone(
            "SELECT id, result_json FROM comparisons WHERE video_ids_json = ? "
            "ORDER BY created_at DESC LIMIT 1", (cache_key,)
        )
        if existing is not None:
            return json.loads(existing["result_json"]), existing["id"]

    syntheses = []
    for vid in key_ids:
        video_row = await db.fetchone("SELECT title FROM videos WHERE id = ?", (vid,))
        synthesis_row = await db.fetchone("SELECT * FROM video_syntheses WHERE video_id = ?", (vid,))
        if video_row is None or synthesis_row is None:
            raise ValueError(f"video {vid} has no completed synthesis yet")
        syntheses.append(
            {
                "video_id": vid,
                "title": video_row["title"],
                "flow_steps": json.loads(synthesis_row["flow_steps_json"] or "[]"),
                "ux_insights": json.loads(synthesis_row["ux_insights_json"] or "[]"),
            }
        )

    messages = build_comparison_message(syntheses)
    # Scale with total flow steps across videos so common_patterns/divergences
    # don't get truncated to nothing after a large stage_matrix (see synthesis.py).
    total_steps = sum(len(s["flow_steps"]) for s in syntheses)
    max_tokens = min(16000, 4000 + total_steps * 100)
    comparison_id = new_id("cmp")
    result = await call_tool(
        system=COMPARISON_SYSTEM,
        messages=messages,
        tool_name="record_comparison",
        tool_schema=comparison_tool_schema(),
        usage_context=AIUsageContext(
            analysis_type="comparison",
            analysis_id=comparison_id,
            stage="comparison",
        ),
        max_tokens=max_tokens,
        purpose="comparison",
    )
    data = result.data

    label = ", ".join(s["title"] or s["video_id"] for s in syntheses)
    async with db.write() as conn:
        await conn.execute(
            "INSERT INTO comparisons (id, video_ids_json, label, result_json, created_at) VALUES (?,?,?,?,?)",
            (comparison_id, cache_key, label, json.dumps(data), utcnow_iso()),
        )

    return data, comparison_id
