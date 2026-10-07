---
name: analyzing-product-demo-ux
description: Produces a grounded UX teardown of a product walkthrough or demo video by analysing it with the FlowScope MCP server — reconstructing the user flow, naming each distinct screen, and citing the narration behind each finding. Use when asked how a product's onboarding, signup, checkout, activation, or any recorded flow works; to tear down or critique a product demo video; to document screens and UI patterns from a walkthrough; or when someone shares a YouTube demo link and asks what the product does or how it works. Also use when a UX, competitive-research, or design-review question is about a recorded product rather than a live app.
license: MIT
---

# Analysing a product demo into a UX flow

FlowScope turns a demo video into a structured report: the ordered user flow,
synthesised UX insights, and a per-screen analysis with the visible UI elements
and the narration aligned to each screen. Your job is to read that report
properly and turn it into an answer.

## Order of operations

Follow this order every time. The first two steps exist because analysing a
video costs money and takes minutes.

1. **Check readiness.** Call `flowscope_health_check`. If `ok` is false, report
   the `blocking_problems` and stop — do not submit a video that cannot be
   analysed. A missing ffmpeg, yt-dlp, or LLM provider key is a configuration
   problem for the user, not something to work around.
2. **Check what is already analysed.** Call `flowscope_list_videos`. Analyses
   are cached by YouTube video id. If the video is already listed with
   `has_report: true`, go straight to step 4. Resubmitting costs money for a
   result that already exists.
3. **Analyse it.** Call `flowscope_analyze_video` with the URL. It waits by
   default and returns the finished report. If it returns `status: "running"`,
   the analysis needs longer than the wait budget: poll `flowscope_job_status`
   with the returned `video_id` every 20–30 seconds. **Never resubmit the same
   URL while a job is running** — that creates duplicate work.
4. **Read the report.** Call `flowscope_get_report` with the `video_id`.

Only pass `force: true` to `flowscope_analyze_video` when the user has asked
for a fresh analysis of a video that is already stored.

## Reading the report

The report has three layers, and they are not interchangeable:

- **`ux_insights`** is the synthesis: considered, cross-screen observations.
  This is the product of the analysis. Lead with it.
- **`flow_steps`** is the reconstructed path a user takes, already ordered.
  Use it as the spine of the teardown.
- **`frames`** is the evidence: one entry per distinct screen, with
  `screen_name`, `purpose`, `ux_notes`, `ui_elements`, and `narration_excerpt`.

Write the teardown from `ux_insights` and `flow_steps`, and use `frames` to
support and illustrate each claim. **Do not re-derive observations from
`narration_excerpt` alone** — that is raw transcript, and treating it as the
analysis produces a summary of what was said rather than of what the product
does. The synthesis has already weighed the screens against each other.

### The shape of a teardown

1. **What the product does** — one paragraph, and only from what the video shows.
2. **The flow** — the numbered steps, naming each screen.
3. **Screen-by-screen** — for each screen: purpose, controls present, and the
   narration that explains it. Keep the video's ordering.
4. **Strengths** — specific and evidence-backed; name the screen each one comes from.
5. **Friction and open questions** — where the demo skips a step, or where a
   first-time user would hesitate.

## When the written report is not enough

`ux_notes` is a description, not a picture. If a judgement turns on visual
detail — spacing, hierarchy, density, contrast, how a layout actually reads —
fetch the screenshot with `flowscope_frame_image`, passing the `video_id` and
the `frame_id` from the report's `frames`. Look at it before asserting anything
about visual design.

Do this sparingly. It is one extra call per screen, so reserve it for the
screens that carry the claim you are making.

## Honesty rules

- Ground every claim in the report. If the video does not show duplicate-email
  handling, do not describe the error state.
- Say "the demo does not show this" rather than inferring the likely behaviour.
  Inference presented as observation is the main failure mode of this task.
- A demo shows a curated path. Do not generalise from it to the product's full
  behaviour without saying that is what you are doing.
- `transcript_source` tells you the provenance: `official_caption` and
  `auto_caption` come from YouTube and can be wrong on product names;
  `whisper` was transcribed locally. Treat product and feature names from an
  auto-caption with suspicion.

## Handling failures

- **`status: "error"`** — read `error_message` from `flowscope_job_status`, then
  call `flowscope_retry_video`. Completed stages are reused, so a retry does not
  re-download or re-bill them.
- **A `403` or "Sign in to confirm you're not a bot" error** — YouTube is
  challenging the downloader. This needs cookie configuration on the backend
  (`YTDLP_COOKIES_FROM_BROWSER` or `YTDLP_COOKIE_FILE`); report it rather than retrying.
- **"Cannot reach the FlowScope backend"** — the backend is not running. Ask the
  user to start it; do not attempt to run the pipeline yourself.
- **A report with an empty `frames` list** — the video is still being processed.
  Check `flowscope_job_status` rather than assuming the analysis failed.

## Reporting cost

Analysing a video makes one vision call per distinct screen plus a synthesis
call. If you analyse several videos, say so before starting, and mention that
already-cached videos are free. Do not quietly spend money on the user's behalf.
