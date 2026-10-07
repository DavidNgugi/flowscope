"""Stage: resolve video metadata, decide caption strategy, download the video.

Runs on the io_executor (network-bound). Two yt-dlp passes:
  1. Metadata-only (download=False) to see what subtitle tracks exist, so we
     can decide official-caption vs auto-caption vs "no captions -> Whisper"
     BEFORE spending bandwidth, and to grab title/channel/duration/thumbnail.
  2. Actual download of the video (and the chosen subtitle track, if any) in
     one pass.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

from app.config import settings
from app.db import db
from app.deps import run_io
from app.jobs.manager import JobContext
from app.models import JobStatus, TranscriptSource
from app.storage import paths

logger = logging.getLogger("flowscope.stages.download")

PREFERRED_LANGS = ["en", "en-US", "en-GB", "en-orig"]


@dataclass
class DownloadResult:
    youtube_id: str
    title: str | None
    channel: str | None
    duration_seconds: int | None
    thumbnail_url: str | None
    source_video_path: Path
    caption_source: TranscriptSource | None  # None if no captions available
    caption_lang: str | None
    caption_file: Path | None


def extract_youtube_id(url: str) -> str:
    match = re.search(r"(?:v=|youtu\.be/|embed/)([A-Za-z0-9_-]{11})", url)
    if match:
        return match.group(1)
    return url.strip().split("/")[-1][:11]


def _pick_caption_lang(langs: dict) -> str | None:
    for lang in PREFERRED_LANGS:
        if lang in langs:
            return lang
    return None


def _common_ydl_opts() -> dict:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}

    # YouTube hands its default web/visionOS clients SABR-only formats whose
    # URLs the downloader cannot fetch (HTTP 403), so no video bytes arrive.
    # The android client still returns a progressive format -- at the cost of
    # resolution. Set YTDLP_PLAYER_CLIENT=default (or another client) to
    # override, and see yt-dlp's youtube extractor docs for the trade-offs.
    if settings.ytdlp_player_client:
        opts["extractor_args"] = {
            "youtube": {"player_client": [settings.ytdlp_player_client]}
        }

    if settings.ytdlp_js_runtimes:
        # Lets yt-dlp solve YouTube's signature challenges; the Docker image
        # ships deno for exactly this.
        opts["js_runtimes"] = {
            name.strip(): None
            for name in settings.ytdlp_js_runtimes.split(",")
            if name.strip()
        }

    if settings.ytdlp_cookie_file:
        cookie_file = Path(settings.ytdlp_cookie_file).expanduser()
        if not cookie_file.is_file():
            raise RuntimeError(f"YTDLP_COOKIE_FILE does not exist: {cookie_file}")
        opts["cookiefile"] = str(cookie_file)

    if settings.ytdlp_cookies_from_browser:
        if settings.ytdlp_cookie_file:
            raise RuntimeError(
                "Set only one of YTDLP_COOKIE_FILE or YTDLP_COOKIES_FROM_BROWSER"
            )
        opts["cookiesfrombrowser"] = (settings.ytdlp_cookies_from_browser, None, None, None)

    return opts


def _probe_sync(url: str) -> dict:
    opts = {**_common_ydl_opts(), "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _download_sync(url: str, video_dir: Path, max_height: int, sub_lang: str | None, want_auto: bool) -> dict:
    outtmpl = str(video_dir / "source.%(ext)s")
    opts = {
        **_common_ydl_opts(),
        "format": f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best",
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "restrictfilenames": True,
    }
    if sub_lang:
        opts["writesubtitles"] = not want_auto
        opts["writeautomaticsub"] = want_auto
        opts["subtitleslangs"] = [sub_lang]
        opts["subtitlesformat"] = "vtt"
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)


async def download_video(ctx: JobContext, youtube_url: str) -> DownloadResult:
    await ctx.report_progress(JobStatus.DOWNLOADING, message="Fetching video metadata")

    info = await run_io(_probe_sync, youtube_url)
    youtube_id = info.get("id") or extract_youtube_id(youtube_url)

    manual_langs = info.get("subtitles") or {}
    auto_langs = info.get("automatic_captions") or {}

    caption_source: TranscriptSource | None = None
    caption_lang: str | None = None
    want_auto = False

    lang = _pick_caption_lang(manual_langs)
    if lang:
        caption_source = TranscriptSource.OFFICIAL_CAPTION
        caption_lang = lang
    else:
        lang = _pick_caption_lang(auto_langs)
        if lang:
            caption_source = TranscriptSource.AUTO_CAPTION
            caption_lang = lang
            want_auto = True

    video_dir = paths.video_dir(ctx.video_id)
    out_path = paths.source_video_path(ctx.video_id)

    await ctx.report_progress(
        JobStatus.DOWNLOADING,
        message=f"Downloading video ({'captions: ' + caption_source.value if caption_source else 'no captions found'})",
    )

    dl_info = await run_io(
        _download_sync, youtube_url, video_dir, settings.video_max_height, caption_lang, want_auto
    )

    caption_file: Path | None = None
    if caption_lang:
        candidates = sorted(video_dir.glob(f"source.{caption_lang}.vtt"))
        if not candidates:
            candidates = sorted(video_dir.glob("source.*.vtt"))
        caption_file = candidates[0] if candidates else None
        if caption_file is None:
            caption_source = None
            caption_lang = None

    result = DownloadResult(
        youtube_id=youtube_id,
        title=dl_info.get("title"),
        channel=dl_info.get("channel") or dl_info.get("uploader"),
        duration_seconds=dl_info.get("duration"),
        thumbnail_url=dl_info.get("thumbnail"),
        source_video_path=out_path,
        caption_source=caption_source,
        caption_lang=caption_lang,
        caption_file=caption_file,
    )

    async with db.write() as conn:
        await conn.execute(
            "UPDATE videos SET title=?, channel=?, duration_seconds=?, thumbnail_url=?, "
            "source_video_path=? WHERE id=?",
            (
                result.title,
                result.channel,
                result.duration_seconds,
                result.thumbnail_url,
                str(result.source_video_path),
                ctx.video_id,
            ),
        )

    return result
