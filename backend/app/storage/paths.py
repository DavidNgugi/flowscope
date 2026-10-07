from pathlib import Path

from app.config import settings


def video_dir(video_id: str) -> Path:
    d = settings.media_path / video_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def frames_dir(video_id: str) -> Path:
    d = video_dir(video_id) / "frames"
    d.mkdir(parents=True, exist_ok=True)
    return d


def source_video_path(video_id: str) -> Path:
    return video_dir(video_id) / "source.mp4"


def audio_path(video_id: str) -> Path:
    return video_dir(video_id) / "audio.wav"


def captions_path(video_id: str) -> Path:
    return video_dir(video_id) / "captions.vtt"


def frame_file(video_id: str, idx: int, timestamp_ms: int) -> Path:
    return frames_dir(video_id) / f"frame_{idx:04d}_{timestamp_ms}.jpg"
