"""Stage: perceptual-hash dedup of extracted candidate frames.

Hashes are computed in parallel across the cpu_executor pool (embarrassingly
parallel, no ordering dependency). The keep/discard decision itself is a
cheap, purely sequential walk in timestamp order -- a frame is kept if its
Hamming distance to the *last kept* frame's hash exceeds the threshold, so a
revisited screen later in the video becomes a new flow step rather than
being silently merged with its first appearance.
"""

import asyncio
import logging
from pathlib import Path

import imagehash
from PIL import Image

from app.config import settings
from app.db import db
from app.deps import run_cpu
from app.jobs.manager import JobContext
from app.models import JobStatus, new_id
from app.storage import paths

logger = logging.getLogger("flowscope.stages.dedupe")


def _phash_sync(path: Path) -> str:
    with Image.open(path) as img:
        return str(imagehash.phash(img, hash_size=16))


async def dedupe_frames(
    ctx: JobContext, candidates: list[tuple[float, Path, int, int]]
) -> list[dict]:
    await ctx.report_progress(
        JobStatus.DEDUPING_FRAMES, current=0, total=len(candidates),
        message="Computing perceptual hashes",
    )

    hashes = await asyncio.gather(*(run_cpu(_phash_sync, c[1]) for c in candidates))

    await ctx.report_progress(
        JobStatus.DEDUPING_FRAMES, current=len(candidates), total=len(candidates),
        message="Deduplicating near-identical frames",
    )

    kept: list[dict] = []
    last_hash: imagehash.ImageHash | None = None
    for (ts, cand_path, width, height), hash_str in zip(candidates, hashes):
        phash = imagehash.hex_to_hash(hash_str)
        is_new = last_hash is None or (phash - last_hash) > settings.frame_dedupe_hamming_threshold
        if is_new:
            timestamp_ms = int(ts * 1000)
            final_path = paths.frame_file(ctx.video_id, len(kept), timestamp_ms)
            cand_path.rename(final_path)
            kept.append(
                {
                    "timestamp_ms": timestamp_ms,
                    "file_path": str(final_path),
                    "phash": hash_str,
                    "width": width,
                    "height": height,
                }
            )
            last_hash = phash
        else:
            cand_path.unlink(missing_ok=True)

    async with db.write() as conn:
        for frame in kept:
            frame_id = new_id("frm")
            frame["id"] = frame_id
            await conn.execute(
                "INSERT INTO frames (id, video_id, timestamp_ms, file_path, phash, width, height) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    frame_id, ctx.video_id, frame["timestamp_ms"], frame["file_path"],
                    frame["phash"], frame["width"], frame["height"],
                ),
            )

    candidates_dir = paths.video_dir(ctx.video_id) / "candidates"
    if candidates_dir.exists():
        for leftover in candidates_dir.iterdir():
            leftover.unlink(missing_ok=True)
        candidates_dir.rmdir()

    logger.info(
        "video %s: %d candidates -> %d distinct frames after dedup",
        ctx.video_id, len(candidates), len(kept),
    )
    return kept
