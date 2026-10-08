import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import comparisons, health, videos
from app.config import settings
from app.db import db
from app.deps import check_ffmpeg, check_yt_dlp, shutdown_executors
from app.llm.registry import get_registry
from app.ws import routes as ws_routes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("flowscope")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()

    ffmpeg_ok, ffmpeg_version = check_ffmpeg()
    if not ffmpeg_ok:
        logger.warning("ffmpeg not found on PATH -- video processing will fail. Run `brew install ffmpeg`.")
    else:
        logger.info("ffmpeg OK (%s)", ffmpeg_version)

    if not check_yt_dlp():
        logger.warning("yt-dlp not importable -- check backend/requirements.txt install.")
    else:
        logger.info("yt-dlp OK")

    try:
        vision = get_registry().resolve("vision")
        logger.info("Screen analysis ready (%s)", vision.describe())
    except Exception as exc:
        logger.warning(
            "%s -- downloads/transcription still work, but new analysis jobs "
            "will be rejected at submit. Update backend/.env and restart.",
            exc,
        )

    from app.jobs.manager import job_manager

    await job_manager.start()

    yield

    await job_manager.stop()
    shutdown_executors()
    await db.close()


app = FastAPI(title="FlowScope", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(videos.router, prefix="/api")
app.include_router(comparisons.router, prefix="/api")
app.include_router(ws_routes.router)

app.mount("/media", StaticFiles(directory=str(settings.media_path)), name="media")
