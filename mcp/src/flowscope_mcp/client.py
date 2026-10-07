"""Typed async client for the FlowScope backend REST API.

This is the only module that knows the backend's HTTP shape. Keeping it
separate from the tool definitions means the MCP surface can be unit-tested
against a fake transport without a running backend.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import httpx2

from .settings import Settings

# Non-JSON bodies can be large; cap what we will read into memory.
MAX_MEDIA_BYTES = 12 * 1024 * 1024


class FlowScopeError(RuntimeError):
    """The backend answered, but with an error (or an unusable payload)."""


class FlowScopeUnreachable(FlowScopeError):
    """The backend could not be reached at all."""


@dataclass(frozen=True)
class FetchedImage:
    """A frame image ready to be handed to an MCP client."""

    data_base64: str
    mime_type: str
    byte_size: int


@dataclass(frozen=True)
class SubmittedVideo:
    """One row of the POST /videos response."""

    video_id: str
    job_id: str | None
    youtube_id: str
    status: str
    reused: bool

    @property
    def already_done(self) -> bool:
        return self.reused and self.status == "done"


def _detail_from_response(response: httpx2.Response) -> str:
    """Pull FastAPI's ``detail`` string out of an error response."""
    try:
        payload = response.json()
    except Exception:
        text = response.text.strip()
        return text[:400] if text else response.reason_phrase
    if isinstance(payload, dict) and "detail" in payload:
        detail = payload["detail"]
        if isinstance(detail, str):
            return detail
        # Pydantic validation errors arrive as a list of dicts.
        return "; ".join(str(item) for item in detail) if isinstance(detail, list) else str(detail)
    return str(payload)[:400]


