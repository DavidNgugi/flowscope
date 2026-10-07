import uuid
from datetime import UTC, datetime
from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    FETCHING_CAPTIONS = "fetching_captions"
    TRANSCRIBING = "transcribing"
    DETECTING_SCENES = "detecting_scenes"
    EXTRACTING_FRAMES = "extracting_frames"
    DEDUPING_FRAMES = "deduping_frames"
    ALIGNING_TRANSCRIPT = "aligning_transcript"
    ANALYZING_FRAMES = "analyzing_frames"
    SYNTHESIZING = "synthesizing"
    DONE = "done"
    ERROR = "error"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.DONE, JobStatus.ERROR)


STAGE_ORDER: list[JobStatus] = [
    JobStatus.QUEUED,
    JobStatus.DOWNLOADING,
    JobStatus.FETCHING_CAPTIONS,
    JobStatus.TRANSCRIBING,
    JobStatus.DETECTING_SCENES,
    JobStatus.EXTRACTING_FRAMES,
    JobStatus.DEDUPING_FRAMES,
    JobStatus.ALIGNING_TRANSCRIPT,
    JobStatus.ANALYZING_FRAMES,
    JobStatus.SYNTHESIZING,
    JobStatus.DONE,
]


class TranscriptSource(StrEnum):
    OFFICIAL_CAPTION = "official_caption"
    AUTO_CAPTION = "auto_caption"
    WHISPER = "whisper"


def new_id(prefix: str = "") -> str:
    token = uuid.uuid4().hex[:20]
    return f"{prefix}_{token}" if prefix else token


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()
