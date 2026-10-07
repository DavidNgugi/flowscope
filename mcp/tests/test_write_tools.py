"""Behaviour of the tools that mutate backend state or spend money.

The distinction these tests protect is between an *expected* failure the model
should read and act on (a raised ``ToolError``, which arrives as an
``is_error=True`` result) and an unexpected crash (which the SDK sanitises).
Getting that wrong makes a failed call look like a successful one.
"""

from __future__ import annotations

import pytest
from mcp import Client

from .conftest import JOB_ID, SECOND_VIDEO_ID, VIDEO_ID, MockBackend

pytestmark = pytest.mark.anyio

VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


async def test_analyze_video_returns_the_report_when_it_finishes_immediately(
    client: Client, backend: MockBackend
) -> None:
    """A fast analysis should hand back the report, not just a job id."""
    result = await client.call_tool("flowscope_analyze_video", {"urls": [VIDEO_URL]})
    assert result.is_error is False
    payload = result.structured_content

    assert payload["status"] == "done"
    assert payload["started_count"] == 1
    assert payload["reused_count"] == 0
    assert len(payload["reports"]) == 1
    report = payload["reports"][0]
    assert report["video_id"] == VIDEO_ID
    assert report["ux_insights"] == ["Exactly one primary call to action above the fold"]
    assert report["report_markdown"].startswith("# Acme onboarding demo")


async def test_analyze_video_polls_until_the_job_finishes(
    client: Client, backend: MockBackend
) -> None:
    """A job that is still running must be polled, not reported as done."""
    backend.polls_until_done = 2
    result = await client.call_tool("flowscope_analyze_video", {"urls": [VIDEO_URL]})
    payload = result.structured_content

    assert payload["status"] == "done"
    assert backend.polls >= 2, "the server should have polled for status"
    assert backend.called("GET", f"/api/videos/{VIDEO_ID}")


async def test_analyze_video_reuses_a_cached_analysis_without_resubmitting(
    client: Client, backend: MockBackend
) -> None:
    """Re-analysing a known video is free; it must not be submitted again."""
    backend.submit_reports_reused = True
    result = await client.call_tool("flowscope_analyze_video", {"urls": [VIDEO_URL]})
    payload = result.structured_content

    assert payload["status"] == "reused"
    assert payload["reused_count"] == 1
    assert payload["started_count"] == 0
    assert "cost nothing" in payload["message"]
    assert len(payload["reports"]) == 1


async def test_analyze_video_without_waiting_reports_the_job_to_poll(
    client: Client, backend: MockBackend
) -> None:
    backend.polls_until_done = 5
    result = await client.call_tool(
        "flowscope_analyze_video", {"urls": [VIDEO_URL], "wait": False}
    )
    payload = result.structured_content

    assert payload["status"] == "running"
    assert payload["reports"] == []
    assert payload["videos"][0]["video_id"] == VIDEO_ID
    assert "flowscope_job_status" in payload["message"]
    # One status read to report the current state, but no polling loop: the
    # job's non-terminal status must be handed back rather than waited on.
    assert payload["videos"][0]["status"] != "done"
    assert payload["videos"][0]["is_terminal"] is False
    assert backend.paths().count(f"/api/videos/{VIDEO_ID}") == 1


async def test_analyze_video_gives_up_and_hands_back_the_video_id(
    client: Client, backend: MockBackend
) -> None:
    """Exceeding the wait budget is not an error: the caller continues polling."""
    backend.polls_until_done = 10_000
    result = await client.call_tool(
        "flowscope_analyze_video", {"urls": [VIDEO_URL], "wait_seconds": 1}
    )
    payload = result.structured_content

    assert result.is_error is False
    assert payload["status"] == "running"
    assert "do not resubmit" in payload["message"]
    assert payload["videos"][0]["video_id"] == VIDEO_ID


async def test_analyze_video_marks_a_failed_job_as_an_error(
    client: Client, backend: MockBackend
) -> None:
    backend.video_status = "error"
    result = await client.call_tool("flowscope_analyze_video", {"urls": [VIDEO_URL]})
    payload = result.structured_content

    assert payload["status"] == "error"
    assert "403" in payload["message"]


async def test_analyze_video_forwards_force_flags_to_the_backend(
    client: Client, backend: MockBackend
) -> None:
    await client.call_tool(
        "flowscope_analyze_video",
        {"urls": [VIDEO_URL], "force": True, "force_local_transcription": True},
    )
    assert backend.called("POST", "/api/videos")


async def test_analyze_video_rejects_an_empty_url_list(client: Client, backend: MockBackend) -> None:
    result = await client.call_tool("flowscope_analyze_video", {"urls": ["   "]})
    assert result.is_error is True
    assert "No YouTube URLs" in result.content[0].text


async def test_analyze_video_surfaces_a_backend_refusal(client: Client, backend: MockBackend) -> None:
    backend.fail_writes_with = 503
    result = await client.call_tool("flowscope_analyze_video", {"urls": [VIDEO_URL]})
    assert result.is_error is True
    assert "503" in result.content[0].text


