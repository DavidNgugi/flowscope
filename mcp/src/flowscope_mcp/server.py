"""Tool, resource and prompt definitions for the FlowScope MCP server.

Design notes that are load-bearing:

* **Tool names are prefixed ``flowscope_``.** MCP clients merge tools from every
  connected server into one namespace, so an unprefixed ``get_report`` would
  collide the moment a second server is installed.
* **Read tools and write tools are separated** so annotations can be honest.
  Every read tool carries ``read_only_hint``; only the four tools that mutate
  backend state or spend money are marked otherwise.
* **Expected failures are returned, not raised.** Per the MCP guidance, a
  missing video or an unreachable backend is a normal outcome that the model
  should see and reason about, so these come back as ``is_error`` results with
  an actionable message. Raising is reserved for genuinely unexpected faults.
* **Reports return structured content plus a rendered Markdown view.** The
  structured payload is what a program consumes; the Markdown is what the model
  reads without burning tokens on JSON punctuation.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx2
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from .client import FlowScopeClient, FlowScopeError, SubmittedVideo
from .settings import Settings, load_settings

SERVER_INSTRUCTIONS = """\
FlowScope analyses YouTube product-demo videos and produces a structured UX
breakdown: deduplicated screenshots of each distinct screen, a transcript
aligned to those screens, per-screen design notes, and a synthesised step-by-
step flow with UX insights. It can also compare several videos side by side.

Use it when someone asks how a product's onboarding, signup, checkout, or any
recorded flow works, or asks to compare the UX of two or more products.

The analysis pipeline is slow (a video is downloaded, transcribed, scene-
detected and then analysed screen by screen) and the vision steps cost money,
so follow this order:

1. `flowscope_health_check` -- confirm the backend is up and ffmpeg, yt-dlp and
   an LLM provider key are present. Do this before submitting anything.
2. `flowscope_list_videos` -- analyses are cached and reused by YouTube video
   id. If the video is already there, go straight to the report; nothing is
   re-downloaded and nothing is re-billed.
3. `flowscope_analyze_video` -- start a new analysis. It waits by default and
   returns the finished report. If it returns `status: "running"` instead, the
   video needs longer than the wait budget: poll `flowscope_job_status` with the
   returned `video_id`, and do not resubmit the URL.
4. `flowscope_get_report` -- read a finished analysis.

