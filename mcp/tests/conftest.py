"""Shared fixtures: a fake FlowScope backend and a connected MCP client.

The tests never touch the network. A :class:`MockBackend` serves the handful of
REST endpoints the server uses, and the MCP session runs over the SDK's
in-memory transport, so the whole suite is deterministic and offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx2
import pytest

from flowscope_mcp.server import build_server

VIDEO_ID = "vid_0123456789abcdef01"
JOB_ID = "job_0123456789abcdef01"
FRAME_ID = "frm_0123456789abcdef01"
SECOND_VIDEO_ID = "vid_fedcba9876543210fe"

# A real (tiny) JPEG, so frame-image assertions exercise base64 round-tripping.
TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb0043000a07070807060a0808080b"
    "0a0a0b0e18100e0d0d0e1d15161118231f2524221f2221262b372f262934292122304131"
    "34393b3e3e3e252e4449433c48373d3e3bffdb0043010a0b0b0e0d0e1c10101c3b282228"
    "3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b"
    "3b3b3b3b3b3b3b3b3b3b3b3b3b3bffc00011080002000203012200021101031101ffc400"
    "1f0000010501010101010100000000000000000102030405060708090a0bffc400b51000"
    "02010303020403050504040000017d010203000411051221314106135161072271143281"
    "91a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a"
    "434445464748494a535455565758595a636465666768696a737475767778797a83848586"
    "8788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6"
    "c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffc400"
    "1f0100030101010101010101010000000000000102030405060708090a0bffc400b51100"
    "020102040403040705040400010277000102031104052131061241510761711322328108"
    "144291a1b1c109233352f0156272d10a162434e125f11718191a262728292a3536373839"
    "3a434445464748494a535455565758595a636465666768696a737475767778797a828384"
    "85868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4"
    "c5c6c7c8c9cad2d3d4d5d6d7d8d9dae2e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9faffda00"
    "0c03010002110311003f00e668a28af20fd10fffd9"
)


def detail_payload(
    status: str = "done",
    *,
    with_analysis: bool = True,
    video_id: str = VIDEO_ID,
) -> dict[str, Any]:
    """Build a GET /api/videos/{id} body in the real backend's shape."""
    frames: list[dict[str, Any]] = []
    synthesis: dict[str, Any] | None = None

    if with_analysis:
        frames = [
            {
                "id": FRAME_ID,
                "video_id": video_id,
                "timestamp_ms": 1500,
                "file_path": "/data/media/vid/frames/frame_001.jpg",
                "url": f"/media/{video_id}/frames/frame_001.jpg",
                "phash": "abc",
                "scene_score": 0.5,
                "width": 1280,
                "height": 720,
                "transcript_excerpt": "Welcome to the demo",
                "analysis": {
                    "id": "fa_1",
                    "frame_id": FRAME_ID,
                    "screen_name": "Landing page",
                    "flow_step_label": "Arrive",
                    "purpose": "First impression and primary CTA",
                    "ux_notes": "Single primary action above the fold",
                    "ui_elements": [
                        {"type": "button", "label": "Get started", "notes": "primary action"},
                        {"type": "nav item", "label": "Pricing", "notes": None},
                    ],
                    "transcript_excerpt": "Welcome to the demo",
                    "model_used": "gpt-5-mini",
                },
            }
        ]
        synthesis = {
            "flow_steps": [
                {
                    "step_index": 1,
                    "screen_name": "Landing page",
                    "frame_id": FRAME_ID,
                    "description": "The user lands on the pitch and sees one CTA.",
                }
            ],
            "ux_insights": ["Exactly one primary call to action above the fold"],
            "screens_gallery": [{"frame_id": FRAME_ID, "flow_stage": "Arrive"}],
        }

    return {
        "video": {
            "id": video_id,
            "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "youtube_id": "dQw4w9WgXcQ",
            "title": "Acme onboarding demo",
            "channel": "Acme",
            "duration_seconds": 300,
            "thumbnail_url": None,
            "transcript_source": "whisper",
            "error_message": None,
        },
        "job": {
            "id": JOB_ID,
            "video_id": video_id,
            "status": status,
            "progress_current": 5 if status == "done" else 1,
            "progress_total": 5,
            "stage_detail": "Analysis complete" if status == "done" else "Downloading the video",
            "error_message": "yt-dlp returned HTTP 403" if status == "error" else None,
        },
        "transcript": [
            {"id": "t1", "video_id": video_id, "start_ms": 0, "end_ms": 1200,
             "text": "Welcome to the demo", "source": "whisper"},
            {"id": "t2", "video_id": video_id, "start_ms": 1200, "end_ms": 2600,
             "text": "of the Acme product", "source": "whisper"},
        ],
        "frames": frames,
        "synthesis": synthesis,
        "ai_usage_runs": [],
    }


