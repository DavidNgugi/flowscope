"""Tests for the client-config generator.

The per-client differences encoded here are the ones that actually break an
install: VS Code's `servers` key against everyone else's `mcpServers`, Codex's
TOML against everyone else's JSON, and the fact that not every client expands a
`${VAR}` placeholder. Each is asserted so a future edit cannot silently drop it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flowscope_mcp import install
from flowscope_mcp.install_cli import main

pytestmark = pytest.mark.anyio


def test_vscode_uses_the_servers_key_and_everyone_else_uses_mcpservers() -> None:
    """VS Code's native file is the one host whose top-level key differs."""
    vscode = json.loads(install.render_config("vscode"))
    assert set(vscode) == {"servers"}
    assert "flowscope" in vscode["servers"]

    for client in ("claude-code", "claude-desktop", "cursor", "gemini", "print"):
        document = json.loads(install.render_config(client))
        assert set(document) == {"mcpServers"}, f"{client} should use mcpServers"
        assert "flowscope" in document["mcpServers"]


def test_codex_renders_toml_not_json() -> None:
    """Codex CLI is the only TOML client; emitting JSON there fails silently."""
    rendered = install.render_config("codex")
    with pytest.raises(json.JSONDecodeError):
        json.loads(rendered)
    assert "[mcp_servers.flowscope]" in rendered
    assert "command = " in rendered
    assert "args = [" in rendered


def test_timeouts_are_configured_for_a_slow_pipeline() -> None:
    """A default tool timeout would cut off an analysis mid-flight."""
    rendered = install.render_config("codex")
    assert "tool_timeout_sec = 600" in rendered
    assert "startup_timeout_sec = 20" in rendered


def test_cursor_gets_the_stdio_type_field() -> None:
    """Cursor's own field table marks `type` required, though its examples omit it."""
    entry = json.loads(install.render_config("cursor"))["mcpServers"]["flowscope"]
    assert entry["type"] == "stdio"


def test_claude_desktop_gets_no_placeholder_it_cannot_expand() -> None:
    """Claude Desktop does not expand placeholders, so only a literal is safe."""
    entry = json.loads(install.render_config("claude-desktop"))["mcpServers"]["flowscope"]
    assert "env" not in entry, "an unexpanded placeholder would override the server default"


def test_clients_that_expand_get_their_own_placeholder_syntax() -> None:
    claude_code = json.loads(install.render_config("claude-code"))["mcpServers"]["flowscope"]
    assert claude_code["env"]["FLOWSCOPE_API_URL"] == "${FLOWSCOPE_API_URL}"

    cursor = json.loads(install.render_config("cursor"))["mcpServers"]["flowscope"]
    assert cursor["env"]["FLOWSCOPE_API_URL"] == "${env:FLOWSCOPE_API_URL}"

    gemini = json.loads(install.render_config("gemini"))["mcpServers"]["flowscope"]
    assert gemini["env"]["FLOWSCOPE_API_URL"] == "$FLOWSCOPE_API_URL"


def test_an_explicit_api_url_is_written_as_a_literal() -> None:
    entry = json.loads(
        install.render_config("claude-desktop", api_url="http://192.168.1.5:8000")
    )["mcpServers"]["flowscope"]
    assert entry["env"]["FLOWSCOPE_API_URL"] == "http://192.168.1.5:8000"


def test_an_explicit_python_interpreter_launches_the_module() -> None:
    """Lets a specific virtualenv be used instead of uvx."""
    entry = json.loads(
        install.render_config("print", python="/opt/venvs/fs/bin/python")
    )["mcpServers"]["flowscope"]
    assert entry["command"] == "/opt/venvs/fs/bin/python"
    assert entry["args"] == ["-m", "flowscope_mcp"]


def test_a_custom_package_spec_is_passed_to_uvx() -> None:
    entry = json.loads(
        install.render_config("print", package_spec="/abs/path/to/mcp")
    )["mcpServers"]["flowscope"]
    if entry["command"] == "uvx":
        assert entry["args"][:2] == ["--from", "/abs/path/to/mcp"]


# ---- writing to disk -------------------------------------------------------


def test_merge_preserves_other_servers(tmp_path: Path) -> None:
    """The generator must not clobber a user's existing configuration."""
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {"other": {"command": "other-server"}}, "unrelated": True})
    )
    target = install.TARGETS["cursor"]
    document = install.render_json_config(target)

    message = install.merge_into_json_file(config, target, document)

    written = json.loads(config.read_text())
    assert written["mcpServers"]["other"] == {"command": "other-server"}
    assert "flowscope" in written["mcpServers"]
    assert written["unrelated"] is True
    assert "Added" in message


def test_merge_creates_a_missing_file(tmp_path: Path) -> None:
    config = tmp_path / "nested" / "mcp.json"
    target = install.TARGETS["vscode"]
    install.merge_into_json_file(config, target, install.render_json_config(target))
    assert json.loads(config.read_text())["servers"]["flowscope"]["command"]


def test_merge_refuses_to_destroy_an_unparseable_file(tmp_path: Path) -> None:
    """Better to do nothing than to overwrite settings we cannot read."""
    config = tmp_path / "mcp.json"
    original = "{ this is not json"
    config.write_text(original)

    message = install.merge_into_json_file(
        config, install.TARGETS["cursor"], install.render_json_config(install.TARGETS["cursor"])
    )

    assert "not valid JSON" in message
    assert config.read_text() == original


def test_merge_updates_in_place_and_reports_it(tmp_path: Path) -> None:
    config = tmp_path / "mcp.json"
    target = install.TARGETS["cursor"]
    install.merge_into_json_file(config, target, install.render_json_config(target))
    message = install.merge_into_json_file(config, target, install.render_json_config(target))
    assert "Updated" in message
    # Still exactly one entry, not a duplicate.
    assert list(json.loads(config.read_text())["mcpServers"]) == ["flowscope"]


def test_cli_print_is_side_effect_free(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["--print", "vscode"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["servers"]["flowscope"]["command"]
    assert list(tmp_path.iterdir()) == []


def test_cli_writes_into_the_project_dir(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Project-scoped paths resolve against --project-dir, not the process cwd."""
    assert main(["cursor", "--project-dir", str(tmp_path)]) == 0
    written = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert written["mcpServers"]["flowscope"]["type"] == "stdio"


def test_cli_reports_the_intended_location_for_print_only_clients(
    capsys: pytest.CaptureFixture,
) -> None:
    assert main(["--print", "claude-code"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["mcpServers"]["flowscope"]
    assert "Intended location" in captured.err


def test_every_target_renders_without_error() -> None:
    """A typo in one client's table should fail here, not for a user."""
    for key in install.TARGETS:
        rendered = install.render_config(key)
        assert rendered.strip(), f"{key} rendered nothing"


def test_every_target_that_writes_json_has_a_config_path() -> None:
    for key, target in install.TARGETS.items():
        if key in ("codex", "print"):
            continue
        assert target.config_path, f"{key} has no config path but is web-writable"
