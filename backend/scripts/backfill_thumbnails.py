"""Backfill missing video thumbnails from frames already on disk.

The library card shows a skeleton until `videos.thumbnail_url` is set, and the
pipeline only sets it during download. A video ingested by another route -- a
pre-seeded file, a script that ran the frame stages directly -- therefore shows
an empty card even though its frames are present.

This derives a thumbnail from the video's first extracted frame, writes it to
`<media>/<video_id>/thumb.jpg` and points the row at it. Serving it from the
media mount means it works offline and shows the product screen rather than the
YouTube poster frame.

    python scripts/backfill_thumbnails.py --data-dir ./data
    python scripts/backfill_thumbnails.py --data-dir ./data --overwrite
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
_parser.add_argument("--data-dir", required=True, help="FlowScope DATA_DIR holding the DB and media")
_parser.add_argument("--overwrite", action="store_true", help="Replace existing thumbnails too")
_pre = _parser.parse_args()

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DATA_DIR"] = str(Path(_pre.data_dir).expanduser())

from PIL import Image  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import db  # noqa: E402
from app.storage import paths  # noqa: E402

THUMB_SIZE = (640, 360)


def make_thumbnail(frame_path: Path, out_path: Path) -> bool:
    try:
        with Image.open(frame_path) as img:
            img = img.convert("RGB")
            # Cover-crop to 16:9 so the card grid stays uniform.
            target_ratio = THUMB_SIZE[0] / THUMB_SIZE[1]
            w, h = img.size
            if w / h > target_ratio:
                new_w = int(h * target_ratio)
                img = img.crop(((w - new_w) // 2, 0, (w - new_w) // 2 + new_w, h))
            else:
                new_h = int(w / target_ratio)
                img = img.crop((0, (h - new_h) // 2, w, (h - new_h) // 2 + new_h))
            img = img.resize(THUMB_SIZE)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(out_path, format="JPEG", quality=85, optimize=True)
        return True
    except Exception:
        return False


async def main() -> int:
    await db.connect()
    where = "" if _pre.overwrite else "WHERE v.thumbnail_url IS NULL OR v.thumbnail_url = ''"
    rows = await db.fetchall(
        f"""SELECT v.id, v.title,
                   (SELECT f.file_path FROM frames f WHERE f.video_id = v.id
                    ORDER BY f.timestamp_ms LIMIT 1) AS first_frame
            FROM videos v {where}"""
    )
    print(f"{len(rows)} video(s) without a thumbnail\n")

    done = skipped = 0
    for row in rows:
        first = row["first_frame"]
        if not first:
            print(f"  skip  {str(row['title'])[:44]:<46} no frames")
            skipped += 1
            continue
        source = Path(first)
        if not source.exists():
            # Stored paths are absolute from the machine that produced them;
            # fall back to the portable portion below "media".
            parts = source.parts
            if "media" in parts:
                source = settings.media_path / Path(*parts[parts.index("media") + 1 :])
        if not source.exists():
            print(f"  skip  {str(row['title'])[:44]:<46} frame file missing")
            skipped += 1
            continue

        out = paths.video_dir(row["id"]) / "thumb.jpg"
        if not make_thumbnail(source, out):
            print(f"  skip  {str(row['title'])[:44]:<46} could not read frame")
            skipped += 1
            continue

        url = f"/media/{out.relative_to(settings.media_path).as_posix()}"
        async with db.write() as conn:
            await conn.execute("UPDATE videos SET thumbnail_url = ? WHERE id = ?", (url, row["id"]))
        print(f"  ok    {str(row['title'])[:44]:<46} {url}")
        done += 1

    print(f"\nthumbnails written: {done}   skipped: {skipped}")
    await db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
