"""The advertised tool surface: names, annotations and output schemas.

These are contract tests. The annotation defaults in the MCP spec are
fail-safe in the *unsafe* direction -- an unannotated tool reads as
``destructiveHint: true`` and ``openWorldHint: true`` -- so a tool that loses
its annotations is a safety regression, not a cosmetic one.
"""

from __future__ import annotations

import pytest
from mcp import Client

from .conftest import MockBackend

pytestmark = pytest.mark.anyio

READ_ONLY_TOOLS = {
    "flowscope_health_check",
    "flowscope_list_videos",
    "flowscope_job_status",
    "flowscope_get_report",
    "flowscope_frame_image",
}

DESTRUCTIVE_TOOLS = {"flowscope_reanalyze_video", "flowscope_delete_video"}

EXPECTED_TOOLS = READ_ONLY_TOOLS | DESTRUCTIVE_TOOLS | {
    "flowscope_analyze_video",
    "flowscope_compare_videos",
    "flowscope_retry_video",
}


async def _tools_by_name(client: Client) -> dict:
    """The client returns a ListToolsResult, not a bare list."""
    listing = await client.list_tools()
    return {tool.name: tool for tool in listing.tools}


async def test_exposes_exactly_the_expected_tools(client: Client, backend: MockBackend) -> None:
    tools = await _tools_by_name(client)
    assert set(tools) == EXPECTED_TOOLS


async def test_every_tool_has_a_substantial_description(client: Client, backend: MockBackend) -> None:
    """Descriptions are the model's only documentation; thin ones waste a call."""
    for name, tool in (await _tools_by_name(client)).items():
        assert tool.description, f"{name} has no description"
        assert len(tool.description) > 80, f"{name}'s description is too terse to guide a model"


async def test_read_only_tools_are_annotated_read_only(client: Client, backend: MockBackend) -> None:
    tools = await _tools_by_name(client)
    for name in READ_ONLY_TOOLS:
        annotations = tools[name].annotations
        assert annotations is not None, f"{name} has no annotations"
        assert annotations.read_only_hint is True, f"{name} should be read-only"
        assert annotations.destructive_hint is False, f"{name} must not claim to be destructive"


async def test_destructive_tools_are_annotated_destructive(client: Client, backend: MockBackend) -> None:
    tools = await _tools_by_name(client)
    for name in DESTRUCTIVE_TOOLS:
        annotations = tools[name].annotations
        assert annotations is not None, f"{name} has no annotations"
        assert annotations.destructive_hint is True, f"{name} destroys data; clients must warn"
        assert annotations.read_only_hint is False


async def test_no_unannotated_tool_is_left_destructive_by_default(
    client: Client, backend: MockBackend
) -> None:
    """Every write tool must state its hints explicitly.

    Omitting them inherits the spec defaults, which claim a tool is destructive
    and open-world. That is a false alarm for this server's non-destructive
    writes, which trains users to click through warnings.
    """
    tools = await _tools_by_name(client)
    for name, tool in tools.items():
        if name in READ_ONLY_TOOLS:
            continue
        annotations = tool.annotations
        assert annotations is not None, f"{name} has no annotations"
        # The two write hints are only meaningful for non-read-only tools.
        assert annotations.destructive_hint is not None, f"{name} does not state destructive_hint"
        assert annotations.idempotent_hint is not None, f"{name} does not state idempotent_hint"


async def test_tool_names_are_within_the_spec_charset(client: Client, backend: MockBackend) -> None:
    """Names must be 1-128 chars of [A-Za-z0-9_.-] and prefixed to avoid collisions."""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")
    for name in await _tools_by_name(client):
        assert 1 <= len(name) <= 128, f"{name} is outside the allowed length"
        assert set(name) <= allowed, f"{name} contains disallowed characters"
        # A bare `get_report` would collide with any other server's tool.
        assert name.startswith("flowscope_"), f"{name} is not namespaced"


async def test_write_tools_declare_an_output_schema(client: Client, backend: MockBackend) -> None:
    """Structured output lets a caller act on values rather than parse prose."""
    tools = await _tools_by_name(client)
    for name in EXPECTED_TOOLS - {"flowscope_frame_image"}:
        assert tools[name].output_schema, f"{name} should declare an outputSchema"


async def test_tool_listing_is_deterministic(client: Client, backend: MockBackend) -> None:
    """Deterministic ordering enables client-side caching and prompt-cache hits."""
    first = [tool.name for tool in (await client.list_tools()).tools]
    second = [tool.name for tool in (await client.list_tools()).tools]
    assert first == second
    assert set(first) == EXPECTED_TOOLS


async def test_resources_and_prompts_are_exposed(client: Client, backend: MockBackend) -> None:
    resources = {str(r.uri) for r in (await client.list_resources()).resources}
    assert resources == {"flowscope://videos"}

    templates = {
        t.uri_template for t in (await client.list_resource_templates()).resource_templates
    }
    assert templates == {"flowscope://videos/{video_id}/report"}

    prompts = {p.name for p in (await client.list_prompts()).prompts}
    assert prompts == {"flowscope_ux_teardown", "flowscope_compare_flows"}


async def test_server_instructions_teach_the_cache_first_workflow(client: Client, backend: MockBackend) -> None:
    """Instructions are where the caching/cost discipline is established."""
    instructions = client.instructions or ""
    assert "flowscope_health_check" in instructions
    assert "flowscope_list_videos" in instructions
    # The cost and time warnings are the whole reason the ordering matters.
    assert "cost" in instructions.lower()