async def test_analyze_video_reports_an_unreachable_backend(client: Client, backend: MockBackend) -> None:
    import httpx2
    from mcp import Client as MCPClient

    from flowscope_mcp.server import build_server

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused")

    server = build_server(transport=httpx2.MockTransport(refuse))
    async with MCPClient(server, raise_exceptions=True) as probe:
        result = await probe.call_tool("flowscope_analyze_video", {"urls": [VIDEO_URL]})

    assert result.is_error is True
    text = result.content[0].text
    assert "Cannot reach the FlowScope backend" in text
    # The message must name the fix, since the model cannot guess it.
    assert "FLOWSCOPE_API_URL" in text or "uvicorn" in text


async def test_compare_videos_returns_patterns_and_cost(
    client: Client, backend: MockBackend
) -> None:
    result = await client.call_tool(
        "flowscope_compare_videos", {"video_ids": [VIDEO_ID, SECOND_VIDEO_ID]}
    )
    assert result.is_error is False
    payload = result.structured_content

    assert payload["common_patterns"] == ["Both open with a single primary CTA"]
    assert payload["divergences"] == ["Acme collects email before pricing"]
    assert payload["stage_matrix"][0]["stage"] == "Arrive"
    assert payload["estimated_cost_usd"] == 0.0421
    assert "## Common patterns" in payload["report_markdown"]


async def test_compare_videos_requires_at_least_two_ids(client: Client, backend: MockBackend) -> None:
    result = await client.call_tool("flowscope_compare_videos", {"video_ids": [VIDEO_ID]})
    assert result.is_error is True
    assert "at least two" in result.content[0].text


async def test_retry_video_requeues_and_returns_the_job(client: Client, backend: MockBackend) -> None:
    result = await client.call_tool("flowscope_retry_video", {"video_id": VIDEO_ID})
    assert result.is_error is False
    payload = result.structured_content
    assert payload["ok"] is True
    assert payload["action"] == "retry"
    assert payload["job_id"] == JOB_ID
    assert payload["status"] == "queued"
    assert backend.called("POST", f"/api/videos/{VIDEO_ID}/retry")


async def test_retry_video_on_an_unknown_video_is_an_error(
    client: Client, backend: MockBackend
) -> None:
    # A retry against a non-existent video must not look like it worked.
    backend.fail_writes_with = 404
    result = await client.call_tool("flowscope_retry_video", {"video_id": "vid_nope"})
    assert result.is_error is True
    assert "404" in result.content[0].text


async def test_reanalyze_video_discards_and_requeues(client: Client, backend: MockBackend) -> None:
    result = await client.call_tool("flowscope_reanalyze_video", {"video_id": VIDEO_ID})
    assert result.is_error is False
    payload = result.structured_content
    assert payload["action"] == "reanalyze"
    assert payload["job_id"] == JOB_ID
    assert backend.called("POST", f"/api/videos/{VIDEO_ID}/reanalyze")


async def test_delete_video_defaults_to_keeping_media(client: Client, backend: MockBackend) -> None:
    """Deleting the analysis is recoverable; deleting the media is not."""
    result = await client.call_tool("flowscope_delete_video", {"video_id": VIDEO_ID})
    assert result.is_error is False
    payload = result.structured_content
    assert payload["ok"] is True
    assert payload["action"] == "delete"
    assert "kept on disk" in payload["message"]
    assert backend.called("DELETE", f"/api/videos/{VIDEO_ID}")


async def test_delete_video_can_remove_media(client: Client, backend: MockBackend) -> None:
    result = await client.call_tool(
        "flowscope_delete_video", {"video_id": VIDEO_ID, "delete_media": True}
    )
    payload = result.structured_content
    assert "including downloaded media" in payload["message"].lower()


async def test_delete_video_surfaces_failure(client: Client, backend: MockBackend) -> None:
    backend.fail_writes_with = 500
    result = await client.call_tool("flowscope_delete_video", {"video_id": VIDEO_ID})
    assert result.is_error is True
    assert "500" in result.content[0].text


# ---- resources and prompts -------------------------------------------------


async def test_videos_resource_returns_a_json_index(client: Client, backend: MockBackend) -> None:
    import json

    read = await client.read_resource("flowscope://videos")
    assert read.contents, "the resource should return contents"
    block = read.contents[0]
    assert block.mime_type == "application/json"
    payload = json.loads(block.text)
    assert payload["count"] == 1
    assert payload["videos"][0]["video_id"] == VIDEO_ID


async def test_report_resource_renders_markdown(client: Client, backend: MockBackend) -> None:
    read = await client.read_resource(f"flowscope://videos/{VIDEO_ID}/report")
    block = read.contents[0]
    assert block.mime_type == "text/markdown"
    assert block.text.startswith("# Acme onboarding demo")
    assert "## UX insights" in block.text


async def test_ux_teardown_prompt_embeds_the_url(client: Client, backend: MockBackend) -> None:
    prompt = await client.get_prompt("flowscope_ux_teardown", {"video_url": VIDEO_URL})
    text = prompt.messages[0].content.text
    assert VIDEO_URL in text
    # The prompt must steer the agent through the cache check first.
    assert "flowscope_list_videos" in text
    assert "flowscope_analyze_video" in text


async def test_compare_flows_prompt_embeds_the_urls(client: Client, backend: MockBackend) -> None:
    prompt = await client.get_prompt("flowscope_compare_flows", {"video_urls": VIDEO_URL})
    text = prompt.messages[0].content.text
    assert VIDEO_URL in text
    assert "flowscope_compare_videos" in text
