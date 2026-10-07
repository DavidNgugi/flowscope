# flowscope-mcp

<!-- mcp-name: io.github.DavidNgugi/flowscope -->

[![PyPI](https://img.shields.io/pypi/v/flowscope-mcp?logo=pypi&logoColor=white&label=PyPI)](https://pypi.org/project/flowscope-mcp/) [![Python](https://img.shields.io/pypi/pyversions/flowscope-mcp?logo=python&logoColor=white)](https://pypi.org/project/flowscope-mcp/) [![License: MIT](https://img.shields.io/pypi/l/flowscope-mcp)](../LICENSE) [![CI](https://img.shields.io/github/actions/workflow/status/DavidNgugi/flowscope/mcp.yml?branch=main&logo=github&label=CI)](https://github.com/DavidNgugi/flowscope/actions/workflows/mcp.yml) [![release](https://img.shields.io/github/actions/workflow/status/DavidNgugi/flowscope/release.yml?branch=main&logo=github&label=release)](https://github.com/DavidNgugi/flowscope/actions/workflows/release.yml) [![tag](https://img.shields.io/github/v/tag/DavidNgugi/flowscope?logo=git&logoColor=white&label=tag)](https://github.com/DavidNgugi/flowscope/releases) [![MCP Registry](https://img.shields.io/badge/MCP_Registry-io.github.DavidNgugi%2Fflowscope-1f6feb)](https://registry.modelcontextprotocol.io/v0.1/servers?search=flowscope)

An [MCP](https://modelcontextprotocol.io) server that exposes the
[FlowScope](../README.md) UX-analysis pipeline to any LLM client. Speaks MCP
protocol `2026-07-28` and ships two spec-portable
[Agent Skills](https://agentskills.io).

```bash
uvx flowscope-mcp        # stdio server; needs a local FlowScope backend
```

FlowScope takes a YouTube product-demo URL, downloads the video, transcribes it,
extracts and de-duplicates screenshots of each distinct screen, describes every
screen with a vision model, and synthesises a step-by-step UX flow. This package
turns that pipeline into tools an agent can call.

Ask your agent something like:

> Compare the onboarding flows in these two videos: `<url>` and `<url>`

> Tear down the checkout UX in this demo: `<url>`

## Requirements

This server is a **client** of a running FlowScope backend; it does not run the
pipeline itself. Start the backend first:

```bash
cd backend
uvicorn app.main:app --port 8000
```

The backend needs `ffmpeg`, a Python environment with `backend/requirements.txt`,
and one LLM provider key in `backend/.env`. The server's
`flowscope_health_check` tool reports exactly which of these is missing, so you
can ask your agent to check rather than guessing.

## Install

### As a Claude Code plugin

One step, and it brings the skills, the prompts, and the server registration:

```bash
/plugin marketplace add DavidNgugi/flowscope
/plugin install flowscope@flowscope
```

### From PyPI

Nothing needs a permanent install — `uvx` runs it in a throwaway environment.
`uv` is the only prerequisite (`brew install uv`):

```bash
uvx flowscope-mcp                 # starts a stdio server
uvx flowscope-mcp --version       # confirm the published version
uvx flowscope-mcp@0.1.0           # pin, when reproducibility matters
```

### From a git URL or a local checkout

```bash
uvx --from /absolute/path/to/flowscope/mcp flowscope-mcp
uvx --from "git+https://github.com/DavidNgugi/flowscope.git@main#subdirectory=mcp" flowscope-mcp
```

### Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `FLOWSCOPE_API_URL` | `http://127.0.0.1:8000` | Backend base URL. `/api` is appended if absent, so either form works. |
| `FLOWSCOPE_API_TOKEN` | _(empty)_ | Sent as `Authorization: Bearer …`. Only needed behind an authenticating proxy. |
| `FLOWSCOPE_HTTP_TIMEOUT` | `30` | Seconds for ordinary requests. |
| `FLOWSCOPE_MAX_WAIT` | `900` | Default wait budget for `flowscope_analyze_video`. |
| `FLOWSCOPE_POLL_INTERVAL` | `5` | Seconds between job-status polls. |

These are read from the process environment on each call. The MCP Python SDK v2
deliberately stopped reading `MCP_*` variables and `.env` files, so set them in
your client's server configuration instead.

## Point a harness at it

The config shapes genuinely differ — **VS Code's own file uses `servers` where
every other client uses `mcpServers`**, Codex is TOML, and Claude Desktop does
not expand `${VAR}` placeholders — so generating the file is more reliable than
copying a snippet:

```bash
uvx --from flowscope-mcp flowscope-mcp-install --help   # every client + its path
uvx --from flowscope-mcp flowscope-mcp-install print    # portable JSON, writes nothing

uvx --from flowscope-mcp flowscope-mcp-install claude-code
uvx --from flowscope-mcp flowscope-mcp-install cursor
uvx --from flowscope-mcp flowscope-mcp-install vscode
uvx --from flowscope-mcp flowscope-mcp-install codex
uvx --from flowscope-mcp flowscope-mcp-install gemini
uvx --from flowscope-mcp flowscope-mcp-install claude-desktop
```

It merges into an existing config file and leaves other servers alone.

| Harness | File | Key | Syntax |
| --- | --- | --- | --- |
| Claude Code | `.mcp.json`, `~/.claude.json` | `mcpServers` | JSON |
| Claude Desktop | `claude_desktop_config.json` | `mcpServers` | JSON |
| Cursor | `.cursor/mcp.json`, `~/.cursor/mcp.json` | `mcpServers` | JSON |
| VS Code | `.vscode/mcp.json` | **`servers`** | JSON |
| Codex CLI | `~/.codex/config.toml` | `[mcp_servers.flowscope]` | **TOML** |
| Gemini CLI | `~/.gemini/settings.json` | `mcpServers` | JSON |

VS Code additionally reads the portable `<project>/.mcp.json` shape (key
`mcpServers`), which `flowscope-mcp-install vscode-portable` writes when you want
one config shared with Claude Code and Cursor.

<details>
<summary>Remote / shared deployment</summary>

Run one server for several clients, or for a harness that cannot spawn a
process:

```bash
flowscope-mcp --transport streamable-http --host 127.0.0.1 --port 8765
```

Clients connect to `POST http://127.0.0.1:8765/mcp`:

```bash
claude mcp add --transport http flowscope http://127.0.0.1:8765/mcp
```

</details>

[docs/install.md](docs/install.md) has the full per-harness reference, the
placeholder syntax each one accepts, and the two traps that most often break an
install.

## Use it

Restart your client, open a new chat, and check the install first:

> Check whether FlowScope is ready to use.

That runs `flowscope_health_check` and names anything missing — usually ffmpeg or
an LLM provider key on the **backend** host. Then ask for what you want.

### Example prompts

**One video, fully analysed**

> Tear down the onboarding UX in this demo: `<youtube-url>`

> What does this product actually do, based on this demo? Walk me through the flow screen by screen: `<youtube-url>`

> Analyse `<youtube-url>` and tell me where a first-time user would get stuck.

**A specific question about a flow**

> In this demo, how many steps does signup take, and what does each one ask for? `<youtube-url>`

> Does this demo show any error or empty states? `<youtube-url>`

> What's above the fold on the first screen, and what's the primary call to action? `<youtube-url>`

**Several videos, compared**

> Compare the onboarding flows in these two demos. Where do they diverge, and what's the trade-off each one makes?
> - `<youtube-url-1>`
> - `<youtube-url-2>`

> I'm designing a checkout flow. What do these two demos do differently, and what should I borrow? `<url1>` `<url2>`

> Which of these three gets a new user to first value fastest, judging by the demos? `<url1>` `<url2>` `<url3>`

**Working with what already exists**

> What videos has FlowScope already analysed?

> Show me the screenshot of the pricing screen from that report.

You do not need to phrase these carefully. Analyses are cached by YouTube video
id, so a vague prompt costs a lookup rather than a re-analysis — the agent checks
the cache before spending anything.

### What happens after you press enter

Analysing a video downloads it, transcribes it, extracts and de-duplicates
screenshots, then makes **one vision call per distinct screen** plus a synthesis
call. A five-minute demo typically yields 15–40 screens. Expect **2–10 minutes**
and real money per video.

`flowscope_analyze_video` waits by default and usually returns the finished
report directly, so one prompt is normally enough. If the video needs longer than
the budget it returns `status: "running"` with a `video_id`, and the agent should
poll `flowscope_job_status`. **If it resubmits the URL instead, stop it** — that
starts duplicate work.

### Answers are grounded, or should be

Reports have three layers: `ux_insights` (the synthesis), `flow_steps` (the
ordered user path), and `frames` (the evidence — one entry per screen, with its
purpose, UI elements, and aligned narration). A good answer leads with the
insights and cites the screen behind each claim. The bundled skills enforce this,
including saying "the demo does not show this" rather than inferring.

[docs/usage.md](docs/usage.md) has the full walkthrough, prompt patterns that
produce better answers, and a troubleshooting table.

## Tools

Ten tools, split between read-only inspection and actions that cost money.

| Tool | Annotations | What it does |
| --- | --- | --- |
| `flowscope_health_check` | read-only | Backend up? ffmpeg, yt-dlp, provider key present? |
| `flowscope_list_videos` | read-only | Every stored video with its latest job status. |
| `flowscope_job_status` | read-only | Progress of one analysis job. |
| `flowscope_get_report` | read-only | The finished UX report: flow, insights, per-screen findings. |
| `flowscope_frame_image` | read-only | The actual screenshot for one screen, as an image. |
| `flowscope_compare_videos` | open-world | Cross-video common patterns, divergences, stage matrix. |
| `flowscope_analyze_video` | open-world | Download and analyse one or more URLs. Costs money. |
| `flowscope_retry_video` | idempotent | Re-queue a failed job; completed stages are reused. |
| `flowscope_reanalyze_video` | **destructive** | Discard derived results and redo analysis. |
| `flowscope_delete_video` | **destructive** | Remove a video; optionally delete media from disk. |

Plus two prompts (`flowscope_ux_teardown`, `flowscope_compare_flows`) and two
resources (`flowscope://videos`, `flowscope://videos/{video_id}/report`).

### Cost and time

Analysing a video downloads it, transcribes it, and makes **one vision call per
distinct screen** plus a synthesis call. That is minutes and real money per
video. Two things protect against waste:

- Results are cached by YouTube video id. `flowscope_list_videos` reveals
  existing analyses, and resubmitting a finished video is free and instant.
- `flowscope_analyze_video` checks the cache before starting work and only
  submits what is actually new.

The bundled skill teaches an agent to check the cache first.

## Transports

```bash
flowscope-mcp                                  # stdio (default)
flowscope-mcp --transport streamable-http --port 8765
```

Streamable HTTP serves `POST /mcp`. The SDK serves the 2026-07-28 protocol
revision and the older handshake era from the same endpoint, so legacy clients
work without extra configuration. Legacy clients create server-side sessions,
which are stored in-process — if you run more than one worker behind a load
balancer, enable sticky routing.

## Skills

Two [Agent Skills](https://agentskills.io) ship alongside the server, written to
the open specification so they install into any compatible client:

| Skill | Use it when |
| --- | --- |
| `analyzing-product-demo-ux` | You want a teardown of one recorded flow — how onboarding, signup, or checkout works, screen by screen. |
| `comparing-product-demo-ux` | You want several products compared: shared conventions, real divergences, and what is worth borrowing. |

They carry the operational knowledge the tool descriptions cannot: check the
health and the cache before spending money, read the synthesis rather than
re-deriving it from the raw transcript, say "the demo does not show this" rather
than inferring, and treat an auto-caption's product names with suspicion.

The skills and the server are **independent** — the skills are useful on a report
you already have, and the server works without them.

### Install them

**With the plugin** (Claude Code) — skills and server in one step:

```bash
/plugin marketplace add DavidNgugi/flowscope
/plugin install flowscope@flowscope
```

**The portable directory**, which most clients scan. `.agents/skills/` is the
cross-client project convention, so several harnesses pick it up from one copy:

```bash
mkdir -p .agents/skills && cp -r skills/* .agents/skills/    # this project
mkdir -p ~/.agents/skills && cp -r skills/* ~/.agents/skills/ # every project
```

**A client's own directory**, when you want it only there:

```bash
cp -r skills/* ~/.claude/skills/     # Claude Code, personal scope
cp -r skills/* ~/.codex/skills/      # Codex CLI
cp -r skills/* ~/.cursor/skills/     # Cursor
cp -r skills/* .github/skills/       # VS Code / Copilot
cp -r skills/* ~/.gemini/skills/     # Gemini CLI
```

**With the `skills` CLI**, for a team or one of ~80 agent targets:

```bash
npx skills add DavidNgugi/flowscope
```

| Client | Project | Personal |
| --- | --- | --- |
| Claude Code | `.claude/skills/` | `~/.claude/skills/` |
| Codex CLI | `.agents/skills/` | `~/.codex/skills/` |
| Cursor | `.agents/skills/`, `.cursor/skills/` | `~/.cursor/skills/` |
| VS Code / Copilot | `.github/skills/`, `.claude/skills/`, `.agents/skills/` | `~/.copilot/skills/` |
| Gemini CLI | `.gemini/skills/` or `.agents/skills/` | `~/.gemini/skills/` |
| Zed, Cline | `.agents/skills/` | `~/.agents/skills/` |

Verify a skill is portable with the official validator:

```bash
pip install "git+https://github.com/agentskills/agentskills.git#subdirectory=skills-ref"
skills-ref validate .agents/skills/analyzing-product-demo-ux
# -> Valid skill: .agents/skills/analyzing-product-demo-ux
```

Both skills declare only the six fields the open specification defines, so they
work unmodified in Claude Code, Claude Desktop, ChatGPT and Codex, Cursor,
VS Code, Gemini CLI, Zed, Goose, OpenCode and the rest. A test enforces that.

[docs/skills.md](docs/skills.md) covers every route, the compatibility aliases
each client reads, and how to use the skills without the MCP server at all.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/install.md](docs/install.md) | Installing into each harness: config shapes, placeholder syntax, remote/HTTP setup, verification |
| [docs/usage.md](docs/usage.md) | Example prompts, what happens on each call, troubleshooting, getting better answers |
| [docs/skills.md](docs/skills.md) | Installing the skills into each client, the directory matrix, and using them without the server |
| [docs/server-internals.md](docs/server-internals.md) | Design rationale: annotations, error handling, protocol era, output shapes |
| [docs/publishing.md](docs/publishing.md) | Releasing: tag or manual, versioning rules, one-time setup, recovery |
| [docs/security.md](docs/security.md) | Tool poisoning, data flow, and what a non-sandboxed server means |

## Releasing

Releases are automated and driven by version tags. `.github/workflows/release.yml`
verifies everything, publishes to PyPI with trusted publishing, publishes to the
MCP Registry, and creates a GitHub release with the wheel and sdist attached.

```bash
cd mcp
./.venv/bin/python scripts/version.py bump 0.2.0   # rewrites all five files
cd ..
git commit -am "release: v0.2.0" && git tag -a v0.2.0 -m "v0.2.0"
git push && git push --tags
```

Publishing also runs on demand — **Actions → Release MCP server** — with a
`publish` input that **defaults to off**, so the default manual run verifies and
builds without publishing anything.

Six version declarations in five files must agree, and the MCP Registry requires
the server and its package version to match. `scripts/version.py` is what keeps
them in sync, because editing them by hand is how a release fails *during*
publishing — and PyPI permanently refuses to reuse a version number, so a
half-published release cannot be retried:

```bash
python scripts/version.py show         # what every file currently says
python scripts/version.py check        # fail if any disagree, including the tag
```

See [docs/publishing.md](docs/publishing.md) for the full release process,
version-numbering rules, the one-time PyPI configuration, and what to do when a
release goes wrong.

## Development

```bash
cd mcp
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
./.venv/bin/ruff check src tests scripts
./.venv/bin/pytest -q
```

The suite is offline: tool behaviour runs against an in-memory MCP client and a
mocked HTTP transport, so it needs neither a network nor a running backend.
Separate tests spawn the real process to verify the stdio contract — that stdout
carries only protocol frames — and validate the registry, plugin, and version
metadata. The release workflow's triggers are pinned by tests too, so a future
edit cannot make publishing automatic on a branch push.

Validate the skills and manifests against the official tooling:

```bash
skills-ref validate skills/analyzing-product-demo-ux    # agentskills.io reference validator
claude plugin validate .                                # marketplace manifest
claude plugin validate ./mcp --strict                   # plugin manifest
python scripts/validate_metadata.py                     # server.json + wheel contents
```

## Licence

MIT
