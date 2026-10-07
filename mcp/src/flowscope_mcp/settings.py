"""Runtime configuration for the FlowScope MCP server.

Configuration is read from the environment on every access rather than cached
at import time. Two reasons:

* MCP clients launch servers with a per-server ``env`` block, so the values are
  already present in the process environment -- there is nothing to load from
  disk.
* The MCP Python SDK v2 deliberately stopped reading ``MCP_*`` environment
  variables and ``.env`` files, so configuration is this server's own concern.

Everything is prefixed ``FLOWSCOPE_`` to avoid colliding with the backend's own
variables when both run from the same shell.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 30.0
# A full analysis downloads a video, transcribes it, extracts and de-duplicates
# frames, then makes one vision call per frame plus a synthesis call. Minutes,
# not seconds -- but still bounded, because a tool call that can hang forever is
# worse than one that hands back a job id.
DEFAULT_MAX_WAIT_SECONDS = 900.0
DEFAULT_POLL_INTERVAL_SECONDS = 5.0


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Resolved connection settings for one tool call."""

    base_url: str = DEFAULT_BASE_URL
    api_token: str = ""
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS

    @property
    def api_root(self) -> str:
        """``base_url`` normalised to the ``/api`` root, without a trailing slash."""
        base = self.base_url.strip().rstrip("/")
        if not base:
            base = DEFAULT_BASE_URL
        # Accept both "http://host:8000" and "http://host:8000/api" so the value
        # can be pasted straight from the browser.
        if not base.endswith("/api"):
            base = f"{base}/api"
        return base

    @property
    def media_root(self) -> str:
        """Root that frame ``url`` values are relative to (``/media/...``)."""
        return self.api_root[: -len("/api")]


def load_settings() -> Settings:
    """Read settings from the environment (called per tool invocation)."""
    return Settings(
        base_url=os.environ.get("FLOWSCOPE_API_URL", "").strip() or DEFAULT_BASE_URL,
        api_token=os.environ.get("FLOWSCOPE_API_TOKEN", "").strip(),
        timeout_seconds=_env_float("FLOWSCOPE_HTTP_TIMEOUT", DEFAULT_TIMEOUT_SECONDS),
        max_wait_seconds=_env_float("FLOWSCOPE_MAX_WAIT", DEFAULT_MAX_WAIT_SECONDS),
        poll_interval_seconds=_env_float("FLOWSCOPE_POLL_INTERVAL", DEFAULT_POLL_INTERVAL_SECONDS),
    )
