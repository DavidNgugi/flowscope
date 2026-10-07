# Installing the MCP server

The server is a **client** of the FlowScope backend. It does not analyse videos
itself, so two things must be true before a client can use it:

1. The FlowScope backend is running and reachable.
2. `ffmpeg`, `yt-dlp`, and one LLM provider key are available **on the backend
   host** (not on the machine running the MCP client).

`flowscope_health_check` reports exactly which of these is missing, so the
fastest way to diagnose an install is to ask the agent to run it.

## 1. Start the backend

```bash
cd backend
uvicorn app.main:app --port 8000
```

Confirm it is up:

```bash
curl -s http://127.0.0.1:8000/api/health
```

## 2. Install the server

Three sources, in order of preference.

### As a Claude Code plugin

Brings the skills, the prompts, and the server registration in one step. No
separate configuration needed.

```bash
/plugin marketplace add DavidNgugi/flowscope
/plugin install flowscope@flowscope
```

### From PyPI (any MCP client)

Nothing needs a permanent install: `uvx` runs it in a throwaway environment.
`uv` is the only prerequisite (`brew install uv`).

```bash
uvx flowscope-mcp                 # starts a stdio server
uvx flowscope-mcp --version       # confirm the published version
```

Pin a version when reproducibility matters:

```bash
uvx flowscope-mcp@0.1.0
```

### From a git URL or a local checkout

For an unreleased commit, a fork, or development. Every `--package` value below
is passed straight to `uvx --from`, so a path or a git URL both work.

```bash
# A local checkout
uvx --from /absolute/path/to/flowscope/mcp flowscope-mcp

# A specific git ref
uvx --from "git+https://github.com/DavidNgugi/flowscope.git@main#subdirectory=mcp" flowscope-mcp
```

<details>
<summary>Installing with pip instead of uvx</summary>

```bash
pip install flowscope-mcp
flowscope-mcp
```

This gives a second console script, `flowscope-mcp-install`, used in the next
step. It is also available under `uvx`:

```bash
uvx --from flowscope-mcp flowscope-mcp-install --help
```

</details>

## 3. Point your client at it

The config shapes genuinely differ: **VS Code's own file uses `servers` where
every other client uses `mcpServers`**, Codex is TOML, and Claude Desktop does
not expand `${VAR}` placeholders. Generating the file is therefore more reliable
than copying a snippet.

```bash
uvx --from flowscope-mcp flowscope-mcp-install --help   # show every client + path
uvx --from flowscope-mcp flowscope-mcp-install print    # portable JSON, writes nothing

uvx --from flowscope-mcp flowscope-mcp-install claude-code      # writes .mcp.json
uvx --from flowscope-mcp flowscope-mcp-install cursor
uvx --from flowscope-mcp flowscope-mcp-install vscode           # .vscode/mcp.json, key "servers"
uvx --from flowscope-mcp flowscope-mcp-install vscode-portable  # portable .mcp.json
uvx --from flowscope-mcp flowscope-mcp-install codex            # appends TOML
uvx --from flowscope-mcp flowscope-mcp-install gemini
uvx --from flowscope-mcp flowscope-mcp-install claude-desktop
```

It merges into an existing config file and leaves other servers alone. If the
package is installed locally, `flowscope-mcp-install` works as a bare command.

| Flag | Why |
| --- | --- |
| `--print` | Show the configuration without writing it. |
| `--api-url URL` | Point at a backend that is not on the default `http://127.0.0.1:8000`. |
| `--python /path/to/python` | Launch `python -m flowscope_mcp` instead of `uvx`. Needed when the package is in a specific virtualenv and not on PyPI. |
| `--package PATH_OR_GIT_URL` | Run a local checkout or an unreleased ref. |
| `--project-dir DIR` | Where project-scoped config paths resolve from. |

### Registering by hand

<details>
<summary>Per-client reference</summary>

Every entry below launches the same server; only the spelling differs.

**Claude Code** — the CLI writes the file for you:

```bash
claude mcp add flowscope -- uvx flowscope-mcp
claude mcp add --scope project flowscope -- uvx flowscope-mcp   # writes .mcp.json
claude mcp list
```

**Claude Desktop** — `~/Library/Application Support/Claude/claude_desktop_config.json`
on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows. There is no
placeholder expansion here, so the URL must be literal or omitted:

```json
{
  "mcpServers": {
    "flowscope": {
      "command": "uvx",
      "args": ["flowscope-mcp"],
      "env": { "FLOWSCOPE_API_URL": "http://127.0.0.1:8000" }
    }
  }
}
```

