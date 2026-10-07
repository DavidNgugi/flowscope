"""End-to-end tests over the real stdio transport.

The in-memory tests prove the tool logic; these prove the *process* works the
way an MCP client launches it. That matters because stdio has one failure mode
the in-memory transport cannot catch: **stdout is the wire**. A single stray
``print`` -- even a diagnostic -- corrupts the JSON-RPC stream and breaks every
client, so this is worth asserting against a real subprocess.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

pytestmark = pytest.mark.anyio

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _server_params() -> StdioServerParameters:
    """Launch the package exactly as a client would: `python -m flowscope_mcp`."""
    env = {
        **os.environ,
        "FLOWSCOPE_API_URL": "http://127.0.0.1:9",
        # Keep the child's interpreter from writing .pyc files into the tree.
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "flowscope_mcp"],
        env=env,
        cwd=str(PACKAGE_ROOT),
    )


async def test_stdio_server_completes_a_full_handshake_and_lists_tools() -> None:
    """The subprocess must serve a real client without polluting stdout."""
    async with Client(_server_params(), raise_exceptions=True) as client:
        listing = await client.list_tools()
        names = {tool.name for tool in listing.tools}
        assert "flowscope_analyze_video" in names
        assert "flowscope_get_report" in names
        assert client.server_info is not None
        assert client.server_info.name == "flowscope"


async def test_stdio_server_reports_instructions_and_prompts() -> None:
    async with Client(_server_params(), raise_exceptions=True) as client:
        assert client.instructions and "flowscope_health_check" in client.instructions
        prompts = {p.name for p in (await client.list_prompts()).prompts}
        assert prompts == {"flowscope_ux_teardown", "flowscope_compare_flows"}


async def test_stdio_server_returns_an_actionable_error_when_the_backend_is_down() -> None:
    """Port 9 is discard, so this exercises the unreachable-backend path for real."""
    async with Client(_server_params(), raise_exceptions=True) as client:
        result = await client.call_tool("flowscope_analyze_video", {"urls": ["https://youtu.be/x"]})

    assert result.is_error is True
    text = result.content[0].text
    assert "Cannot reach the FlowScope backend" in text
    assert "FLOWSCOPE_API_URL" in text


async def test_health_check_over_stdio_degrades_instead_of_crashing() -> None:
    async with Client(_server_params(), raise_exceptions=True) as client:
        result = await client.call_tool("flowscope_health_check", {})

    assert result.is_error is False
    payload = result.structured_content
    assert payload["ok"] is False
    assert payload["blocking_problems"]


async def test_stdio_stdout_carries_only_protocol_frames() -> None:
    """Spawn the process directly and assert every stdout line is valid JSON-RPC.

    Stdio has one failure mode the in-memory transport cannot catch: a stray
    ``print`` in server code writes a non-JSON line into the wire and breaks
    every client. The SDK's reader would reject it, and so does this.
    """
    import anyio

    params = _server_params()
    proc = await anyio.open_process(
        [params.command, *params.args], env=params.env, cwd=params.cwd
    )

    async def _converse() -> list[str]:
        assert proc.stdin is not None and proc.stdout is not None
        raw_lines: list[str] = []

        async def _read_until_lines(count: int) -> None:
            while len(raw_lines) < count:
                line = await proc.stdout.receive()
                if not line:
                    return
                for chunk in line.decode().splitlines():
                    if chunk.strip():
                        raw_lines.append(chunk)

        # The 2026-07-28 era is stateless: every request carries its protocol
        # version and client capabilities in `_meta` instead of negotiating an
        # `initialize` handshake. Both envelope keys are required.
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        }
        await proc.stdin.send((json.dumps(request) + "\n").encode())
        with anyio.fail_after(30):
            await _read_until_lines(1)
        return raw_lines

    try:
        raw_lines = await _converse()
    finally:
        proc.terminate()
        with anyio.move_on_after(10):
            await proc.wait()

    assert raw_lines, "the server produced no output at all"
    for line in raw_lines:
        parsed = json.loads(line)  # raises if anything non-protocol was printed
        assert parsed.get("jsonrpc") == "2.0", f"non-protocol stdout line: {line!r}"

    # The reply must be our response, not an unrelated frame.
    first = json.loads(raw_lines[0])
    assert first.get("id") == 1
    assert "result" in first
    assert "tools" in first["result"]


async def test_version_flag_does_not_start_the_server() -> None:
    """`--version` must exit immediately without opening the protocol stream."""
    import anyio

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "flowscope_mcp", "--version"],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        cwd=str(PACKAGE_ROOT),
    )

    with anyio.fail_after(30):
        result = await anyio.run_process(
            [params.command, *params.args], env=params.env, cwd=params.cwd
        )

    assert result.returncode == 0
    assert b"flowscope-mcp" in result.stdout


def test_package_is_importable_without_the_backend() -> None:
    """The server must not import any FlowScope application code.

    If it did, `uvx --from mcp flowscope-mcp` would drag in ffmpeg bindings and
    whisper, and could fail to start on a machine that only wants the client.
    """
    import subprocess

    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import flowscope_mcp, flowscope_mcp.server, sys;"
                "mods = {m.split('.')[0] for m in sys.modules};"
                "bad = mods & {'app', 'torch', 'faster_whisper', 'ctranslate2', 'yt_dlp'};"
                "print(sorted(bad))"
            ),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(PACKAGE_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]", f"unexpected heavy imports: {proc.stdout}"