class FlowScopeClient:
    """Async client for one backend base URL.

    Use as an async context manager so the underlying connection pool is
    created and closed inside a single task::

        async with FlowScopeClient(settings) as client:
            await client.health()
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._client: httpx2.AsyncClient | None = None

    async def __aenter__(self) -> FlowScopeClient:
        headers = {"Accept": "application/json", "User-Agent": "flowscope-mcp"}
        if self._settings.api_token:
            headers["Authorization"] = f"Bearer {self._settings.api_token}"
        kwargs: dict[str, Any] = {
            "base_url": self._settings.api_root,
            "headers": headers,
            "timeout": self._settings.timeout_seconds,
            "follow_redirects": True,
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        self._client = httpx2.AsyncClient(**kwargs)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def _http(self) -> httpx2.AsyncClient:
        if self._client is None:
            raise RuntimeError("FlowScopeClient used outside of an async context manager")
        return self._client

    # ---- transport helpers -------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx2.Response:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx2.TimeoutException as exc:
            raise FlowScopeError(
                f"The FlowScope backend at {self._settings.api_root} took longer than "
                f"{self._settings.timeout_seconds:.0f}s to answer {method} {path}. "
                "Long-running stages should be polled with flowscope_job_status instead "
                "of awaited in one call."
            ) from exc
        except httpx2.ConnectError as exc:
            raise FlowScopeUnreachable(
                f"Cannot reach the FlowScope backend at {self._settings.api_root} ({exc}). "
                "Start it with `cd backend && uvicorn app.main:app --port 8000`, or set "
                "FLOWSCOPE_API_URL to the running instance."
            ) from exc
        except httpx2.RequestError as exc:
            raise FlowScopeUnreachable(f"Request to FlowScope failed: {exc}") from exc

        if response.status_code >= 400:
            detail = _detail_from_response(response)
            raise FlowScopeError(f"FlowScope returned HTTP {response.status_code} for {method} {path}: {detail}")
        return response

    async def _get_json(self, path: str, **kwargs: Any) -> Any:
        response = await self._request("GET", path, **kwargs)
        try:
            return response.json()
        except Exception as exc:
            raise FlowScopeError(f"FlowScope returned a non-JSON body for GET {path}") from exc

    # ---- endpoints ---------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        """GET /api/health."""
        return await self._get_json("/health")

    async def submit_videos(
        self,
        urls: list[str],
        *,
        force: bool = False,
        force_local_transcription: bool = False,
    ) -> list[SubmittedVideo]:
        """POST /api/videos. Returns one entry per submitted URL."""
        payload = {
            "urls": urls,
            "force": force,
            "force_local_transcription": force_local_transcription,
        }
        response = await self._request("POST", "/videos", json=payload)
        rows = response.json()
        return [
            SubmittedVideo(
                video_id=row["video_id"],
                job_id=row.get("job_id"),
                youtube_id=row.get("youtube_id", ""),
                status=row.get("status", "queued"),
                reused=bool(row.get("reused", False)),
            )
            for row in rows
        ]

    async def list_videos(self) -> list[dict[str, Any]]:
        """GET /api/videos, newest first, each with its latest job status."""
        return await self._get_json("/videos")

    async def get_video(self, video_id: str, *, include: set[str] | None = None) -> dict[str, Any]:
        """GET /api/videos/{id}; ``include`` is applied client-side.

        The backend has a single detail endpoint with no field selection, so
        trimming happens here rather than in a second request shape.
        """
        detail = await self._get_json(f"/videos/{video_id}")
        if include is None:
            return detail
        keep = {"video", "job"} | include
        return {key: value for key, value in detail.items() if key in keep}

    async def compare_videos(self, video_ids: list[str], *, force_refresh: bool = False) -> dict[str, Any]:
        """GET /api/comparisons. This one really does block on an LLM call."""
        params = {"video_ids": ",".join(video_ids), "force_refresh": str(force_refresh).lower()}
        # Comparing pays for a completion, so allow far longer than a normal GET.
        timeout = max(self._settings.timeout_seconds, 300.0)
        response = await self._request("GET", "/comparisons", params=params, timeout=timeout)
        return response.json()

    async def retry_video(self, video_id: str) -> dict[str, Any]:
        """POST /api/videos/{id}/retry."""
        response = await self._request("POST", f"/videos/{video_id}/retry")
        return response.json()

    async def reanalyze_video(self, video_id: str) -> dict[str, Any]:
        """POST /api/videos/{id}/reanalyze. Destroys derived artifacts first."""
        response = await self._request("POST", f"/videos/{video_id}/reanalyze")
        return response.json()

    async def delete_video(self, video_id: str, *, delete_media: bool = False) -> dict[str, Any]:
        """DELETE /api/videos/{id}."""
        params = {"delete_media": str(delete_media).lower()}
        response = await self._request("DELETE", f"/videos/{video_id}", params=params)
        return response.json()

    async def fetch_image(self, url: str) -> FetchedImage:
        """Download a frame image from the backend's ``/media`` mount.

        ``url`` may be the root-relative path the API returns (``/media/...``)
        or an absolute URL. A relative path is resolved against
        :attr:`Settings.media_root`, not the ``/api`` root.
        """
        if url.startswith(("http://", "https://")):
            target = url
        elif url.startswith("/"):
            target = f"{self._settings.media_root}{url}"
        else:
            target = f"{self._settings.media_root}/media/{url.lstrip('/')}"

        try:
            response = await self._http.get(target, headers={"Accept": "image/*,application/octet-stream"})
        except httpx2.RequestError as exc:
            raise FlowScopeUnreachable(f"Could not download frame image {target}: {exc}") from exc

        if response.status_code >= 400:
            raise FlowScopeError(f"Frame image {target} returned HTTP {response.status_code}")

        content = response.content
        if not content:
            raise FlowScopeError(f"Frame image {target} was empty")
        if len(content) > MAX_MEDIA_BYTES:
            raise FlowScopeError(
                f"Frame image {target} is {len(content)} bytes, above the "
                f"{MAX_MEDIA_BYTES}-byte limit this server will inline."
            )

        mime_type = response.headers.get("content-type", "").split(";")[0].strip()
        if not mime_type or not mime_type.startswith("image/"):
            mime_type = "image/jpeg"

        return FetchedImage(
            data_base64=base64.b64encode(content).decode("ascii"),
            mime_type=mime_type,
            byte_size=len(content),
        )
