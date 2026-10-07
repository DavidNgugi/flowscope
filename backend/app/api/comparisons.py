from fastapi import APIRouter, HTTPException, Query

from app.jobs.stages.comparison import compare_videos
from app.llm.usage import usage_for_analysis

router = APIRouter()


@router.get("/comparisons")
async def get_comparison(video_ids: str = Query(..., description="comma-separated video ids"), force_refresh: bool = False) -> dict:
    ids = [v.strip() for v in video_ids.split(",") if v.strip()]
    if len(ids) < 2:
        raise HTTPException(status_code=400, detail="need at least 2 video_ids to compare")
    try:
        result, comparison_id = await compare_videos(ids, force_refresh=force_refresh)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {
        "video_ids": sorted(ids),
        "result": result,
        "ai_usage": await usage_for_analysis("comparison", comparison_id),
    }
