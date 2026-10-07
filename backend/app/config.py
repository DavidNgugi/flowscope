import os
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- LLM selection -------------------------------------------------
    # A purpose-based selection: `llm_provider`/`llm_model` are the default,
    # and vision/synthesis/comparison can each override. Any OpenAI-compatible
    # vendor works, so a provider with credit can always be swapped in.
    llm_provider: str = "anthropic"
    llm_model: str = ""
    llm_vision_provider: str = ""
    llm_vision_model: str = ""
    llm_synthesis_provider: str = ""
    llm_synthesis_model: str = ""
    llm_comparison_provider: str = ""
    llm_comparison_model: str = ""
    llm_image_detail: str = "high"
    llm_timeout_seconds: float = 180.0
    # Applies only to reasoning models (gpt-5.x, o-series). Empty omits the
    # parameter entirely. Reasoning tokens are billed against the completion
    # budget, so a lower effort leaves more room for the tool call.
    llm_reasoning_effort: str = "low"

    # ---- provider credentials -----------------------------------------
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    openai_api_key: str = ""
    openai_base_url: str = ""
    deepseek_api_key: str = ""
    deepseek_base_url: str = ""
    openai_compatible_api_key: str = ""
    openai_compatible_base_url: str = ""

    whisper_model_size: str = "small"

    data_dir: str = "./data"
    video_max_height: int = 720

    # YouTube increasingly requires an authenticated browser session for some
    # videos/IP addresses. Prefer a cookie file in containers; browser cookie
    # extraction is convenient for local development.
    ytdlp_cookie_file: str = ""
    ytdlp_cookies_from_browser: str = ""
    # YouTube's default clients return SABR-only formats that fail with 403;
    # "android" is the client that still serves a downloadable stream.
    ytdlp_player_client: str = "android"
    # Comma-separated yt-dlp JS runtimes (e.g. "deno"); the Docker image has deno.
    ytdlp_js_runtimes: str = ""

    max_concurrent_jobs: int = 3
    max_concurrent_llm_calls: int = 6
    cpu_worker_threads: int = 4
    io_worker_threads: int = 4

    scene_detect_threshold: float = 0.30
    frame_dedupe_hamming_threshold: int = 10
    min_frames_floor: int = 8
    uniform_sample_interval_seconds: float = 12.0
    max_uniform_candidates: int = 300
    stable_frame_probe_seconds: float = 1.5
    stable_frame_probe_fps: int = 4

    cors_origins: list[str] = ["http://localhost:5173"]

    def model_post_init(self, __context: Any) -> None:
        # Backwards compatibility: an existing .env that only sets
        # ANTHROPIC_MODEL keeps working without a provider/model rewrite.
        if not self.llm_model:
            self.llm_model = self.anthropic_model if self.llm_provider == "anthropic" else ""

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def media_path(self) -> Path:
        p = self.data_path / "media"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        return self.data_path / "db.sqlite3"


settings = Settings()

# faster-whisper / ctranslate2 spins up its own thread pool; keep it from
# oversubscribing when several transcription jobs happen to run concurrently.
os.environ.setdefault("OMP_NUM_THREADS", str(max(1, settings.cpu_worker_threads)))