Answer UX questions from the report's synthesis and frame analyses rather than
re-deriving them from the raw transcript: the synthesis is the considered
output, the transcript is only the evidence behind it.
"""


# --------------------------------------------------------------------------
# Structured output models
#
# These become each tool's ``outputSchema``. Only fields that are always
# present are required; everything optional has a default so a partially
# analysed video still validates.
# --------------------------------------------------------------------------


class HealthReport(BaseModel):
    """Readiness of the FlowScope backend and its external dependencies."""

    ok: bool = Field(description="True when the backend answered and can analyse videos.")
    backend_url: str = Field(description="Base URL this server is talking to.")
    ffmpeg_available: bool = Field(description="ffmpeg present; required for frame extraction.")
    ffmpeg_version: str | None = Field(default=None, description="Detected ffmpeg version.")
    yt_dlp_available: bool = Field(description="yt-dlp importable; required to download videos.")
    llm_providers_available: list[str] = Field(
        default_factory=list, description="Providers with credentials, e.g. ['openai']."
    )
    llm_provider: str = Field(default="", description="Default provider.")
    llm_vision_model: str = Field(default="", description="Model used for the frame (vision) stage.")
    llm_synthesis_model: str = Field(default="", description="Model used to synthesise the flow.")
    llm_comparison_model: str = Field(default="", description="Model used for comparisons.")
    blocking_problems: list[str] = Field(
        default_factory=list,
        description="Human-readable list of reasons analysis would fail; empty when ok is true.",
    )
    advice: str = Field(default="", description="What to do about blocking_problems, if any.")


class VideoSummary(BaseModel):
    """One analysed (or in-progress) video."""

    video_id: str = Field(description="FlowScope id; pass this to the other tools.")
    youtube_id: str = Field(description="YouTube video id.")
    youtube_url: str = Field(description="Original URL submitted.")
    title: str | None = Field(default=None, description="Video title once downloaded.")
    channel: str | None = Field(default=None)
    # yt-dlp reports a duration that is an int for some videos and a float
    # for others (e.g. 119.211247). Declaring `int` makes Pydantic reject the
    # float case outright, which failed every listing that contained one.
    duration_seconds: float | None = Field(default=None)
    status: str | None = Field(default=None, description="Latest job status, e.g. 'done', 'error'.")
    transcript_source: str | None = Field(
        default=None, description="'official_caption', 'auto_caption' or 'whisper'."
    )
    has_report: bool = Field(default=False, description="True when a synthesis exists and is readable.")
    error_message: str | None = Field(default=None, description="Why the last job failed, if it did.")


class VideoListReport(BaseModel):
    """Result of listing videos."""

    count: int
    videos: list[VideoSummary]


class JobStatusReport(BaseModel):
    """Progress of one analysis job."""

    video_id: str
    job_id: str | None = Field(default=None)
    status: str = Field(description="queued|downloading|...|synthesizing|done|error")
    stage_label: str = Field(description="Human-readable version of status.")
    progress_current: int = 0
    progress_total: int = 0
    percent_complete: float | None = Field(default=None, description="Null when the total is unknown.")
    stage_detail: str | None = Field(default=None)
    error_message: str | None = Field(default=None)
    is_terminal: bool = Field(description="True when the job finished, successfully or not.")
    next_action: str = Field(description="What the caller should do next.")


class FrameFinding(BaseModel):
    """One deduplicated screen and what the vision model made of it."""

    index: int = Field(description="1-based position in the flow.")
    timestamp_seconds: float
    timestamp: str = Field(description="mm:ss into the video.")
    screen_name: str | None = Field(default=None)
    flow_step_label: str | None = Field(default=None)
    purpose: str | None = Field(default=None, description="What this screen is for.")
    ux_notes: str | None = Field(default=None, description="Notable design/UX patterns.")
    ui_elements: list[dict[str, Any]] = Field(
        default_factory=list, description="Controls visible on the screen, e.g. buttons and inputs."
    )
    narration_excerpt: str | None = Field(default=None, description="Transcript around this screen.")
    frame_id: str | None = Field(default=None)
    image_url: str | None = Field(default=None, description="Relative /media/ URL for the screenshot.")


class FlowStep(BaseModel):
    """One step of the reconstructed user flow."""

    step_index: int
    screen_name: str
    description: str
    frame_id: str | None = Field(default=None)


class ReportResult(BaseModel):
    """A complete analysis of one video, or the reason it could not be read.

    A single shape is used for both success and failure on purpose: an error is
    reported with ``ok=False`` and ``message`` rather than by returning a
    different type. A union return type would make the SDK wrap the payload in a
    ``{"result": ...}`` envelope, which is a worse contract for callers.
    """

    ok: bool = Field(default=True, description="False when the report could not be read.")
    message: str = Field(default="", description="Why the report is missing, when ok is false.")
    video_id: str
    title: str | None = Field(default=None)
    channel: str | None = Field(default=None)
    youtube_url: str = Field(default="")
    # yt-dlp reports a duration that is an int for some videos and a float
    # for others (e.g. 119.211247). Declaring `int` makes Pydantic reject the
    # float case outright, which failed every listing that contained one.
    duration_seconds: float | None = Field(default=None)
    transcript_source: str | None = Field(default=None)
    status: str | None = Field(default=None, description="Latest job status.")
    screen_count: int = Field(default=0, description="Number of distinct screens analysed.")
    flow_steps: list[FlowStep] = Field(default_factory=list, description="Ordered user flow.")
    ux_insights: list[str] = Field(default_factory=list, description="Synthesised UX observations.")
    frames: list[FrameFinding] = Field(
        default_factory=list, description="Per-screen findings, in video order."
    )
    transcript_excerpt: str | None = Field(
        default=None, description="Transcript text, present only when include_transcript was true."
    )
    report_markdown: str = Field(default="", description="The whole report rendered as Markdown.")
    truncated: bool = Field(
        default=False, description="True when frames were dropped to respect max_frames."
    )
    note: str = Field(default="", description="Caveats about this report, if any.")


class AnalysisStarted(BaseModel):
    """Outcome of submitting one or more videos for analysis."""

    status: str = Field(description="'done', 'reused', 'running' or 'error'.")
    videos: list[JobStatusReport] = Field(default_factory=list)
    reports: list[ReportResult] = Field(
        default_factory=list, description="Filled in only when every video finished within the wait budget."
    )
    reused_count: int = 0
    started_count: int = 0
    message: str = Field(description="Plain-language summary of what happened.")


class ComparisonResult(BaseModel):
    """Cross-video UX comparison, or the reason it could not be produced."""

    ok: bool = Field(default=True, description="False when the comparison failed.")
    message: str = Field(default="", description="Why the comparison failed, when ok is false.")
    video_ids: list[str] = Field(default_factory=list)
    common_patterns: list[str] = Field(default_factory=list, description="What the products share.")
    divergences: list[str] = Field(default_factory=list, description="Where they differ.")
    stage_matrix: list[dict[str, Any]] = Field(
        default_factory=list, description="Per-stage, per-product breakdown."
    )
    estimated_cost_usd: float | None = Field(default=None)
    report_markdown: str = Field(default="", description="The comparison rendered as Markdown.")


class MutationResult(BaseModel):
    """Outcome of a state-changing operation."""

    ok: bool
    action: str
    video_id: str
    job_id: str | None = Field(default=None)
    status: str | None = Field(default=None)
    message: str


# --------------------------------------------------------------------------
# Annotation presets
#
# Explicit rather than relying on constructor defaults: a wrong hint is a
# safety bug, because clients use these to decide what to auto-approve.
# --------------------------------------------------------------------------

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

# Reads that leave the machine (nothing here does, but the health probe and
# report reads hit a local service; kept False deliberately).
READ_ONLY_OPEN = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)

# Downloads a video and spends money on LLM calls: not read-only, not
# idempotent (it creates a job), but it destroys nothing.
COSTLY_CREATE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)

# Retrying is repeatable: re-running it on an already-queued job is a no-op.
IDEMPOTENT_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)

# Removes stored analysis and optionally the downloaded media.
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=True,
)

STAGE_LABELS = {
    "queued": "Queued",
    "downloading": "Downloading the video",
    "fetching_captions": "Checking for existing captions",
    "transcribing": "Transcribing audio (Whisper)",
    "detecting_scenes": "Detecting scene changes",
    "extracting_frames": "Extracting frames",
    "deduping_frames": "Removing duplicate frames",
    "aligning_transcript": "Aligning transcript to screens",
    "analyzing_frames": "Analysing each screen (vision model)",
    "synthesizing": "Synthesising the flow and UX insights",
    "done": "Done",
    "error": "Failed",
}

TERMINAL_STATUSES = {"done", "error"}


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------


def _mmss(ms: int | float | None) -> str:
    if ms is None:
        return "--:--"
    total_seconds = int(ms // 1000)
    return f"{total_seconds // 60:02d}:{total_seconds % 60:02d}"


def _percent(current: int, total: int) -> float | None:
    if not total:
        return None
    return round(min(100.0, current / total * 100.0), 1)


def _flatten_frame(frame: dict[str, Any], index: int) -> FrameFinding:
    """Turn the backend's nested frame+analysis shape into one flat record."""
    analysis = frame.get("analysis") or {}
    return FrameFinding(
        index=index,
        timestamp_seconds=round((frame.get("timestamp_ms") or 0) / 1000.0, 1),
        timestamp=_mmss(frame.get("timestamp_ms")),
        screen_name=analysis.get("screen_name"),
        flow_step_label=analysis.get("flow_step_label"),
        purpose=analysis.get("purpose"),
        ux_notes=analysis.get("ux_notes"),
        ui_elements=analysis.get("ui_elements") or [],
        narration_excerpt=(frame.get("transcript_excerpt") or analysis.get("transcript_excerpt") or None),
        frame_id=frame.get("id"),
        image_url=frame.get("url"),
    )


