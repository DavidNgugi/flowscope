# Using FlowScope

Installation puts the tools in reach; this page is about what to actually type.
Everything here works in any MCP client, and the two bundled
[skills](skills.md) exist to make the model reach for the right steps on its own.

## Check it works first

One prompt, before anything expensive:

> Check whether FlowScope is ready to use.

The agent calls `flowscope_health_check` and reports:

```
ok: false
blocking_problems:
  - ffmpeg is not on PATH; frames cannot be extracted. Install it with `brew install ffmpeg`.
  - No LLM provider has credentials, so the screen and synthesis stages will fail.
      Set a key such as OPENAI_API_KEY or ANTHROPIC_API_KEY in backend/.env.
advice: Fix the items in blocking_problems, then re-run this check.
```

Those two problems are the usual ones, and both are on the **backend** host, not
the machine running the client. Fix them there and re-run the check.

## Example prompts

Analysing a video takes minutes and costs money, so the useful prompts are the
specific ones. All of these assume a YouTube URL.

### One video, fully analysed

> Tear down the onboarding UX in this demo: https://www.youtube.com/watch?v=…

> What does this product actually do, based on this demo? Walk me through the flow screen by screen: `<url>`

> Analyse `<url>` and tell me where a first-time user would get stuck.

### A specific question about a flow

> In this demo, how many steps does signup take, and what does each one ask for? `<url>`

> Does this demo show any error or empty states? `<url>`

> What's above the fold on the first screen, and what's the primary call to action? `<url>`

### Several videos, compared

> Compare the onboarding flows in these two demos. Where do they diverge, and what's the trade-off each one makes?
> - `<url1>`
> - `<url2>`

> Which of these three products gets a new user to first value fastest, judging by the demos? `<url1>` `<url2>` `<url3>`

> I'm designing a checkout flow. What do these two demos do differently, and what should I borrow? `<url1>` `<url2>`

### Working with what is already analysed

> What videos has FlowScope already analysed?

> Re-read the report for the Acme demo and pull out every UI element on every screen.

You do not need to phrase these carefully. Because results are cached by YouTube
video id, a vague prompt costs a lookup rather than a re-analysis — the agent
checks the cache first (the skills and the server instructions both tell it to)
and reuses an existing report for free.

## What the agent does with your prompt

Worth knowing, because it explains the wait and the cost. From a cold start:

| Step | Tool | Typical duration |
| --- | --- | --- |
| Check readiness | `flowscope_health_check` | instant |
| Check the cache | `flowscope_list_videos` | instant |
| Download, transcribe, extract screens, analyse each screen | `flowscope_analyze_video` | **2–10 minutes** |
| Poll if it exceeds the wait budget | `flowscope_job_status` | every 20–30s |
| Read the result | `flowscope_get_report` | instant |

`flowscope_analyze_video` waits by default and usually returns the finished
report directly, so a single prompt normally suffices. If the video needs longer
than the budget (900s by default), it returns `status: "running"` with a
`video_id`, and the agent should poll. **If it resubmits the URL instead, stop it
and say "poll the existing job"** — resubmitting starts duplicate work.

Analysing a video makes **one vision call per distinct screen** plus a synthesis
call. A 5-minute demo typically yields 15–40 screens. That is the cost, and it is
why the cache matters.

## Reading what comes back

Reports have three layers, and they are not interchangeable:

- **`ux_insights`** — the synthesis: considered, cross-screen observations.
- **`flow_steps`** — the reconstructed user path, already ordered.
- **`frames`** — the evidence: one entry per screen with its purpose, UX notes,
  visible UI elements, and the narration aligned to it.

A good answer leads with the synthesis and cites screens. The bundled skills
enforce this, and the reasoning is in
[server-internals.md](server-internals.md#output-shape).

When a judgement turns on visual detail — spacing, hierarchy, density — ask for
the screenshot rather than accepting a description:

> Show me the screenshot of the pricing screen from the report.

That calls `flowscope_frame_image` and returns the actual image for the model to
look at. It is one extra call per screen, so it is worth asking only when the
visual matters.

## Getting better answers

**Name the lens you want.** "Tear this down" produces a broad report; "how does
this demo handle a failed payment?" produces a focused one.

**Ask for evidence.** "Cite the screen behind each observation" makes the model
use `frames` rather than generalising from the narration.

**Ask what is missing.** "What does this demo not show?" is genuinely useful — a
demo is a curated path, and the honest gaps are often the most interesting part.

**Ask for the transcript when accuracy matters.** Product and feature names from
YouTube auto-captions are frequently wrong. `flowscope_get_report` with
`include_transcript: true` returns the full text so names can be checked, and
`flowscope_job_status` reports `transcript_source` so you know which you have.

**Reuse across sessions.** Everything is stored by YouTube video id. Ask "what
has FlowScope already analysed?" at the start of a new session instead of
re-analysing.

## When something goes wrong

| What the agent reports | What it means | Fix |
| --- | --- | --- |
| `ok: false` with `blocking_problems` | A backend prerequisite is missing | Install ffmpeg, or set an LLM key in `backend/.env` |
| `Cannot reach the FlowScope backend` | The backend is not running | `cd backend && uvicorn app.main:app --port 8000` |
| `status: "error"`, `error_message` mentions 403 or "Sign in to confirm you're not a bot" | YouTube is challenging the downloader | Configure cookies on the backend (`YTDLP_COOKIES_FROM_BROWSER` or `YTDLP_COOKIE_FILE`) |
| The agent lists no FlowScope tools | The server failed to start | Run `uvx flowscope-mcp` directly and read stderr |
| An analysis is "stuck" | The job is still running | Ask for `flowscope_job_status`; do not resubmit |

A failed analysis is cheap to retry: `flowscope_retry_video` reuses the stages
that already completed, so it does not re-download the video or re-bill the
vision calls. Changes the agent does not need to ask you about — retrying, or
reading a cached report — are safe to let it do.

Two tools are genuinely destructive and worth approving by hand:
`flowscope_reanalyze_video` discards the stored frames, screen analyses, and
synthesis; `flowscope_delete_video` removes the record, and with `delete_media`
also deletes the downloaded video and frames from disk.

## Using the skills to steer

If the skills are installed, you rarely need to name tools. They teach the model
the order of operations, the cost discipline, and the honesty rules. If you want
to invoke one explicitly, most clients expose them by name:

> Use the analyzing-product-demo-ux skill on `<url>`

> Use the comparing-product-demo-ux skill to compare these three: `<url1>` `<url2>` `<url3>`

Claude Code also exposes two prompts as slash commands:

| Command | What it does |
| --- | --- |
| `/flowscope_ux_teardown` | A structured teardown of one video |
| `/flowscope_compare_flows` | A structured comparison of several |

## Producing a shareable document

Ask the agent to write up the report as a document, a slide outline, or
Markdown. It has the structured data plus the rendered Markdown, so it can
reshape either. The backend can also produce a PDF directly, outside the MCP
surface:

```bash
curl -o teardown.pdf "http://127.0.0.1:8000/api/videos/<video_id>/export/pdf"
```

`video_id` values come from `flowscope_list_videos`, or from the report the agent
already produced.
