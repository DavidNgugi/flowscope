"""FlowScope MCP server.

Wraps the FlowScope backend REST API (a local FastAPI service) and exposes it
to any Model Context Protocol client as a small set of well-annotated tools,
resources and prompts.

The package is deliberately thin: no FlowScope application code is imported, so
this server can run from `uvx` in a different virtualenv from the backend and
never pins the backend's heavy dependencies (ffmpeg bindings, whisper, torch).
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