def _status_report(video_id: str, detail: dict[str, Any]) -> JobStatusReport:
    """Build a job status report from a GET /videos/{id} payload."""
    job = detail.get("job") or {}
    status = job.get("status") or "unknown"
    current = job.get("progress_current") or 0
    total = job.get("progress_total") or 0
    is_terminal = status in TERMINAL_STATUSES

    if status == "done":
        next_action = "Call flowscope_get_report to read the finished analysis."
    elif status == "error":
        next_action = (
            "The job failed. Read error_message, fix the cause, then call "
            "flowscope_retry_video. Re-running is cheap for stages that already completed."
        )
    elif status == "unknown":
        next_action = "No job has been recorded for this video; submit it with flowscope_analyze_video."
    else:
        next_action = (
            "Still running. Poll flowscope_job_status again in 20-30 seconds; "
            "do not resubmit the same URL."
        )

    return JobStatusReport(
        video_id=video_id,
        job_id=job.get("id"),
        status=status,
        stage_label=STAGE_LABELS.get(status, status),
        progress_current=current,
        progress_total=total,
        percent_complete=_percent(current, total),
        stage_detail=job.get("stage_detail"),
        error_message=job.get("error_message"),
        is_terminal=is_terminal,
        next_action=next_action,
    )


def _fail(exc: Exception) -> ToolError:
    """Turn a backend failure into an error the model can act on.

    Raised rather than returned: a returned error message has ``is_error=False``
    and therefore reads to the model as a successful answer. Only failures that
    leave nothing useful to report are routed here; a report that is merely
    incomplete keeps its partial data and sets ``ok=False`` instead.

    The exception text is passed through by design -- it already names the
    concrete cause (unreachable backend, HTTP 404, missing API key), which is
    exactly what the model needs to fix it.
    """
    return ToolError(str(exc))


