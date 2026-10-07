from pydantic import BaseModel, Field


# ---- API request/response ----


class SubmitVideosRequest(BaseModel):
    urls: list[str] = Field(min_length=1)
    force: bool = False
    force_local_transcription: bool = False


class SubmitVideosResponseItem(BaseModel):
    video_id: str
    job_id: str
    youtube_id: str
    status: str
    reused: bool = False


class HealthResponse(BaseModel):
    ffmpeg_available: bool
    ffmpeg_version: str | None
    yt_dlp_available: bool
    anthropic_key_present: bool
    anthropic_model: str
    # Multi-provider view: which providers have credentials, and the model
    # resolved for each purpose.
    llm_providers_available: list[str] = []
    llm_provider: str = ""
    llm_model: str = ""
    llm_vision_model: str = ""
    llm_synthesis_model: str = ""
    llm_comparison_model: str = ""


class JobSnapshot(BaseModel):
    job_id: str
    video_id: str
    status: str
    progress_current: int
    progress_total: int
    stage_detail: str | None
    error_message: str | None


# ---- LLM structured outputs ----


class UIElement(BaseModel):
    type: str = Field(description="e.g. button, input, nav item, modal, toggle")
    label: str | None = Field(default=None, description="Visible text/label if any")
    notes: str | None = Field(default=None, description="Why it matters / how it's used")


class FrameAnalysis(BaseModel):
    screen_name: str = Field(description="Short human name for this screen/state")
    flow_step_label: str = Field(description="Short label for this step in the overall flow")
    purpose: str = Field(description="What this screen is for")
    ux_notes: str = Field(description="Notable UX/design patterns visible or described")
    ui_elements: list[UIElement] = Field(default_factory=list)


class GalleryEntry(BaseModel):
    frame_id: str
    flow_stage: str


class FlowStep(BaseModel):
    step_index: int
    screen_name: str
    frame_id: str
    description: str


class VideoSynthesis(BaseModel):
    flow_steps: list[FlowStep]
    ux_insights: list[str]
    screens_gallery: list[GalleryEntry]


class ComparisonResult(BaseModel):
    common_patterns: list[str]
    divergences: list[str]
    stage_matrix: list[dict]
