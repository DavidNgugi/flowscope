"""Ingest one video URL from the command line.

Runs the same pipeline the API's submit endpoint runs -- download, captions,
frame extraction, dedupe, alignment, vision analysis, synthesis -- without
starting a server. Useful for a machine with no browser, for re-running one
video after a model change, and for verifying the pipeline end to end.

    python scripts/ingest_video.py --url "https://youtu.be/XXXX" --data-dir ./data
    python scripts/ingest_video.py --url ... --data-dir ./data --force

Writes into the given DATA_DIR, so an existing library is added to rather than
replaced. Re-running a URL that already exists reuses its row and, unless
--force is given, skips work that is already done.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
_parser.add_argument("--url", required=True, help="YouTube URL (or any yt-dlp-supported URL)")
_parser.add_argument("--data-dir", required=True, help="FlowScope DATA_DIR to write into")
_parser.add_argument("--force", action="store_true", help="Re-download and re-analyse even if present")
_pre = _parser.parse_args()

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DATA_DIR"] = str(Path(_pre.data_dir).expanduser())

from app.config import settings  # noqa: E402
from app.db import db  # noqa: E402
from app.jobs.manager import JobContext  # noqa: E402
from app.jobs.pipeline import run_pipeline  # noqa: E402
from app.jobs.stages.download import extract_youtube_id  # noqa: E402
from app.llm.registry import get_registry  # noqa: E402
from app.llm.usage import summarize_usage_rows  # noqa: E402
from app.models import new_id, utcnow_iso  # noqa: E402


async def main() -> int:
    await db.connect()
    registry = get_registry()
    print(f"data dir  : {settings.data_path}")
    print(f"client    : {settings.ytdlp_player_client or 'yt-dlp default'}")
    for purpose in ("vision", "synthesis"):
        try:
            print(f"{purpose:9} : {registry.resolve(purpose).describe()}")
        except Exception as exc:
            print(f"{purpose:9} : unavailable ({exc})")
    print()

    youtube_id = extract_youtube_id(_pre.url)
    row = await db.fetchone("SELECT id FROM videos WHERE youtube_id = ?", (youtube_id,))
    if row is not None and not _pre.force:
        video_id = row["id"]
        print(f"video {youtube_id} already present ({video_id})")
    else:
        video_id = row["id"] if row else new_id("vid")
        if row is None:
            async with db.write() as conn:
                await conn.execute(
                    "INSERT INTO videos (id, youtube_url, youtube_id, created_at) VALUES (?,?,?,?)",
                    (video_id, _pre.url, youtube_id, utcnow_iso()),
                )

    job_id = new_id("job")
    async with db.write() as conn:
        await conn.execute(
            "INSERT INTO jobs (id, video_id, status, created_at) VALUES (?,?,?,?)",
            (job_id, video_id, "queued", utcnow_iso()),
        )

    ctx = JobContext(job_id, video_id)
    try:
        await run_pipeline(ctx)
    except Exception as exc:  # noqa: BLE001 - report, then show what did land
        print(f"\npipeline stopped: {type(exc).__name__}: {exc}")
        await ctx.fail(f"{type(exc).__name__}: {exc}")

    frames = await db.fetchone("SELECT COUNT(*) AS n FROM frames WHERE video_id = ?", (video_id,))
    analyses = await db.fetchone(
        "SELECT COUNT(*) AS n FROM frame_analyses WHERE video_id = ?", (video_id,)
    )
    synthesis = await db.fetchone(
        "SELECT COUNT(*) AS n FROM video_syntheses WHERE video_id = ?", (video_id,)
    )
    usage = summarize_usage_rows([
        dict(r) for r in await db.fetchall(
            "SELECT * FROM ai_usage_records WHERE video_id = ?", (video_id,)
        )
    ])
    title = (await db.fetchone("SELECT title FROM videos WHERE id = ?", (video_id,)))["title"]
    print(f"\nvideo     : {title}")
    print(f"frames    : {frames['n']}   analysed: {analyses['n']}   synthesis: {synthesis['n']}")
    print(f"calls     : {usage['call_count']}  in={usage['input_tokens']} out={usage['output_tokens']}  "
          f"cost={'$%.4f' % usage['estimated_cost_usd'] if usage['estimated_cost_usd'] is not None else 'unknown'}")
    await db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