**Cursor** — `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global).
Cursor expands `${env:NAME}`:

```json
{
  "mcpServers": {
    "flowscope": {
      "type": "stdio",
      "command": "uvx",
      "args": ["flowscope-mcp"],
      "env": { "FLOWSCOPE_API_URL": "${env:FLOWSCOPE_API_URL}" }
    }
  }
}
```

**VS Code** — `.vscode/mcp.json`. Note the key is **`servers`**, not
`mcpServers`. VS Code's portable `.mcp.json` uses `mcpServers`; this file does not:

```json
{
  "servers": {
    "flowscope": {
      "type": "stdio",
      "command": "uvx",
      "args": ["flowscope-mcp"]
    }
  }
}
```

VS Code also reads the portable `<project>/.mcp.json` shape, which uses
`mcpServers` like every other client. Use `flowscope-mcp-install vscode-portable`
for that file if you want one config shared with Claude Code and Cursor:

**Codex CLI** — `~/.codex/config.toml`, which is TOML:

```toml
[mcp_servers.flowscope]
command = "uvx"
args = ["flowscope-mcp"]
startup_timeout_sec = 20
tool_timeout_sec = 600
```

**Gemini CLI** — `~/.gemini/settings.json`. Gemini redacts inherited environment
variables, so anything the server needs must be declared explicitly:

```json
{
  "mcpServers": {
    "flowscope": {
      "command": "uvx",
      "args": ["flowscope-mcp"],
      "env": { "FLOWSCOPE_API_URL": "$FLOWSCOPE_API_URL" }
    }
  }
}
```

Avoid underscores in the server name here: Gemini parses tool names at the first
underscore after `mcp_`, so `flowscope` is a safe alias.

</details>

### The differences that break installs

| Client | File | Top-level key | Syntax | Env placeholder |
| --- | --- | --- | --- | --- |
| Claude Code | `.mcp.json` (project) or `~/.claude.json` | `mcpServers` | JSON | `${VAR}` |
| Claude Desktop | see above | `mcpServers` | JSON | **none** — literal only |
| Cursor | `.cursor/mcp.json` or `~/.cursor/mcp.json` | `mcpServers` | JSON | `${env:VAR}` |
| VS Code (native) | `.vscode/mcp.json` | **`servers`** | JSON | `${input:...}` + `inputs` |
| VS Code (portable) | `.mcp.json` | `mcpServers` | JSON | `${VAR}` |
| Codex CLI | `~/.codex/config.toml` | `[mcp_servers.<id>]` | **TOML** | n/a |
| Gemini CLI | `~/.gemini/settings.json` | `mcpServers` | JSON | `$VAR` |

Two traps worth calling out:

- **Claude Desktop does not expand placeholders.** A literal
  `${FLOWSCOPE_API_URL}` there is passed through as that twenty-character string,
  which overrides the server's real default with nonsense. The generator emits no
  `env` block for Claude Desktop unless `--api-url` supplies a literal.
- **A `url` with no `type` is a hard error in Claude Code**, not a stdio entry.
  Remote entries need an explicit transport type.

## Remote: one server for several clients

For a shared or hosted deployment, run the server once over Streamable HTTP and
point clients at the URL. This is also how harnesses that cannot spawn a process
consume it.

```bash
flowscope-mcp --transport streamable-http --host 127.0.0.1 --port 8765
```

The endpoint is `POST http://127.0.0.1:8765/mcp`. One endpoint serves both the
current protocol revision and the older handshake era, so legacy clients work
without extra configuration. Older clients create server-side sessions, held in
process memory — behind a load balancer with more than one worker, enable sticky
routing.

<details>
<summary>Remote entries per client</summary>

**Claude Code**

```bash
claude mcp add --transport http flowscope http://127.0.0.1:8765/mcp
```

**VS Code**

```json
{
  "servers": {
    "flowscope": { "type": "http", "url": "http://127.0.0.1:8765/mcp" }
  }
}
```

**OpenAI (Responses API)** — declared in the request body, not a file:

```json
{
  "tools": [
    {
      "type": "mcp",
      "server_label": "flowscope",
      "server_url": "http://127.0.0.1:8765/mcp",
      "require_approval": "always"
    }
  ]
}
```

`require_approval` is worth setting: `flowscope_analyze_video` spends money, and
the approval prompt is the cheapest guard against a runaway loop.

</details>

## Environment variables

Read from the process environment on each call. The MCP Python SDK v2
deliberately stopped reading `MCP_*` variables and `.env` files, so declare them
in the client's `env` block; a registry-driven client prompts for the ones
declared in `server.json`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `FLOWSCOPE_API_URL` | `http://127.0.0.1:8000` | Backend base URL. `/api` is appended if absent, so either form works. |
| `FLOWSCOPE_API_TOKEN` | _(empty)_ | Sent as `Authorization: Bearer …`. Only for a backend behind an authenticating proxy. |
| `FLOWSCOPE_HTTP_TIMEOUT` | `30` | Seconds for ordinary requests. |
| `FLOWSCOPE_MAX_WAIT` | `900` | Default wait budget for `flowscope_analyze_video`, in seconds. |
| `FLOWSCOPE_POLL_INTERVAL` | `5` | Seconds between job-status polls. |

## Verifying

Ask the agent to run `flowscope_health_check`. A healthy install returns
`ok: true`; otherwise `blocking_problems` names the cause.

Independently of the client, drive the server with the official inspector:

```bash
npx -y @modelcontextprotocol/inspector@latest uvx flowscope-mcp
```

Set `FLOWSCOPE_API_URL` first if the backend is not on the default port. The
inspector shows the ten tools, both prompts, the resources, and every annotation.

Two failure signatures worth recognising:

| Symptom | Cause |
| --- | --- |
| The client starts but lists no tools | The server exited at startup. Run the `uvx` command directly to see the error on stderr. |
| `Cannot reach the FlowScope backend` | The backend is not running, or `FLOWSCOPE_API_URL` points at the wrong port. |

## Next

Now that it is installed, see [usage.md](usage.md) for example prompts, what
happens on each call, and how to get better answers.

The skills install separately, and into any Agent Skills-compatible client. See
[skills.md](skills.md) for the per-client directory matrix and the plugin route.
