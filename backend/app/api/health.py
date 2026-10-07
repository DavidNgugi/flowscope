from fastapi import APIRouter

from app.config import settings
from app.deps import check_ffmpeg, check_yt_dlp
from app.llm.registry import get_registry
from app.schemas import HealthResponse

router = APIRouter()


def _resolved(purpose: str) -> str:
    """Model id for a purpose, without raising when nothing is configured."""
    try:
        return get_registry().resolve(purpose).profile.model
    except Exception:
        return ""


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    ffmpeg_available, ffmpeg_version = check_ffmpeg()
    return HealthResponse(
        ffmpeg_available=ffmpeg_available,
        ffmpeg_version=ffmpeg_version,
        yt_dlp_available=check_yt_dlp(),
        anthropic_key_present=bool(settings.anthropic_api_key),
        anthropic_model=settings.anthropic_model,
        llm_providers_available=get_registry().available(),
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        llm_vision_model=_resolved("vision"),
        llm_synthesis_model=_resolved("synthesis"),
        llm_comparison_model=_resolved("comparison"),
    )
