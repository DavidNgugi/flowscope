"""Stage: detect candidate screen-change timestamps and extract a JPEG per candidate.

Scene detection via ffmpeg's `select='gt(scene,T)'` filter finds visually
abrupt changes, but software demos are often low-motion (static chrome, only
inner content changes) so it can under-trigger. Uniform sampling is therefore
always merged in, capped so long videos do not explode the candidate count.

Scene-change timestamps often land on a transition or fast scroll. For each
candidate we inspect a short run of frames after the timestamp and keep the
sharpest one rather than blindly accepting the first frame.

Frame extraction runs many short-lived ffmpeg processes concurrently across
the bounded cpu_executor thread pool.
"""

import asyncio
import logging
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

from app.config import settings
from app.deps import run_cpu
from app.jobs.manager import JobContext
from app.models import JobStatus
from app.storage import paths

logger = logging.getLogger("flowscope.stages.scenes")

def _detect_candidates_sync(video_path: Path, threshold: float, scenes_txt: Path) -> list[float]:
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video_path),
            "-vf", f"select='gt(scene,{threshold})',metadata=print:file={scenes_txt}",
            "-vsync", "vfr", "-f", "null", "-",
        ],
        capture_output=True, timeout=1800,
    )
    if not scenes_txt.exists():
        return []
    times: list[float] = []
    for line in scenes_txt.read_text(errors="ignore").splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            times.append(float(m.group(1)))
    scenes_txt.unlink(missing_ok=True)
    return times


def _build_uniform_timestamps(duration_seconds: float, max_frames: int) -> list[float]:
    if not duration_seconds or duration_seconds <= 0:
        return [0.0]
    step = max(3.0, duration_seconds / max_frames)
    out: list[float] = []
    t = 0.0
    while t < duration_seconds:
        out.append(round(t, 2))
        t += step
    return out


def _merge_nearby_timestamps(timestamps: list[float], min_gap_seconds: float = 1.0) -> list[float]:
    merged: list[float] = []
    for timestamp in sorted(timestamps):
        if not merged or timestamp - merged[-1] >= min_gap_seconds:
            merged.append(timestamp)
    return merged


async def detect_scene_timestamps(ctx: JobContext, duration_seconds: float | None) -> list[float]:
    await ctx.report_progress(JobStatus.DETECTING_SCENES, message="Detecting scene changes")
    video_path = paths.source_video_path(ctx.video_id)
    scenes_txt = paths.video_dir(ctx.video_id) / "scenes.txt"

    candidates = await run_cpu(_detect_candidates_sync, video_path, settings.scene_detect_threshold, scenes_txt)
    duration = duration_seconds or 0.0
    desired_uniform_count = max(
        settings.min_frames_floor,
        int(duration / max(1.0, settings.uniform_sample_interval_seconds)) + 1,
    )
    uniform = _build_uniform_timestamps(
        duration, min(settings.max_uniform_candidates, desired_uniform_count)
    )
    timestamps = _merge_nearby_timestamps(
        [0.0, *(round(t, 2) for t in candidates), *uniform]
    )
    logger.info(
        "video %s: %d scene changes + uniform coverage -> %d candidates",
        ctx.video_id, len(candidates), len(timestamps),
    )

    return timestamps


def _sharpness_score(path: Path) -> float:
    with Image.open(path) as img:
        gray = img.convert("L")
        gray.thumbnail((640, 640))
        edges = gray.filter(ImageFilter.FIND_EDGES)
        if edges.width > 4 and edges.height > 4:
            edges = edges.crop((2, 2, edges.width - 2, edges.height - 2))
        return ImageStat.Stat(edges).var[0]


def _extract_frame_sync(video_path: Path, out_path: Path, timestamp_s: float) -> tuple[int, int] | None:
    probe_pattern = out_path.with_name(f"{out_path.stem}_probe_%02d.jpg")
    probe_paths: list[Path] = []
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", f"{timestamp_s:.3f}", "-i", str(video_path),
                "-t", f"{settings.stable_frame_probe_seconds:.2f}",
                "-vf", f"fps={max(1, settings.stable_frame_probe_fps)}",
                "-frames:v", str(max(2, int(settings.stable_frame_probe_seconds * settings.stable_frame_probe_fps))),
                "-q:v", "2", str(probe_pattern),
            ],
            capture_output=True, check=True, timeout=30,
        )
    except subprocess.CalledProcessError:
        return None

    probe_paths = sorted(out_path.parent.glob(f"{out_path.stem}_probe_*.jpg"))
    valid_probes = [path for path in probe_paths if path.stat().st_size > 0]
    if not valid_probes:
        return None
    try:
        sharpest = max(valid_probes, key=_sharpness_score)
        sharpest.replace(out_path)
        with Image.open(out_path) as img:
            return img.size
    finally:
        for probe_path in probe_paths:
            probe_path.unlink(missing_ok=True)


async def extract_candidate_frames(
    ctx: JobContext, timestamps: list[float]
) -> list[tuple[float, Path, int, int]]:
    await ctx.report_progress(
        JobStatus.EXTRACTING_FRAMES, current=0, total=len(timestamps),
        message=f"Extracting {len(timestamps)} candidate frames",
    )
    video_path = paths.source_video_path(ctx.video_id)
    candidates_dir = paths.video_dir(ctx.video_id) / "candidates"
    candidates_dir.mkdir(parents=True, exist_ok=True)

    results: list[tuple[float, Path, int, int]] = []
    results_lock = asyncio.Lock()
    completed = 0
    completed_lock = asyncio.Lock()

    async def extract_one(idx: int, ts: float) -> None:
        nonlocal completed
        out_path = candidates_dir / f"cand_{idx:04d}_{int(ts * 1000)}.jpg"
        size = await run_cpu(_extract_frame_sync, video_path, out_path, ts)
        if size is not None:
            async with results_lock:
                results.append((ts, out_path, size[0], size[1]))
        async with completed_lock:
            completed += 1
            n = completed
        if n % 5 == 0 or n == len(timestamps):
            await ctx.report_progress(
                JobStatus.EXTRACTING_FRAMES, current=n, total=len(timestamps),
                message=f"Extracted frame {n}/{len(timestamps)}",
            )

    await asyncio.gather(*(extract_one(i, t) for i, t in enumerate(timestamps)))
    results.sort(key=lambda r: r[0])
    return results
