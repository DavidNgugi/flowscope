# The FlowScope MCP server

How the server is built and why. Read this before changing a tool signature or
an annotation.

## Shape

```
MCP client  ──stdio or streamable HTTP──▶  flowscope-mcp  ──HTTP/JSON──▶  FlowScope backend
```

The server is a thin, stateless adapter. It imports no FlowScope application
code, so installing it never pulls in ffmpeg bindings, Whisper, or torch, and it
can run from `uvx` in a virtualenv unrelated to the backend's. That property is
asserted by a test.

| Module | Responsibility |
| --- | --- |
| `settings.py` | Environment-only configuration, read per call |
| `client.py` | The only module that knows the backend's HTTP shape |
| `server.py` | Tool, resource and prompt definitions plus rendering |
| `install.py` / `install_cli.py` | Per-client configuration generation |
| `__main__.py` | Transport selection and the console entry point |

## Protocol era

The server targets MCP **2026-07-28** through the official Python SDK (`mcp>=2.3`).
That revision is a breaking rewrite of the earlier handshake era:

- There is no `initialize` handshake and no `Mcp-Session-Id`. Every request
  carries its protocol version and client capabilities in `_meta`, and
  `server/discover` replaces `initialize`.
- HTTP is POST-only against a single `/mcp` endpoint. `ping`,
  `logging/setLevel`, and SSE resumability are gone.
- Roots, sampling, and MCP-level logging are deprecated; log to stderr instead.

The SDK serves both eras from one server object with no configuration, so old
clients keep working. That is free, and it is why the server does not pin a
protocol version. Two consequences for deployment:

- An older client creates a server-side session, held **in process memory**.
  Behind a load balancer with more than one worker, sticky routing is required
  or clients get `404 Session not found`.
- In `mcp` 2.x the class is `MCPServer` (`mcp.server.mcpserver`). `FastMCP` was
  renamed, and `mcp.server.fastmcp` is now a stub that raises on import. Any
  tutorial written for the 1.x era will not run.

## Annotations are not optional

The spec's annotation defaults are fail-safe in the **unsafe** direction: an
unannotated tool reads as `destructiveHint: true` and `openWorldHint: true`.
Omitting them is therefore not neutral — it tells a client that a read-only
lookup may destroy data, which trains users to click through warnings until they
stop reading them.

Every tool therefore carries explicit hints, and a test asserts it:

| Tool | readOnly | destructive | idempotent | openWorld |
| --- | --- | --- | --- | --- |
| `flowscope_health_check` | ✓ | | ✓ | |
| `flowscope_list_videos` | ✓ | | ✓ | |
| `flowscope_job_status` | ✓ | | ✓ | |
| `flowscope_get_report` | ✓ | | ✓ | |
| `flowscope_frame_image` | ✓ | | ✓ | |
| `flowscope_compare_videos` | | | | ✓ |
| `flowscope_analyze_video` | | | | ✓ |
| `flowscope_retry_video` | | | ✓ | ✓ |
| `flowscope_reanalyze_video` | | ✓ | ✓ | ✓ |
| `flowscope_delete_video` | | ✓ | ✓ | ✓ |

Annotations are **hints, not enforcement**. The spec says clients must treat
them as untrusted for servers they do not trust, so nothing here relies on a
client honouring them.

## Errors: raise, never return

Two kinds of failure, two behaviours:

- **Expected failures** — backend unreachable, video not found, a job that
  failed, an empty URL list. These are raised as `ToolError`. The SDK turns that
  into an `is_error: true` result whose text the model reads and can act on.
- **Unexpected bugs** — anything else. The SDK logs the traceback and shows the
  model only `Error executing tool <name>`, so no internals leak.

The trap this avoids: **returning** an error string produces a result with
`is_error: false`, which reads to the model as a successful answer whose content
happens to be an error message. Always raise.

Three places deliberately do *not* raise, because the failure is the answer:

- `flowscope_health_check` returns `ok: false` with `blocking_problems`. "The
  backend is down" is precisely what that tool was asked.
