"""SQLite access layer.

Single aiosqlite connection shared across the app, opened once at startup and
reused for the process lifetime. aiosqlite serializes commands onto its own
background thread, so awaited calls from many coroutines are individually
safe -- but a multi-statement write (e.g. insert-then-update-counters) is not
atomic across interleaved coroutines unless we hold a lock around it. All
worker-thread code (ffmpeg/yt-dlp/whisper, run via executors) must NOT touch
this connection directly -- it belongs to the event loop. Threads compute
results and hand them back to async code, which performs the DB write.
"""

import asyncio
from contextlib import asynccontextmanager

import aiosqlite

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    youtube_url TEXT NOT NULL,
    youtube_id TEXT NOT NULL,
    title TEXT,
    channel TEXT,
    duration_seconds INTEGER,
    thumbnail_url TEXT,
    transcript_source TEXT,
    source_video_path TEXT,
    created_at TEXT NOT NULL,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_videos_youtube_id ON videos(youtube_id);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(id),
    status TEXT NOT NULL,
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    stage_detail TEXT,
    force_local_transcription INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_video_id ON jobs(video_id);

CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    ts TEXT NOT NULL,
    stage TEXT NOT NULL,
    message TEXT,
    progress_current INTEGER,
    progress_total INTEGER
);
CREATE INDEX IF NOT EXISTS idx_job_events_job_id ON job_events(job_id, id);

CREATE TABLE IF NOT EXISTS transcript_segments (
    id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(id),
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transcript_video_start ON transcript_segments(video_id, start_ms);

CREATE TABLE IF NOT EXISTS frames (
    id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(id),
    timestamp_ms INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    phash TEXT,
    scene_score REAL,
    width INTEGER,
    height INTEGER
);
CREATE INDEX IF NOT EXISTS idx_frames_video_id ON frames(video_id, timestamp_ms);

CREATE TABLE IF NOT EXISTS frame_analyses (
    id TEXT PRIMARY KEY,
    frame_id TEXT NOT NULL UNIQUE REFERENCES frames(id),
    video_id TEXT NOT NULL REFERENCES videos(id),
    screen_name TEXT,
    flow_step_label TEXT,
    purpose TEXT,
    ux_notes TEXT,
    ui_elements_json TEXT,
    raw_llm_json TEXT,
    transcript_excerpt TEXT,
    model_used TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_frame_analyses_video_id ON frame_analyses(video_id);

CREATE TABLE IF NOT EXISTS video_syntheses (
    id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL UNIQUE REFERENCES videos(id),
    flow_steps_json TEXT,
    ux_insights_json TEXT,
    screens_gallery_json TEXT,
    model_used TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comparisons (
    id TEXT PRIMARY KEY,
    video_ids_json TEXT NOT NULL,
    label TEXT,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_usage_records (
    id TEXT PRIMARY KEY,
    analysis_type TEXT NOT NULL,
    analysis_id TEXT NOT NULL,
    job_id TEXT,
    video_id TEXT,
    stage TEXT NOT NULL,
    operation_id TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    request_id TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
    input_cost_per_mtok REAL,
    output_cost_per_mtok REAL,
    cache_creation_cost_per_mtok REAL,
    cache_read_cost_per_mtok REAL,
    estimated_cost_usd REAL,
    service_tier TEXT,
    inference_geo TEXT,
    duration_ms INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_analysis ON ai_usage_records(analysis_type, analysis_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_usage_video ON ai_usage_records(video_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_usage_request ON ai_usage_records(request_id) WHERE request_id IS NOT NULL;
"""


class Database:
    def __init__(self) -> None:
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(settings.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database not connected"
        return self._conn

    async def fetchone(self, query: str, params: tuple = ()) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(query, params)
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def fetchall(self, query: str, params: tuple = ()) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(query, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return rows

    @asynccontextmanager
    async def write(self):
        """Serialize multi-statement writes so concurrent job workers can't interleave them."""
        async with self._write_lock:
            yield self.conn
            await self.conn.commit()

    async def execute(self, query: str, params: tuple = ()) -> None:
        async with self.write() as conn:
            await conn.execute(query, params)


db = Database()
