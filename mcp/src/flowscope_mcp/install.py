"""Generate MCP client configuration for this server.

Every MCP host registers a server with the same three ideas -- a command, its
arguments, and an environment map -- but they disagree on the file, on the
top-level key, and on how (or whether) an environment placeholder is expanded.
Hand-editing that per editor is the most common reason a correctly written
server appears not to work.

The disagreements that actually break things, and which this module encodes:

* **VS Code's native file uses ``servers``; every other client uses
  ``mcpServers``.** Same file name pattern, different key.
* **Codex CLI is TOML**, not JSON.
* **Only some clients expand placeholders**, and with different syntax:
  Claude Code ``${VAR}``, Cursor ``${env:VAR}``, VS Code ``${input:...}``,
  Gemini ``$VAR``, Claude Desktop not at all.
* **Cursor's own field table marks ``type`` as required** for stdio even though
  its examples omit it, so it is emitted.

Rather than generate a placeholder that may not expand, the environment block
is only emitted when it is certain to work, and a literal value is used when the
user supplied one. The server already defaults to ``http://127.0.0.1:8000``, so
an absent variable is not an error.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

SERVER_KEY = "flowscope"
UVX = "uvx"
DEFAULT_PACKAGE_SPEC = "flowscope-mcp"
DEFAULT_API_URL = "http://127.0.0.1:8000"


def default_config_path(kind: str) -> str:
    """Per-platform config location for a known client."""
    home = Path.home()
    if kind == "claude-desktop":
        if sys.platform == "darwin":
            return str(home / "Library/Application Support/Claude/claude_desktop_config.json")
        if sys.platform == "win32":
            return str(Path(os.environ.get("APPDATA", home)) / "Claude/claude_desktop_config.json")
        return str(home / ".config/Claude/claude_desktop_config.json")
    raise KeyError(kind)


@dataclass(frozen=True)
class ClientTarget:
    """One MCP host's configuration conventions."""

    key: str
    label: str
    #: Top-level key holding the server map. VS Code is the odd one out.
    container_key: str = "mcpServers"
    #: Relative paths are project-scoped; absolute ones are per-user.
    config_path: str | None = None
    #: Emit `"type": "stdio"`. Required by Cursor's field table.
    emit_type: bool = False
    #: How this client expects an environment placeholder, if it supports one.
    env_template: str | None = None
    notes: tuple[str, ...] = ()


TARGETS: dict[str, ClientTarget] = {
    "claude-code": ClientTarget(
        key="claude-code",
        label="Claude Code",
        config_path=".mcp.json",
        env_template="${%s}",
        notes=(
            "Project scope: commit .mcp.json to share with the team.",
            "Interactive equivalent: claude mcp add flowscope -- uvx flowscope-mcp",
        ),
    ),
    "claude-desktop": ClientTarget(
        key="claude-desktop",
        label="Claude Desktop",
        config_path=default_config_path("claude-desktop"),
        # Claude Desktop does not expand placeholders, so only a literal is safe.
        env_template=None,
        notes=("Restart Claude Desktop after editing this file.",),
    ),
    "cursor": ClientTarget(
        key="cursor",
        label="Cursor",
        config_path=".cursor/mcp.json",
        emit_type=True,
        env_template="${env:%s}",
        notes=("Global config lives at ~/.cursor/mcp.json.",),
    ),
    "vscode": ClientTarget(
        key="vscode",
        label="VS Code (GitHub Copilot)",
        container_key="servers",
        config_path=".vscode/mcp.json",
        emit_type=True,
        # VS Code wants an `inputs` entry to back a ${input:...} placeholder;
        # handled separately rather than faked here.
        env_template=None,
        notes=(
            "VS Code uses the key 'servers'; every other client uses 'mcpServers'.",
            "For the user-scope file: Command Palette -> 'MCP: Open User Configuration'.",
        ),
    ),
    "vscode-portable": ClientTarget(
        key="vscode-portable",
        label="VS Code (portable .mcp.json)",
        config_path=".mcp.json",
        env_template="${%s}",
        notes=("VS Code also reads the portable .mcp.json shape under mcpServers.",),
    ),
    "codex": ClientTarget(
        key="codex",
        label="OpenAI Codex CLI",
        config_path=str(Path.home() / ".codex/config.toml"),
        notes=("Codex is TOML, not JSON. Append the snippet to that file.",),
    ),
    "gemini": ClientTarget(
        key="gemini",
        label="Gemini CLI",
        config_path=str(Path.home() / ".gemini/settings.json"),
        env_template="$%s",
        notes=(
            (
                "Gemini redacts inherited environment variables, so anything the server "
                "needs must be declared explicitly in env."
            ),
        ),
    ),
    "print": ClientTarget(key="print", label="Portable JSON (any client)"),
}


