"""Startup dependency checks and bounded thread-pool executors.

Two separate pools, sized for the kind of blocking work they run:
  - cpu_executor: ffmpeg scene-detect/frame-extract, faster-whisper inference.
    Bounded to CPU_WORKER_THREADS so CPU-bound stages across concurrently
    running jobs don't oversubscribe the machine.
  - io_executor: yt-dlp downloads. Mostly network-wait, so a separate pool
    lets downloads proceed while CPU-bound stages of other jobs are busy,
    without either starving the other.

Anything run in these executors must be plain, self-contained blocking code
with no access to the aiosqlite connection (see app/db.py) -- it belongs to
the event loop thread only.
"""

import asyncio
import functools
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

from app.config import settings

T = TypeVar("T")

cpu_executor = ThreadPoolExecutor(
    max_workers=settings.cpu_worker_threads, thread_name_prefix="flowscope-cpu"
)
io_executor = ThreadPoolExecutor(
    max_workers=settings.io_worker_threads, thread_name_prefix="flowscope-io"
)


async def run_cpu(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(cpu_executor, functools.partial(func, *args, **kwargs))


async def run_io(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(io_executor, functools.partial(func, *args, **kwargs))


def check_ffmpeg() -> tuple[bool, str | None]:
    path = shutil.which("ffmpeg")
    if not path:
        return False, None
    try:
        out = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, timeout=5, check=True
        )
        first_line = out.stdout.splitlines()[0] if out.stdout else None
        return True, first_line
    except Exception:
        return True, None


def check_yt_dlp() -> bool:
    try:
        import yt_dlp  # noqa: F401

        return True
    except ImportError:
        return False


def shutdown_executors() -> None:
    cpu_executor.shutdown(wait=False, cancel_futures=True)
    io_executor.shutdown(wait=False, cancel_futures=True)