def _render_report_markdown(detail: dict[str, Any], frames: list[FrameFinding]) -> str:
    """Render a report the model can read cheaply, with the JSON as backup."""
    video = detail.get("video") or {}
    job = detail.get("job") or {}
    synthesis = detail.get("synthesis") or {}

    lines: list[str] = []
    lines.append(f"# {video.get('title') or video.get('youtube_id') or video.get('id')}")
    meta = []
    if video.get("channel"):
        meta.append(f"Channel: {video['channel']}")
    if video.get("duration_seconds"):
        meta.append(f"Duration: {_mmss((video['duration_seconds'] or 0) * 1000)}")
    if video.get("transcript_source"):
        meta.append(f"Transcript: {video['transcript_source'].replace('_', ' ')}")
    if job.get("status"):
        meta.append(f"Status: {job['status']}")
    if meta:
        lines.append("  \n".join(meta))
    lines.append("")

    insights = synthesis.get("ux_insights") or []
    if insights:
        lines.append("## UX insights")
        lines.extend(f"- {item}" for item in insights)
        lines.append("")

    steps = synthesis.get("flow_steps") or []
    if steps:
        lines.append("## User flow")
        for step in sorted(steps, key=lambda s: s.get("step_index", 0)):
            lines.append(f"{step.get('step_index', '?')}. **{step.get('screen_name', 'Untitled screen')}**")
            if step.get("description"):
                lines.append(f"   {step['description']}")
        lines.append("")

    if frames:
        lines.append("## Screens")
        for item in frames:
            lines.append(f"### {item.index}. {item.screen_name or 'Unnamed screen'} — {item.timestamp}")
            if item.flow_step_label:
                lines.append(f"*Flow step:* {item.flow_step_label}")
            if item.purpose:
                lines.append(f"*Purpose:* {item.purpose}")
            if item.ux_notes:
                lines.append(f"*UX notes:* {item.ux_notes}")
            if item.ui_elements:
                rendered = ", ".join(
                    filter(
                        None,
                        (
                            f"{el.get('type', 'element')}"
                            + (f" \"{el['label']}\"" if el.get("label") else "")
                            for el in item.ui_elements
                        ),
                    )
                )
                if rendered:
                    lines.append(f"*UI elements:* {rendered}")
            if item.narration_excerpt:
                lines.append(f"> {item.narration_excerpt}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_comparison_markdown(payload: dict[str, Any]) -> str:
    result = payload.get("result") or {}
    video_ids = payload.get("video_ids") or []
    lines = [f"# Comparison of {len(video_ids)} videos", "", f"Videos: {', '.join(video_ids)}", ""]

    common = result.get("common_patterns") or []
    if common:
        lines.append("## Common patterns")
        lines.extend(f"- {item}" for item in common)
        lines.append("")

    divergences = result.get("divergences") or []
    if divergences:
        lines.append("## Divergences")
        lines.extend(f"- {item}" for item in divergences)
        lines.append("")

    matrix = result.get("stage_matrix") or []
    if matrix:
        lines.append("## Stage matrix")
        for row in matrix:
            lines.append(f"- {row}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _frames_from_detail(detail: dict[str, Any], include_analysed_only: bool = True) -> list[FrameFinding]:
    raw_frames = detail.get("frames") or []
    if include_analysed_only:
        analysed = [f for f in raw_frames if f.get("analysis")]
        if analysed:
            raw_frames = analysed
    return [_flatten_frame(frame, index) for index, frame in enumerate(raw_frames, start=1)]


async def _build_report(
    client: FlowScopeClient,
    video_id: str,
    *,
    max_frames: int,
    include_transcript: bool,
) -> ReportResult:
    """Assemble a report from the detail endpoint."""
    detail = await client.get_video(
        video_id,
        include={"frames", "synthesis", "transcript"} | ({"transcript"} if include_transcript else set()),
    )
    video = detail.get("video") or {}
    job = detail.get("job") or {}
    note = ""

    frames = _frames_from_detail(detail)
    truncated = False
    if max_frames > 0 and len(frames) > max_frames:
        # Keep an even spread so the report still shows the whole flow rather
        # than only the first few screens.
        step = len(frames) / max_frames
        frames = [frames[min(len(frames) - 1, int(i * step))] for i in range(max_frames)]
        truncated = True
        note = (
            f"Showing {max_frames} of {len(detail.get('frames') or [])} screens, evenly sampled. "
            "Raise max_frames for the full set."
        )

    transcript_excerpt: str | None = None
    if include_transcript:
        segments = detail.get("transcript") or []
        transcript_excerpt = " ".join((seg.get("text") or "").strip() for seg in segments).strip() or None

    synthesis = detail.get("synthesis") or {}
    flow_steps = [
        FlowStep(
            step_index=step.get("step_index", 0),
            screen_name=step.get("screen_name", ""),
            description=step.get("description", ""),
            frame_id=step.get("frame_id"),
        )
        for step in sorted(synthesis.get("flow_steps") or [], key=lambda s: s.get("step_index", 0))
    ]

    if not synthesis and not frames:
        note = (
            (note + " ").strip()
            + "No analysis is stored for this video yet. Check flowscope_job_status; "
            "if the job failed, use flowscope_retry_video."
        ).strip()

    return ReportResult(
        ok=True,
        video_id=video_id,
        title=video.get("title"),
        channel=video.get("channel"),
        youtube_url=video.get("youtube_url", ""),
        duration_seconds=video.get("duration_seconds"),
        transcript_source=video.get("transcript_source"),
        status=job.get("status"),
        screen_count=len(detail.get("frames") or []),
        flow_steps=flow_steps,
        ux_insights=list(synthesis.get("ux_insights") or []),
        frames=frames,
        transcript_excerpt=transcript_excerpt,
        report_markdown=_render_report_markdown(detail, frames),
        truncated=truncated,
        note=note,
    )


async def _poll_until_terminal(
    client: FlowScopeClient,
    video_id: str,
    *,
    settings: Settings,
    wait_seconds: float,
    ctx: Context | None = None,
) -> JobStatusReport:
    """Poll GET /videos/{id} until the job is terminal or the budget runs out."""
    deadline = time.monotonic() + max(0.0, wait_seconds)
    interval = max(1.0, settings.poll_interval_seconds)
    report = _status_report(video_id, await client.get_video(video_id, include=set()))

    while not report.is_terminal and time.monotonic() < deadline:
        if ctx is not None:
            # Best-effort: clients that did not opt into progress simply ignore it.
            try:
                await ctx.report_progress(
                    progress=min(report.progress_current, report.progress_total or report.progress_current),
                    total=report.progress_total or None,
                    message=f"{report.stage_label} ({report.status})",
                )
            except Exception:
                pass
        await asyncio.sleep(min(interval, max(0.0, deadline - time.monotonic())))
        report = _status_report(video_id, await client.get_video(video_id, include=set()))

    return report


# --------------------------------------------------------------------------
# Server factory
# --------------------------------------------------------------------------


def build_server(*, transport: httpx2.AsyncBaseTransport | None = None) -> MCPServer:
    """Create the FlowScope MCP server.

    ``transport`` exists so tests can inject a mock HTTP transport; production
    callers leave it as ``None``.
    """
    server: MCPServer = MCPServer(
        name="flowscope",
        title="FlowScope UX Flow Analyser",
        version="0.1.0",
        instructions=SERVER_INSTRUCTIONS,
        website_url="https://github.com/DavidNgugi/flowscope",
    )

    def make_client() -> FlowScopeClient:
        return FlowScopeClient(load_settings(), transport=transport)

    # ---- read-only tools ---------------------------------------------------

    @server.tool(
        name="flowscope_health_check",
        title="Check FlowScope readiness",
        description=(
            "Check that the FlowScope backend is running and that its dependencies "
            "(ffmpeg, yt-dlp, an LLM provider key) are ready. Call this before "
            "submitting a video so a missing prerequisite is reported as a config "
            "problem rather than a failed multi-minute job."
        ),
        annotations=READ_ONLY,
    )
    async def health_check() -> HealthReport:
        settings = load_settings()
        async with make_client() as client:
            try:
                payload = await client.health()
            except FlowScopeError as exc:
                return HealthReport(
                    ok=False,
                    backend_url=settings.api_root,
                    ffmpeg_available=False,
                    yt_dlp_available=False,
                    blocking_problems=[str(exc)],
                    advice=(
                        "Start the backend with `cd backend && uvicorn app.main:app --port 8000`, "
                        "or point FLOWSCOPE_API_URL at the running instance."
                    ),
                )

        problems: list[str] = []
        if not payload.get("ffmpeg_available"):
            problems.append("ffmpeg is not on PATH; frames cannot be extracted. Install it with `brew install ffmpeg`.")
        if not payload.get("yt_dlp_available"):
            problems.append("yt-dlp is not importable; videos cannot be downloaded.")
        if not payload.get("llm_providers_available"):
            problems.append(
                "No LLM provider has credentials, so the screen and synthesis stages will fail. "
                "Set a key such as OPENAI_API_KEY or ANTHROPIC_API_KEY in backend/.env."
            )

        return HealthReport(
            ok=not problems,
            backend_url=settings.api_root,
            ffmpeg_available=bool(payload.get("ffmpeg_available")),
            ffmpeg_version=payload.get("ffmpeg_version"),
            yt_dlp_available=bool(payload.get("yt_dlp_available")),
            llm_providers_available=list(payload.get("llm_providers_available") or []),
            llm_provider=payload.get("llm_provider", ""),
            llm_vision_model=payload.get("llm_vision_model", ""),
            llm_synthesis_model=payload.get("llm_synthesis_model", ""),
            llm_comparison_model=payload.get("llm_comparison_model", ""),
            blocking_problems=problems,
            advice=("Fix the items in blocking_problems, then re-run this check." if problems else ""),
        )

    @server.tool(
        name="flowscope_list_videos",
        title="List analysed videos",
        description=(
            "List videos FlowScope has been asked to analyse, newest first, with the "
            "status of each one's latest job. Call this first: results are cached by "
            "YouTube video id, so an existing entry means the analysis is free to "
            "re-read and must not be resubmitted."
        ),
        annotations=READ_ONLY,
    )
    async def list_videos() -> VideoListReport:
        async with make_client() as client:
            try:
                rows = await client.list_videos()
            except FlowScopeError as exc:
                raise _fail(exc) from exc

        videos: list[VideoSummary] = []
        for row in rows:
            status = row.get("job_status")
            videos.append(
                VideoSummary(
                    video_id=row.get("id", ""),
                    youtube_id=row.get("youtube_id", ""),
                    youtube_url=row.get("youtube_url", ""),
                    title=row.get("title"),
                    channel=row.get("channel"),
                    duration_seconds=row.get("duration_seconds"),
                    status=status,
                    transcript_source=row.get("transcript_source"),
                    has_report=status == "done",
                    error_message=row.get("job_error"),
                )
            )
        return VideoListReport(count=len(videos), videos=videos)

    @server.tool(
        name="flowscope_job_status",
        title="Check analysis progress",
        description=(
            "Report the progress of a video's latest analysis job. Use this to poll "
            "after flowscope_analyze_video returns while still running, or to find out "
            "why an analysis failed. Poll every 20-30 seconds; do not resubmit the URL."
        ),
        annotations=READ_ONLY,
    )
    async def job_status(video_id: str) -> JobStatusReport:
        """Args:
        video_id: The FlowScope video id returned by flowscope_analyze_video.
        """
        async with make_client() as client:
            try:
                detail = await client.get_video(video_id, include=set())
            except FlowScopeError as exc:
                # Kept as a result rather than an error: "there is no such
                # video" is exactly what a polling caller needs to see, and the
                # agent should be able to distinguish it from a broken backend.
                return JobStatusReport(
                    video_id=video_id,
                    status="unknown",
                    stage_label="Unknown",
                    is_terminal=True,
                    next_action=(
                        "No job is stored for this video id. Check it with "
                        "flowscope_list_videos, or analyse it with flowscope_analyze_video."
                    ),
                    error_message=str(exc),
                )
        return _status_report(video_id, detail)

    @server.tool(
        name="flowscope_get_report",
        title="Read a video's UX report",
        description=(
            "Return the finished UX analysis of one video: the ordered user flow, "
            "synthesised UX insights, and per-screen findings with the visible UI "
            "elements and the narration that accompanies each screen. This is the "
            "primary tool for answering UX questions about a video."
        ),
        annotations=READ_ONLY,
    )
    async def get_report(
        video_id: str,
        max_frames: int = 24,
        include_transcript: bool = False,
    ) -> ReportResult:
        """Args:
        video_id: The FlowScope video id.
        max_frames: Maximum screens to include, evenly sampled across the flow.
            Use 0 for every screen. Defaults to 24 to keep the response small.
        include_transcript: Include the full transcript text. Off by default
            because it is long and the per-screen excerpts already carry the
            relevant narration.
        """
        async with make_client() as client:
            try:
                return await _build_report(
                    client,
                    video_id,
                    max_frames=max(0, max_frames),
                    include_transcript=include_transcript,
                )
            except FlowScopeError as exc:
                raise _fail(exc) from exc

    @server.tool(
        name="flowscope_compare_videos",
        title="Compare the UX of several videos",
        description=(
            "Compare two or more already-analysed videos and return their common "
            "patterns, their divergences, and a per-stage matrix. Requires at least "
            "two videos that have finished analysing. The comparison is an LLM call, "
            "so it takes a while and costs money; results are cached until one of the "
            "videos is re-analysed."
        ),
        annotations=COSTLY_CREATE,
    )
    async def compare_videos(video_ids: list[str], force_refresh: bool = False) -> ComparisonResult:
        """Args:
        video_ids: Two or more FlowScope video ids that have finished analysing.
        force_refresh: Recompute even though a stored comparison exists.
        """
        if len(video_ids) < 2:
            raise ToolError(
                f"A comparison needs at least two video ids; got {len(video_ids)}. "
                "Analyse each video first, then pass their ids together."
            )
        async with make_client() as client:
            try:
                payload = await client.compare_videos(video_ids, force_refresh=force_refresh)
            except FlowScopeError as exc:
                raise _fail(exc) from exc
        result = payload.get("result") or {}
        usage = payload.get("ai_usage") or {}
        return ComparisonResult(
            ok=True,
            video_ids=list(payload.get("video_ids") or video_ids),
            common_patterns=list(result.get("common_patterns") or []),
            divergences=list(result.get("divergences") or []),
            stage_matrix=list(result.get("stage_matrix") or []),
            estimated_cost_usd=usage.get("estimated_cost_usd"),
            report_markdown=_render_comparison_markdown(payload),
        )

    # ---- cost-incurring tools ---------------------------------------------

    @server.tool(
        name="flowscope_analyze_video",
        title="Analyse a YouTube product demo",
        description=(
            "Submit one or more YouTube URLs for UX analysis. FlowScope downloads "
            "each video, transcribes it, extracts and deduplicates screenshots of "
            "distinct screens, describes each screen with a vision model, and "
            "synthesises the flow. This is slow (minutes per video) and the vision "
            "steps cost money, so check flowscope_list_videos first -- an "
            "already-analysed video is reused for free and immediately. By default "
            "this waits for completion and returns the reports; if the analysis "
            "needs longer than wait_seconds it returns status 'running' with video "
            "ids to poll."
        ),
        annotations=COSTLY_CREATE,
    )
    async def analyze_video(
        urls: list[str],
        wait: bool = True,
        wait_seconds: float = 0.0,
        force: bool = False,
        force_local_transcription: bool = False,
        ctx: Context | None = None,
    ) -> AnalysisStarted:
        """Args:
        urls: YouTube video URLs to analyse.
        wait: Block until the analysis finishes (or the wait budget expires).
        wait_seconds: Override the wait budget in seconds. 0 uses the server
            default (FLOWSCOPE_MAX_WAIT, 900s). Ignored when wait is false.
        force: Re-analyse even if this video id is already stored. Costs money;
            only use it to deliberately replace an existing analysis.
        force_local_transcription: Skip YouTube captions and always transcribe
            the audio locally with Whisper. Slower, but useful when captions are
            wrong or missing.
        """
        settings = load_settings()
        cleaned = [url.strip() for url in urls if url and url.strip()]
        if not cleaned:
            raise ToolError(
                "No YouTube URLs were supplied. Pass at least one http(s) YouTube video URL in `urls`."
            )

        async with make_client() as client:
            try:
                submitted: list[SubmittedVideo] = await client.submit_videos(
                    cleaned,
                    force=force,
                    force_local_transcription=force_local_transcription,
                )
            except FlowScopeError as exc:
                raise _fail(exc) from exc

            reused_count = sum(1 for item in submitted if item.already_done)
            started_count = sum(1 for item in submitted if not item.already_done)

            if not wait:
                statuses = []
                for item in submitted:
                    detail = await client.get_video(item.video_id, include=set())
                    statuses.append(_status_report(item.video_id, detail))
                return AnalysisStarted(
                    status="running",
                    videos=statuses,
                    reused_count=reused_count,
                    started_count=started_count,
                    message=(
                        f"Submitted {started_count} video(s) for analysis"
                        + (f"; {reused_count} were already analysed." if reused_count else ".")
                        + " Poll flowscope_job_status with each video_id."
                    ),
                )

            budget = wait_seconds if wait_seconds > 0 else settings.max_wait_seconds
            # Split the budget across videos, and give videos that are already
            # cached no time at all.
            pending = [item for item in submitted if not item.already_done]
            per_video_budget = budget / len(pending) if pending else 0.0

            statuses = []
            for item in submitted:
                if item.already_done:
                    detail = await client.get_video(item.video_id, include=set())
                    statuses.append(_status_report(item.video_id, detail))
                else:
                    statuses.append(
                        await _poll_until_terminal(
                            client,
                            item.video_id,
                            settings=settings,
                            wait_seconds=per_video_budget,
                            ctx=ctx,
                        )
                    )

            finished = all(report.status == "done" for report in statuses)
            failed = [report for report in statuses if report.status == "error"]

            reports: list[ReportResult] = []
            if finished:
                # Everything is ready, so hand back the reports directly rather
                # than making the caller ask again.
                for report in statuses:
                    reports.append(
                        await _build_report(
                            client, report.video_id, max_frames=24, include_transcript=False
                        )
                    )

            if failed:
                status = "error"
                message = "At least one analysis failed: " + "; ".join(
                    f"{report.video_id}: {report.error_message}" for report in failed
                )
            elif finished:
                status = "reused" if started_count == 0 and reused_count else "done"
                message = f"Analysis complete for {len(statuses)} video(s)."
                if reused_count:
                    message += f" {reused_count} were already analysed and cost nothing."
            else:
                status = "running"
                message = (
                    f"Still running after the {budget:.0f}s wait budget. "
                    "Poll flowscope_job_status with the video_id; do not resubmit the URL."
                )

        return AnalysisStarted(
            status=status,
            videos=statuses,
            reports=reports,
            reused_count=reused_count,
            started_count=started_count,
            message=message,
        )

    @server.tool(
        name="flowscope_retry_video",
        title="Retry a failed analysis",
        description=(
            "Re-queue the last analysis job for a video after a failure, or start a "
            "fresh job when the previous one finished. Stages that already completed "
            "are reused, so a retry does not re-download or re-bill them."
        ),
        annotations=IDEMPOTENT_WRITE,
    )
    async def retry_video(video_id: str) -> MutationResult:
        """Args:
        video_id: The FlowScope video id to retry.
        """
        async with make_client() as client:
            try:
                payload = await client.retry_video(video_id)
            except FlowScopeError as exc:
                raise _fail(exc) from exc
        return MutationResult(
            ok=True,
            action="retry",
            video_id=video_id,
            job_id=payload.get("job_id"),
            status=payload.get("status"),
            message="Re-queued. Poll flowscope_job_status for progress.",
        )

    @server.tool(
        name="flowscope_reanalyze_video",
        title="Redo the derived analysis for a video",
        description=(
            "Discard a video's frames, per-screen analyses and synthesised flow, "
            "then redo extraction and analysis from the downloaded video. Use this "
            "after changing the LLM model or prompt. It deletes stored results and "
            "spends money again. Any comparison that included this video is dropped too."
        ),
        annotations=DESTRUCTIVE,
    )
    async def reanalyze_video(video_id: str) -> MutationResult:
        """Args:
        video_id: The FlowScope video id to re-analyse.
        """
        async with make_client() as client:
            try:
                payload = await client.reanalyze_video(video_id)
            except FlowScopeError as exc:
                raise _fail(exc) from exc
        return MutationResult(
            ok=True,
            action="reanalyze",
            video_id=video_id,
            job_id=payload.get("job_id"),
            status=payload.get("status"),
            message="Derived results discarded and analysis re-queued.",
        )

    @server.tool(
        name="flowscope_delete_video",
        title="Delete a video and its analysis",
        description=(
            "Permanently remove a video's analysis from FlowScope. With "
            "delete_media true it also deletes the downloaded video and extracted "
            "frames from disk, which cannot be undone. Ask the user before using "
            "delete_media."
        ),
        annotations=DESTRUCTIVE,
    )
    async def delete_video(video_id: str, delete_media: bool = False) -> MutationResult:
        """Args:
        video_id: The FlowScope video id to delete.
        delete_media: Also delete the downloaded video and frame files from disk.
        """
        async with make_client() as client:
            try:
                await client.delete_video(video_id, delete_media=delete_media)
            except FlowScopeError as exc:
                raise _fail(exc) from exc
        return MutationResult(
            ok=True,
            action="delete",
            video_id=video_id,
            message=(
                "Deleted, including downloaded media and frames."
                if delete_media
                else "Analysis deleted. Downloaded media and frames were kept on disk."
            ),
        )

    # ---- resources ---------------------------------------------------------

    @server.resource(
        "flowscope://videos",
        name="Analysed videos",
        title="FlowScope video index",
        description="JSON index of every video FlowScope knows about, with its latest job status.",
        mime_type="application/json",
    )
    async def videos_resource() -> str:
        import json

        async with make_client() as client:
            rows = await client.list_videos()
        slim = [
            {
                "video_id": row.get("id"),
                "youtube_id": row.get("youtube_id"),
                "title": row.get("title"),
                "status": row.get("job_status"),
                "duration_seconds": row.get("duration_seconds"),
            }
            for row in rows
        ]
        return json.dumps({"count": len(slim), "videos": slim}, indent=2)

    @server.resource(
        "flowscope://videos/{video_id}/report",
        name="Video UX report",
        title="FlowScope report for one video",
        description="The Markdown UX report for a single analysed video.",
        mime_type="text/markdown",
    )
    async def video_report_resource(video_id: str) -> str:
        async with make_client() as client:
            report = await _build_report(client, video_id, max_frames=0, include_transcript=False)
        return report.report_markdown

    # ---- frame images ------------------------------------------------------

    @server.tool(
        name="flowscope_frame_image",
        title="View a screen screenshot",
        description=(
            "Return the actual screenshot for one screen of a video, as an image "
            "the caller can look at. Use this when the written report is not enough "
            "and the visual layout matters, for example to judge spacing, "
            "hierarchy or visual style. Takes the video id and the frame_id from "
            "flowscope_get_report."
        ),
        annotations=READ_ONLY,
    )
    async def frame_image(video_id: str, frame_id: str) -> CallToolResult:
        """Args:
        video_id: The video the frame belongs to, from flowscope_get_report.
        frame_id: The frame_id field on any entry of that report's frames.
        """
        async with make_client() as client:
            # Frame ids are only exposed through the report payload, so the URL
            # is recovered from the video's own frame list. The video id is
            # required rather than searched for, because scanning every stored
            # video would issue one request per video on each call.
            try:
                detail = await client.get_video(video_id, include={"frames"})
            except FlowScopeError as exc:
                raise ToolError(
                    f"Could not read video {video_id}: {exc} "
                    "Check the id with flowscope_list_videos."
                ) from exc

            match = next(
                (f for f in (detail.get("frames") or []) if f.get("id") == frame_id), None
            )
            if match is None:
                raise ToolError(
                    f"Video {video_id} has no frame with id {frame_id}. Frame ids come "
                    "from the frames array of flowscope_get_report for this same video; "
                    "video ids and frame ids are different."
                )

            url = match.get("url")
            if not url:
                raise ToolError(
                    f"Frame {frame_id} has no stored image. The frame was recorded before "
                    "its image was written; re-run flowscope_reanalyze_video."
                )
            try:
                image = await client.fetch_image(url)
            except FlowScopeError as exc:
                raise ToolError(
                    f"Could not load the image for frame {frame_id}: {exc} "
                    "The media directory may have moved; re-run flowscope_reanalyze_video."
                ) from exc

            analysis = match.get("analysis") or {}
            caption = f"{analysis.get('screen_name') or 'Screen'} at {_mmss(match.get('timestamp_ms'))}"
            return CallToolResult(
                content=[
                    TextContent(type="text", text=caption),
                    ImageContent(type="image", data=image.data_base64, mime_type=image.mime_type),
                ],
                structured_content={
                    "frame_id": frame_id,
                    "video_id": video_id,
                    "timestamp": _mmss(match.get("timestamp_ms")),
                    "screen_name": analysis.get("screen_name"),
                    "mime_type": image.mime_type,
                    "byte_size": image.byte_size,
                },
            )

    # ---- prompts -----------------------------------------------------------

    @server.prompt(
        name="flowscope_ux_teardown",
        title="UX teardown of a product demo",
        description="Produce a structured UX teardown of one YouTube product-demo video.",
    )
    def ux_teardown_prompt(video_url: str) -> str:
        """Args:
        video_url: The YouTube URL to tear down.
        """
        return (
            f"Produce a UX teardown of this product demo: {video_url}\n\n"
            "Start with flowscope_list_videos to check whether it has already been "
            "analysed, then flowscope_analyze_video. When the report is ready, write "
            "the teardown with these sections:\n\n"
            "1. **What the product does** -- one paragraph, from the video alone.\n"
            "2. **The flow** -- the ordered steps a user takes, naming each screen.\n"
            "3. **Screen-by-screen notes** -- for each screen: its purpose, the "
            "controls on it, and the narration that explains it.\n"
            "4. **UX strengths** -- specific, evidence-backed observations. Cite the "
            "screen you saw each one on.\n"
            "5. **Friction and open questions** -- points where the demo skips a "
            "step or where a screen would confuse a first-time user.\n\n"
            "Ground every claim in the report's frame analyses. Where the video does "
            "not show something, say so rather than inferring it."
        )

    @server.prompt(
        name="flowscope_compare_flows",
        title="Compare several product demos",
        description="Compare the UX of two or more YouTube product-demo videos.",
    )
    def compare_flows_prompt(video_urls: str) -> str:
        """Args:
        video_urls: Comma-separated YouTube URLs to compare.
        """
        return (
            f"Compare the UX of these product demos: {video_urls}\n\n"
            "Analyse each one with flowscope_analyze_video (check "
            "flowscope_list_videos first, since cached videos are free), then call "
            "flowscope_compare_videos with the resulting video ids.\n\n"
            "Write the comparison as:\n"
            "1. **Shared patterns** -- what all of these products do the same way, "
            "and why that might be a convention rather than a choice.\n"
            "2. **Where they diverge** -- the meaningful differences, stage by "
            "stage, and the trade-off each one implies.\n"
            "3. **Standout choices** -- the single most interesting decision in each "
            "product, with the screen that shows it.\n"
            "4. **What to borrow** -- concrete, transferable ideas.\n\n"
            "Prefer the stage matrix over impressionistic summary, and name the "
            "screen behind each observation."
        )

    return server
