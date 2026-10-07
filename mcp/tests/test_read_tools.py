"""Behaviour of the read-only tools."""

from __future__ import annotations

import pytest
from mcp import Client

from .conftest import FRAME_ID, TINY_JPEG, VIDEO_ID, MockBackend

pytestmark = pytest.mark.anyio


async def test_health_check_reports_ready(client: Client, backend: MockBackend) -> None:
    result = await client.call_tool("flowscope_health_check", {})
    assert result.is_error is False
    payload = result.structured_content
    assert payload["ok"] is True
    assert payload["blocking_problems"] == []
    assert payload["ffmpeg_available"] is True
    assert payload["llm_providers_available"] == ["openai"]
    assert payload["llm_vision_model"] == "gpt-5-mini"


async def test_health_check_names_each_missing_prerequisite(
    client: Client, backend: MockBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing dependency must come back as advice, not as a failure."""

    def degraded(request):
        if request.url.path == "/api/health":
            return _json(
                {
                    "ffmpeg_available": False,
                    "ffmpeg_version": None,
                    "yt_dlp_available": True,
                    "anthropic_key_present": False,
                    "anthropic_model": "",
                    "llm_providers_available": [],
                    "llm_provider": "anthropic",
                    "llm_model": "",
                    "llm_vision_model": "",
                    "llm_synthesis_model": "",
                    "llm_comparison_model": "",
                }
            )
        return backend.handle(request)

    import httpx2
    from mcp import Client as MCPClient

    from flowscope_mcp.server import build_server

    server = build_server(transport=httpx2.MockTransport(degraded))
    async with MCPClient(server, raise_exceptions=True) as probe:
        result = await probe.call_tool("flowscope_health_check", {})

    # Not an error: the health tool's job is to report the diagnosis.
    assert result.is_error is False
    payload = result.structured_content
    assert payload["ok"] is False
    joined = " ".join(payload["blocking_problems"])
    assert "ffmpeg" in joined
    assert "No LLM provider" in joined
    assert payload["advice"]


async def test_health_check_survives_an_unreachable_backend(
    client: Client, backend: MockBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead backend is the answer to 'is it ready?', not a tool crash."""
    import httpx2
    from mcp import Client as MCPClient

    from flowscope_mcp.server import build_server

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused")

    server = build_server(transport=httpx2.MockTransport(refuse))
    async with MCPClient(server, raise_exceptions=True) as probe:
        result = await probe.call_tool("flowscope_health_check", {})

    assert result.is_error is False
    payload = result.structured_content
    assert payload["ok"] is False
    assert "Cannot reach the FlowScope backend" in " ".join(payload["blocking_problems"])
    assert "uvicorn" in payload["advice"]


async def test_list_videos_summarises_each_entry(client: Client, backend: MockBackend) -> None:
    result = await client.call_tool("flowscope_list_videos", {})
    payload = result.structured_content
    assert payload["count"] == 1
    video = payload["videos"][0]
    assert video["video_id"] == VIDEO_ID
    assert video["title"] == "Acme onboarding demo"
    assert video["status"] == "done"
    assert video["has_report"] is True


async def test_list_videos_flags_failed_jobs(client: Client, backend: MockBackend) -> None:
    backend.video_status = "error"
    backend.listed_videos = [
        {
            "id": VIDEO_ID,
            "youtube_id": "dQw4w9WgXcQ",
            "youtube_url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
            "title": None,
            "channel": None,
            "duration_seconds": None,
            "transcript_source": None,
            "job_status": "error",
            "job_error": "yt-dlp returned HTTP 403",
        }
    ]
    result = await client.call_tool("flowscope_list_videos", {})
    video = result.structured_content["videos"][0]
    assert video["status"] == "error"
    assert video["has_report"] is False
    assert "403" in video["error_message"]


async def test_job_status_describes_progress_and_next_step(
    client: Client, backend: MockBackend
) -> None:
    backend.video_status = "analyzing_frames"
    result = await client.call_tool("flowscope_job_status", {"video_id": VIDEO_ID})
    payload = result.structured_content
    assert payload["status"] == "analyzing_frames"
    assert payload["stage_label"] == "Analysing each screen (vision model)"
    assert payload["is_terminal"] is False
    assert "Poll" in payload["next_action"]


async def test_job_status_on_a_finished_video_points_at_the_report(
    client: Client, backend: MockBackend
) -> None:
    result = await client.call_tool("flowscope_job_status", {"video_id": VIDEO_ID})
    payload = result.structured_content
    assert payload["status"] == "done"
    assert payload["is_terminal"] is True
    assert payload["percent_complete"] == 100.0
    assert "flowscope_get_report" in payload["next_action"]


async def test_job_status_on_a_failed_video_explains_the_retry(
    client: Client, backend: MockBackend
) -> None:
    backend.video_status = "error"
    result = await client.call_tool("flowscope_job_status", {"video_id": VIDEO_ID})
    payload = result.structured_content
    assert payload["status"] == "error"
    assert payload["is_terminal"] is True
    assert "403" in payload["error_message"]
    assert "flowscope_retry_video" in payload["next_action"]


async def test_job_status_for_an_unknown_video_is_an_answer_not_a_crash(
    client: Client, backend: MockBackend
) -> None:
    """A polling caller must be able to tell 'no job' from 'backend broken'."""
    result = await client.call_tool("flowscope_job_status", {"video_id": "vid_does_not_exist"})
    assert result.is_error is False
    payload = result.structured_content
    assert payload["status"] == "unknown"
    assert payload["is_terminal"] is True
    assert "flowscope_list_videos" in payload["next_action"]


async def test_get_report_returns_flow_insights_and_screens(
    client: Client, backend: MockBackend
) -> None:
    result = await client.call_tool("flowscope_get_report", {"video_id": VIDEO_ID})
    assert result.is_error is False
    payload = result.structured_content

    assert payload["ok"] is True
    assert payload["video_id"] == VIDEO_ID
    assert payload["title"] == "Acme onboarding demo"
    assert payload["ux_insights"] == ["Exactly one primary call to action above the fold"]
    assert payload["flow_steps"][0]["screen_name"] == "Landing page"

    frame = payload["frames"][0]
    assert frame["index"] == 1
    assert frame["screen_name"] == "Landing page"
    assert frame["timestamp"] == "00:01"
    assert frame["timestamp_seconds"] == 1.5
    assert frame["narration_excerpt"] == "Welcome to the demo"
    assert frame["ui_elements"][0]["label"] == "Get started"
    # The image URL is what flowscope_frame_image needs to resolve the frame.
    assert frame["image_url"].endswith("frame_001.jpg")


async def test_get_report_markdown_is_human_readable(client: Client, backend: MockBackend) -> None:
    result = await client.call_tool("flowscope_get_report", {"video_id": VIDEO_ID})
    markdown = result.structured_content["report_markdown"]
    assert markdown.startswith("# Acme onboarding demo")
    assert "## UX insights" in markdown
    assert "## User flow" in markdown
    assert "## Screens" in markdown
    assert "Landing page" in markdown
    assert "Exactly one primary call to action above the fold" in markdown


async def test_get_report_omits_the_transcript_by_default(
    client: Client, backend: MockBackend
) -> None:
    """The transcript is long and the per-screen excerpts already carry it."""
    result = await client.call_tool("flowscope_get_report", {"video_id": VIDEO_ID})
    assert result.structured_content["transcript_excerpt"] is None


async def test_get_report_can_include_the_transcript(client: Client, backend: MockBackend) -> None:
    result = await client.call_tool(
        "flowscope_get_report", {"video_id": VIDEO_ID, "include_transcript": True}
    )
    excerpt = result.structured_content["transcript_excerpt"]
    assert "Welcome to the demo" in excerpt
    assert "of the Acme product" in excerpt


async def test_get_report_samples_frames_when_max_frames_is_small(
    client: Client, backend: MockBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A truncated report must say so rather than silently dropping screens."""
    import httpx2
    from mcp import Client as MCPClient

    from flowscope_mcp.server import build_server

    many_frames = [
        {
            "id": f"frm_{i:02d}",
            "video_id": VIDEO_ID,
            "timestamp_ms": i * 1000,
            "url": f"/media/{VIDEO_ID}/frames/frame_{i:03d}.jpg",
            "transcript_excerpt": f"narration {i}",
            "analysis": {
                "screen_name": f"Screen {i}",
                "flow_step_label": f"Step {i}",
                "purpose": "p",
                "ux_notes": "n",
                "ui_elements": [],
            },
        }
        for i in range(10)
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == f"/api/videos/{VIDEO_ID}":
            from .conftest import detail_payload

            body = detail_payload("done")
            body["frames"] = many_frames
            return httpx2.Response(200, json=body)
        return backend.handle(request)

    server = build_server(transport=httpx2.MockTransport(handler))
    async with MCPClient(server, raise_exceptions=True) as probe:
        result = await probe.call_tool(
            "flowscope_get_report", {"video_id": VIDEO_ID, "max_frames": 4}
        )

    payload = result.structured_content
    assert payload["truncated"] is True
    assert len(payload["frames"]) == 4
    assert payload["screen_count"] == 10
    # Sampling must span the flow, not just take the first N screens.
    assert payload["frames"][0]["screen_name"] == "Screen 0"
    assert payload["frames"][-1]["screen_name"] != "Screen 3"
    assert "evenly sampled" in payload["note"]


async def test_get_report_notes_a_video_that_has_no_analysis_yet(
    client: Client, backend: MockBackend
) -> None:
    backend.video_status = "downloading"
    backend.with_analysis = False
    result = await client.call_tool("flowscope_get_report", {"video_id": VIDEO_ID})
    assert result.is_error is False
    payload = result.structured_content
    assert payload["frames"] == []
    assert payload["flow_steps"] == []
    assert "No analysis is stored" in payload["note"]


async def test_get_report_raises_for_an_unknown_video(client: Client, backend: MockBackend) -> None:
    result = await client.call_tool("flowscope_get_report", {"video_id": "vid_missing"})
    assert result.is_error is True
    text = result.content[0].text
    assert "404" in text or "not found" in text.lower()


async def test_frame_image_returns_the_screenshot_as_an_image(
    client: Client, backend: MockBackend
) -> None:
    result = await client.call_tool(
        "flowscope_frame_image", {"video_id": VIDEO_ID, "frame_id": FRAME_ID}
    )
    assert result.is_error is False
    kinds = [block.type for block in result.content]
    assert "text" in kinds
    assert "image" in kinds

    image = next(block for block in result.content if block.type == "image")
    assert image.mime_type == "image/jpeg"
    assert image.data  # base64 payload present
    assert result.structured_content["frame_id"] == FRAME_ID
    assert result.structured_content["screen_name"] == "Landing page"
    assert result.structured_content["byte_size"] > 0


async def test_frame_image_rejects_a_frame_from_the_wrong_video(
    client: Client, backend: MockBackend
) -> None:
    """Frame ids are not globally unique, so the video id must be honoured."""
    result = await client.call_tool(
        "flowscope_frame_image", {"video_id": "vid_other", "frame_id": FRAME_ID}
    )
    assert result.is_error is True
    assert "vid_other" in result.content[0].text


async def test_frame_image_reports_an_unknown_frame(client: Client, backend: MockBackend) -> None:
    result = await client.call_tool(
        "flowscope_frame_image", {"video_id": VIDEO_ID, "frame_id": "frm_nope"}
    )
    assert result.is_error is True
    assert "frm_nope" in result.content[0].text


def _json(payload: dict):
    import httpx2

    return httpx2.Response(200, json=payload)


def test_the_jpeg_fixture_is_a_real_jpeg() -> None:
    """base64 round-tripping is only meaningful against valid image bytes.

    An earlier version of this fixture was syntactically hex but not a decodable
    JPEG, which made the image test pass while proving very little.
    """
    assert TINY_JPEG.startswith(b"\xff\xd8"), "missing JPEG start-of-image marker"
    assert TINY_JPEG.endswith(b"\xff\xd9"), "missing JPEG end-of-image marker"
    # JFIF APP0 marker, present in every encoder's baseline output.
    assert b"JFIF" in TINY_JPEG[:24]
    # A quantisation table must follow, or no decoder will accept it.
    assert b"\xff\xdb" in TINY_JPEG