- `flowscope_job_status` returns `status: "unknown"` for an unrecognised video,
  so a polling caller can tell "no such job" from "backend broken".
- `flowscope_get_report` returns an empty report with a note when the video
  exists but has not been analysed yet, so a partial result is still usable.

## Cost and time are the design constraints

Analysing a video downloads it, transcribes it, and makes **one vision call per
distinct screen** plus a synthesis call. Minutes and real money. The design
responds in three ways:

1. **Cache-first tooling.** `flowscope_list_videos` exists so a caller can find
   an existing analysis instead of paying for it again, and `analyze_video`
   reports `reused_count` separately from `started_count`.
2. **Bounded waiting.** `flowscope_analyze_video` polls until the job finishes
   or a budget expires (default 900s, split across submitted videos), then
   returns `status: "running"` with the video ids and an instruction not to
   resubmit. A tool call that can hang forever is worse than one that hands back
   a handle.
3. **Server instructions.** The server's `instructions` field establishes the
   correct order — health, list, analyze, report — before the model has chosen
   anything. It is the cheapest place to prevent a wasted run.

## Output shape

Every tool returns a Pydantic model, so each has an `outputSchema` and a
`structuredContent` payload. A BaseModel serialises without the
`{"result": ...}` wrapper that scalars, lists, and unions get; avoiding unions
in return types was a deliberate choice for that reason.

Reports return **both** a structured payload and a rendered Markdown view. The
structured form is what a program consumes; the Markdown is what the model reads
without spending tokens on JSON punctuation.

`flowscope_get_report` defaults to `max_frames: 24` and samples evenly across
the flow rather than truncating, because the first 24 screens of a long demo are
far less useful than a spread. When it samples, `truncated` is true and `note`
says so.

`flowscope_frame_image` is the one tool returning raw content blocks — a text
caption plus a base64 image — because it exists to be *looked at*.

## Why the names are prefixed

MCP clients merge the tools from every connected server into one flat namespace.
A bare `get_report` would collide the moment a second server was installed. The
`flowscope_` prefix is not decoration; names must be 1–128 characters of
`[A-Za-z0-9_.-]`.

## Configuration

Environment only, read on each call rather than cached at import. The SDK v2
stopped reading `MCP_*` variables and `.env` files, so configuration is this
server's concern; reading it per call also means a client that sets the variable
in its own `env` block is honoured without any reload.

`Settings.api_root` normalises the base URL so that both `http://host:8000` and
`http://host:8000/api` work, because the second form is what someone copies out
of a browser.

## Testing

`tests/` runs entirely offline against two seams:

- **`httpx2.MockTransport`** injected into `build_server(transport=...)`, which
  is why that parameter exists. A `MockBackend` serves the handful of REST
  endpoints the client uses and records every request, so a test can assert that
  a cached video was *not* resubmitted.
- **The SDK's in-memory client** (`Client(server)`), which connects to the
  server object directly with no subprocess or port.

`test_stdio_transport.py` additionally spawns the real process. That is not
redundant: stdio has one failure mode no in-memory test can catch, because
**stdout is the wire** — a single stray `print` corrupts the stream. The test
reads raw stdout and asserts every line parses as JSON-RPC.

The tests use **anyio's** pytest plugin rather than pytest-asyncio. pytest-asyncio
runs an async fixture in a different task from the test body, which makes the
anyio cancel scope inside the SDK's transport fail at teardown with
"Attempted to exit cancel scope in a different task than it was entered in".

## Adding a tool

1. Decide the annotations first, from the table above. An unannotated tool is a
   destructive one as far as a client is concerned.
2. Return a Pydantic model, not a dict or a union.
3. Raise `ToolError` for anything the model could fix or should know about.
4. Write the description for the model: what it does, when to use it, and what
   it costs. That text is the only documentation the model gets.
5. If it spends money or writes, add it to `DESTRUCTIVE_TOOLS` or the write set
   in `tests/test_tool_surface.py` so the annotation contract stays enforced.
