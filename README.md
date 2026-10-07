# FlowScope

Given one or more YouTube product-demo URLs, FlowScope downloads the video, transcribes it (captions or local Whisper), extracts and deduplicates representative screenshots of distinct screens, aligns them to the narration, and uses an LLM to produce a structured breakdown of the product's flow, screens, and UX patterns — plus cross-video comparison.

For personal competitive UX research use. Downloaded videos and derived analysis stay local.

## Use it from an LLM (MCP)

`mcp/` is an [MCP](https://modelcontextprotocol.io) server that exposes this
pipeline as tools, plus two installable skills, so any MCP-capable client can
run the analyses for you:

> Compare the onboarding flows in these two videos: `<url>` and `<url>`

Start the backend, then install into your client:

```bash
cd backend && uvicorn app.main:app --port 8000     # terminal 1

# Claude Code: one step, brings the skills and the server registration
/plugin marketplace add DavidNgugi/flowscope
/plugin install flowscope@flowscope
```

For any other harness, generate the client config — the shapes genuinely differ
(VS Code's own file uses `servers` where everyone else uses `mcpServers`, and
Codex is TOML):

```bash
uvx --from flowscope-mcp flowscope-mcp-install --help   # every client and its path
uvx --from flowscope-mcp flowscope-mcp-install cursor   # or vscode, codex, gemini
```

(`flowscope-mcp-install` is a console script inside the `flowscope-mcp`
distribution, hence `--from`. If you installed the package locally, the bare
`flowscope-mcp-install` works too.)

The two bundled skills install separately, into any Agent Skills-compatible
client. `.agents/skills/` is the cross-client convention, so one copy serves most
harnesses:

```bash
mkdir -p .agents/skills && cp -r mcp/skills/* .agents/skills/
npx skills add DavidNgugi/flowscope        # or via the skills CLI
```

Ask the agent to run `flowscope_health_check` — it reports whether the backend,
ffmpeg, yt-dlp, and an LLM provider key are all in place, so a missing
prerequisite is a clear message rather than a failed ten-minute job.

Then just describe what you want:

> Tear down the onboarding UX in this demo: `<youtube-url>`

> Compare the onboarding flows in these two demos. Where do they diverge, and what's the trade-off each one makes?
> - `<youtube-url-1>`
> - `<youtube-url-2>`

> In this demo, how many steps does signup take, and what does each one ask for? `<youtube-url>`

Analysing a video takes **2–10 minutes** and makes one vision call per distinct
screen, so it costs real money. Results are cached by YouTube video id — asking
"what has FlowScope already analysed?" is free, and re-reading a report costs
nothing. The bundled skills teach the agent to check the cache before spending.

The server is a stateless client of the backend's REST API and imports none of
the application code, so it installs without ffmpeg bindings or Whisper.

See [mcp/README.md](mcp/README.md) for the tool list,
[mcp/docs/install.md](mcp/docs/install.md) for the per-harness install reference,
[mcp/docs/usage.md](mcp/docs/usage.md) for the full set of example prompts and
troubleshooting, [mcp/docs/skills.md](mcp/docs/skills.md) for the skills matrix,
and [mcp/docs/publishing.md](mcp/docs/publishing.md) and
[mcp/docs/security.md](mcp/docs/security.md) for the details.

The MCP package releases on its own cadence, from version tags. Bumping is a
single command because six declarations in five files must agree:

```bash
cd mcp && ./.venv/bin/python scripts/version.py bump 0.2.0
```

Then commit, tag `v0.2.0`, and push the tag — CI verifies everything, publishes
to PyPI, publishes to the MCP Registry, and attaches the wheel to the GitHub
release.

## Setup

```bash
brew install ffmpeg

cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in a provider key (see Models below)
uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173.

## Models

The LLM layer is provider-neutral: adding a vendor is configuration, not code.
Four providers are built in — `anthropic`, `openai`, `deepseek`, and
`openai_compatible` (any vendor or local server that speaks the OpenAI
chat-completions format, e.g. xAI, Mistral, Groq, Together, OpenRouter, vLLM,
Ollama).

Selection is by **purpose**, so frames and text can run on different models:

```bash
LLM_PROVIDER=openai            # default provider
LLM_MODEL=gpt-5-mini           # default model
# Optional per-purpose overrides (each falls back to the default):
# LLM_VISION_PROVIDER / LLM_VISION_MODEL          frame analysis (needs vision)
# LLM_SYNTHESIS_PROVIDER / LLM_SYNTHESIS_MODEL    one video's flow
# LLM_COMPARISON_PROVIDER / LLM_COMPARISON_MODEL  cross-video comparison
```

Per-provider credentials: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`DEEPSEEK_API_KEY`, or `OPENAI_COMPATIBLE_API_KEY` + `OPENAI_COMPATIBLE_BASE_URL`.
A provider with no key is simply unavailable; selecting it produces a clear
configuration error naming the variable to set.

Notes that save time:

- **Vision is required for the frame stage.** `deepseek-flash` accepts images;
  `deepseek-v4-pro` does not, and a text-only model produces an explicit error
  rather than silently analysing nothing.
- **Reasoning models** (`gpt-5.x`, `o-series`) spend part of the completion
  budget on hidden reasoning, so the adapter applies a floor and
  `LLM_REASONING_EFFORT` (default `low`). A budget sized for the visible answer
  alone fails with `finish_reason=length` and no tool call.
- **Claude Sonnet 5.5 rejected forced tool use**, so those models are routed to
  automatic tool choice; other Claude models keep forced tool use.
- **Cost is recorded per call** with the rate snapshotted onto the row. Models
  without a published rate in `app/llm/catalog.py` record tokens with a null
  cost rather than a guess.

### Analysing frames that already exist

Downloading and frame extraction are separate from the paid stages, so a run that
stopped at the LLM step — or a re-run against a different model — costs nothing
to repeat:

```bash
python scripts/evaluate_existing_frames.py --data-dir ./data          # resumable
python scripts/export_analysis.py --data-dir ./data --out ./export    # plain files
```

## Docker

```bash
docker compose build          # rebuild after any change under backend/app
docker compose up -d          # backend :8000, frontend :5180
open http://localhost:5180
```

`backend/data` is **bind-mounted** to `/app/data`, so the database, media and frame
files live on the host: they survive rebuilds, can be inspected directly, and are
shared with a local `uvicorn` run. (The older `backend_data` named volume is still
declared but unused, so its contents are not lost.)

Because the app copy is baked into the image at build time, **a code change needs
`docker compose build`** — `up -d` alone reuses the old image.

Data portability: frame paths are stored absolute, but the API resolves them by
their path below the last `media/` segment, so the same database works from the
host and from inside the container.

## YouTube authentication

YouTube may challenge yt-dlp with `Sign in to confirm you're not a bot`, even
for public videos. FlowScope can reuse an authenticated YouTube session in one
of two ways (configure only one):

- Local development: set `YTDLP_COOKIES_FROM_BROWSER=chrome` in
  `backend/.env` (also supported: `firefox`, `safari`, and other browsers
  recognized by yt-dlp), then restart the backend.
- Docker/server: export a Netscape-format `youtube-cookies.txt`, mount it into
  the backend container, and set `YTDLP_COOKIE_FILE` to its container path.

For Docker, add a read-only mount under the backend service, for example:

```yaml
volumes:
  - backend_data:/app/data
  - ./secrets/youtube-cookies.txt:/app/secrets/youtube-cookies.txt
```

Then set `YTDLP_COOKIE_FILE=/app/secrets/youtube-cookies.txt` in
`backend/.env`. Cookie files contain account credentials; keep them out of
source control and refresh the export if YouTube invalidates the session.