@dataclass
class MockBackend:
    """A configurable stand-in for the FlowScope REST API."""

    video_status: str = "done"
    with_analysis: bool = True
    # When set, write endpoints answer with this HTTP status instead of succeeding.
    fail_writes_with: int | None = None
    # When true, POST /videos reports the video as already analysed.
    submit_reports_reused: bool = False
    # Number of status polls before a freshly submitted job reports "done".
    polls_until_done: int = 0
    listed_videos: list[dict[str, Any]] = field(default_factory=list)
    requests: list[tuple[str, str]] = field(default_factory=list)
    polls: int = 0

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)

    # ---- routing -----------------------------------------------------------

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        method = request.method
        self.requests.append((method, path))

        if path == "/api/health":
            return self._health()
        if path == "/api/comparisons":
            return self._comparison()
        if path.startswith("/media/"):
            return httpx2.Response(200, content=TINY_JPEG, headers={"content-type": "image/jpeg"})
        if path == "/api/videos":
            return self._submit() if method == "POST" else self._list()
        if path.startswith("/api/videos/"):
            return self._video_route(method, path)
        return httpx2.Response(404, json={"detail": f"no route for {method} {path}"})

    def _health(self) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "ffmpeg_available": True,
                "ffmpeg_version": "6.1.1",
                "yt_dlp_available": True,
                "anthropic_key_present": False,
                "anthropic_model": "",
                "llm_providers_available": ["openai"],
                "llm_provider": "openai",
                "llm_model": "gpt-5-mini",
                "llm_vision_model": "gpt-5-mini",
                "llm_synthesis_model": "gpt-5-mini",
                "llm_comparison_model": "gpt-5-mini",
            },
        )

    def _list(self) -> httpx2.Response:
        if self.listed_videos:
            return httpx2.Response(200, json=self.listed_videos)
        return httpx2.Response(
            200,
            json=[
                {
                    "id": VIDEO_ID,
                    "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "youtube_id": "dQw4w9WgXcQ",
                    "title": "Acme onboarding demo",
                    "channel": "Acme",
                    "duration_seconds": 300,
                    "thumbnail_url": None,
                    "transcript_source": "whisper",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "job_id": JOB_ID,
                    "job_status": self.video_status,
                    "progress_current": 5,
                    "progress_total": 5,
                    "job_error": None,
                }
            ],
        )

    def _submit(self) -> httpx2.Response:
        if self.fail_writes_with:
            return httpx2.Response(self.fail_writes_with, json={"detail": "backend refused"})
        return httpx2.Response(
            200,
            json=[
                {
                    "video_id": VIDEO_ID,
                    "job_id": JOB_ID,
                    "youtube_id": "dQw4w9WgXcQ",
                    "status": "done" if self.submit_reports_reused else "queued",
                    "reused": self.submit_reports_reused,
                }
            ],
        )

    def _video_route(self, method: str, path: str) -> httpx2.Response:
        parts = path.strip("/").split("/")
        if len(parts) < 3:
            return httpx2.Response(404, json={"detail": "bad path"})
        video_id = parts[2]
        action = parts[3] if len(parts) > 3 else None

        if method in ("POST", "DELETE"):
            if self.fail_writes_with:
                return httpx2.Response(self.fail_writes_with, json={"detail": "backend refused"})
            if method == "DELETE":
                return httpx2.Response(200, json={"video_id": video_id, "deleted": True})
            if action in ("retry", "reanalyze"):
                return httpx2.Response(
                    200, json={"video_id": video_id, "job_id": JOB_ID, "status": "queued"}
                )
            return httpx2.Response(404, json={"detail": f"unknown action {action}"})

        if video_id not in (VIDEO_ID, SECOND_VIDEO_ID):
            return httpx2.Response(404, json={"detail": "video not found"})

        # A freshly submitted job reports a non-terminal status for the first
        # `polls_until_done` polls, so polling behaviour can be exercised.
        if self.polls < self.polls_until_done:
            self.polls += 1
            return httpx2.Response(200, json=detail_payload("downloading", with_analysis=False))

        return httpx2.Response(
            200,
            json=detail_payload(
                self.video_status, with_analysis=self.with_analysis, video_id=video_id
            ),
        )

    def _comparison(self) -> httpx2.Response:
        if self.fail_writes_with:
            return httpx2.Response(self.fail_writes_with, json={"detail": "backend refused"})
        return httpx2.Response(
            200,
            json={
                "video_ids": sorted([VIDEO_ID, SECOND_VIDEO_ID]),
                "result": {
                    "common_patterns": ["Both open with a single primary CTA"],
                    "divergences": ["Acme collects email before pricing"],
                    "stage_matrix": [
                        {"stage": "Arrive", "Acme": "hero + CTA", "Beta": "video autoplay"}
                    ],
                },
                "ai_usage": {"estimated_cost_usd": 0.0421},
            },
        )

    # ---- assertion helpers -------------------------------------------------

    def called(self, method: str, path_fragment: str) -> bool:
        return any(m == method and path_fragment in p for m, p in self.requests)

    def paths(self) -> list[str]:
        return [p for _, p in self.requests]


@pytest.fixture
def anyio_backend() -> str:
    """Run tests on asyncio only; trio is not a dependency of this package."""
    return "asyncio"


@pytest.fixture
def backend() -> MockBackend:
    return MockBackend()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the server at a fake base URL and keep the wait budget short."""
    monkeypatch.setenv("FLOWSCOPE_API_URL", "http://flowscope.test:8000")
    monkeypatch.setenv("FLOWSCOPE_POLL_INTERVAL", "1")
    monkeypatch.setenv("FLOWSCOPE_MAX_WAIT", "60")
    monkeypatch.setenv("FLOWSCOPE_HTTP_TIMEOUT", "10")


@pytest.fixture
def server(backend: MockBackend, env: None):
    return build_server(transport=backend.transport)


@pytest.fixture
async def client(server):
    """An MCP client connected in memory to the server under test."""
    from mcp import Client

    async with Client(server, raise_exceptions=True) as connected:
        yield connected