def resolve_launch(package_spec: str, python: str | None) -> tuple[str, list[str]]:
    """Return the (command, args) that launch this server.

    ``uvx`` is preferred: it needs no prior install and leaves nothing in the
    user's environment. Falling back to the current interpreter keeps the
    generator usable where uv is not installed.
    """
    if python is not None:
        return python, ["-m", "flowscope_mcp"]
    if shutil.which(UVX):
        return UVX, ["--from", package_spec, "flowscope-mcp"]
    return sys.executable, ["-m", "flowscope_mcp"]


def build_entry(
    target: ClientTarget,
    *,
    package_spec: str = DEFAULT_PACKAGE_SPEC,
    python: str | None = None,
    api_url: str | None = None,
) -> dict[str, object]:
    """The `flowscope` server entry for one client."""
    command, args = resolve_launch(package_spec, python)
    entry: dict[str, object] = {"command": command, "args": args}
    if target.emit_type:
        entry["type"] = "stdio"

    # An env block is only emitted when it will do what it looks like it does:
    # a literal value, or a placeholder in the syntax this client understands.
    if api_url:
        entry["env"] = {"FLOWSCOPE_API_URL": api_url}
    elif target.env_template:
        entry["env"] = {"FLOWSCOPE_API_URL": target.env_template % "FLOWSCOPE_API_URL"}
    return entry


def render_json_config(
    target: ClientTarget,
    *,
    package_spec: str = DEFAULT_PACKAGE_SPEC,
    python: str | None = None,
    api_url: str | None = None,
) -> dict[str, object]:
    """The complete JSON config document for one client."""
    return {
        target.container_key: {
            SERVER_KEY: build_entry(
                target, package_spec=package_spec, python=python, api_url=api_url
            )
        }
    }


def render_toml_config(
    *,
    package_spec: str = DEFAULT_PACKAGE_SPEC,
    python: str | None = None,
    api_url: str | None = None,
) -> str:
    """Codex CLI's TOML shape."""
    command, args = resolve_launch(package_spec, python)
    rendered_args = ", ".join(json.dumps(arg) for arg in args)
    lines = [
        f"[mcp_servers.{SERVER_KEY}]",
        f"command = {json.dumps(command)}",
        f"args = [{rendered_args}]",
    ]
    if api_url:
        lines.append(f"env = {{ FLOWSCOPE_API_URL = {json.dumps(api_url)} }}")
    lines.append("startup_timeout_sec = 20")
    lines.append("tool_timeout_sec = 600")
    return "\n".join(lines) + "\n"


def render_config(
    target_key: str,
    *,
    package_spec: str = DEFAULT_PACKAGE_SPEC,
    python: str | None = None,
    api_url: str | None = None,
) -> str:
    """Render a target's config as text, in that client's own syntax."""
    target = TARGETS[target_key]
    if target_key == "codex":
        return render_toml_config(package_spec=package_spec, python=python, api_url=api_url)
    document = render_json_config(
        target, package_spec=package_spec, python=python, api_url=api_url
    )
    return json.dumps(document, indent=2) + "\n"


def merge_into_json_file(path: Path, target: ClientTarget, document: dict[str, object]) -> str:
    """Write or update a JSON config, preserving any other servers.

    Returns a human-readable description of the outcome. Refuses to touch a file
    it cannot parse rather than overwriting the user's other settings.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, object] = {}
    if path.exists() and path.read_text().strip():
        try:
            existing = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            return f"{path} is not valid JSON ({exc}); left unchanged."
        if not isinstance(existing, dict):
            return f"{path} is not a JSON object; left unchanged."

    container = existing.get(target.container_key)
    if container is not None and not isinstance(container, dict):
        return f"{path} has a non-object {target.container_key!r} key; left unchanged."

    container = dict(container or {})
    action = "Updated" if SERVER_KEY in container else "Added"
    container[SERVER_KEY] = document[target.container_key][SERVER_KEY]  # type: ignore[index]
    existing[target.container_key] = container

    path.write_text(json.dumps(existing, indent=2) + "\n")
    return f"{action} the {SERVER_KEY!r} server in {path}"


def resolve_target_path(target: ClientTarget, *, project_dir: Path | None = None) -> Path | None:
    """Absolute path for a target's config file, or None when print-only."""
    if not target.config_path:
        return None
    path = Path(target.config_path)
    if path.is_absolute():
        return path
    return (project_dir or Path.cwd()) / path


def describe_targets() -> str:
    width = max(len(t.label) for t in TARGETS.values())
    lines = []
    for target in TARGETS.values():
        location = target.config_path or "(print to stdout)"
        lines.append(f"  {target.key:<16} {target.label:<{width}}  {location}")
    return "\n".join(lines)
