"""In-process async job manager.

No Celery/Redis -- this is a single-user local tool. A fixed pool of worker
coroutines (MAX_CONCURRENT_JOBS) pulls job ids off an asyncio.Queue and runs
the pipeline for each concurrently, so submitting several videos at once
actually processes them in parallel rather than one-at-a-time. Job state
lives in SQLite (see app/db.py) so it survives process restarts; the queue
itself is in-memory, so on restart any job left in a non-terminal state is
marked as an interrupted error rather than silently resumed mid-stage --
the user just clicks Retry.
"""

import asyncio
import logging

from app.config import settings
from app.db import db
from app.models import JobStatus, utcnow_iso
from app.ws.manager import ws_manager

logger = logging.getLogger("flowscope.jobs")


class JobContext:
    def __init__(self, job_id: str, video_id: str) -> None:
        self.job_id = job_id
        self.video_id = video_id

    @classmethod
    async def load(cls, job_id: str) -> "JobContext":
        row = await db.fetchone("SELECT video_id FROM jobs WHERE id = ?", (job_id,))
        if row is None:
            raise ValueError(f"job {job_id} not found")
        return cls(job_id, row["video_id"])

    async def report_progress(
        self, status: JobStatus, current: int = 0, total: int = 0, message: str | None = None
    ) -> None:
        now = utcnow_iso()
        async with db.write() as conn:
            await conn.execute(
                "UPDATE jobs SET status=?, progress_current=?, progress_total=?, "
                "stage_detail=?, started_at=COALESCE(started_at, ?) WHERE id=?",
                (status.value, current, total, message, now, self.job_id),
            )
            await conn.execute(
                "INSERT INTO job_events (job_id, ts, stage, message, progress_current, progress_total) "
                "VALUES (?,?,?,?,?,?)",
                (self.job_id, now, status.value, message, current, total),
            )
        await ws_manager.broadcast(
            self.job_id,
            {
                "type": "progress",
                "job_id": self.job_id,
                "video_id": self.video_id,
                "status": status.value,
                "progress": {"current": current, "total": total},
                "message": message,
                "timestamp": now,
            },
        )

    async def complete(self, message: str = "Analysis complete") -> None:
        now = utcnow_iso()
        async with db.write() as conn:
            await conn.execute(
                "UPDATE jobs SET status=?, finished_at=?, stage_detail=? WHERE id=?",
                (JobStatus.DONE.value, now, message, self.job_id),
            )
            await conn.execute(
                "INSERT INTO job_events (job_id, ts, stage, message) VALUES (?,?,?,?)",
                (self.job_id, now, JobStatus.DONE.value, message),
            )
        await ws_manager.broadcast(
            self.job_id,
            {
                "type": "done",
                "job_id": self.job_id,
                "video_id": self.video_id,
                "status": JobStatus.DONE.value,
                "message": message,
                "timestamp": now,
            },
        )

    async def fail(self, error_message: str) -> None:
        now = utcnow_iso()
        async with db.write() as conn:
            await conn.execute(
                "UPDATE jobs SET status=?, error_message=?, finished_at=? WHERE id=?",
                (JobStatus.ERROR.value, error_message, now, self.job_id),
            )
            await conn.execute(
                "INSERT INTO job_events (job_id, ts, stage, message) VALUES (?,?,?,?)",
                (self.job_id, now, JobStatus.ERROR.value, error_message),
            )
        await ws_manager.broadcast(
            self.job_id,
            {
                "type": "error",
                "job_id": self.job_id,
                "video_id": self.video_id,
                "status": JobStatus.ERROR.value,
                "message": error_message,
                "timestamp": now,
            },
        )
        logger.error("job %s failed: %s", self.job_id, error_message)


class JobManager:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []

    async def start(self) -> None:
        await self._recover_interrupted_jobs()
        n = max(1, settings.max_concurrent_jobs)
        self._workers = [asyncio.create_task(self._worker_loop(i)) for i in range(n)]
        logger.info("job manager started with %d worker(s)", n)

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def enqueue(self, job_id: str) -> None:
        await self._queue.put(job_id)

    async def _worker_loop(self, worker_idx: int) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self._run_job(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("worker %d: job %s crashed unexpectedly", worker_idx, job_id)
            finally:
                self._queue.task_done()

    async def _run_job(self, job_id: str) -> None:
        from app.jobs.pipeline import run_pipeline

        ctx = await JobContext.load(job_id)
        try:
            await run_pipeline(ctx)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("job %s failed", job_id)
            await ctx.fail(str(exc))

    async def _recover_interrupted_jobs(self) -> None:
        rows = await db.fetchall(
            "SELECT id FROM jobs WHERE status NOT IN (?, ?)",
            (JobStatus.DONE.value, JobStatus.ERROR.value),
        )
        for row in rows:
            async with db.write() as conn:
                await conn.execute(
                    "UPDATE jobs SET status=?, error_message=? WHERE id=?",
                    (
                        JobStatus.ERROR.value,
                        "Interrupted by server restart -- click Retry to re-run.",
                        row["id"],
                    ),
                )
            logger.warning("job %s marked interrupted on startup", row["id"])


job_manager = JobManager()
