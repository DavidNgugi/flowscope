---
name: comparing-product-demo-ux
description: Compares the UX of two or more recorded product demos side by side using the FlowScope MCP server, surfacing shared conventions, meaningful divergences, and transferable design ideas. Use when asked to compare competitor onboarding, signup, checkout, or activation flows; to benchmark one product's demo against another; to find where products diverge on the same task; or when several demo links are given together and the question is how they differ. Also use for competitive UX research, teardown round-ups, and pattern surveys across recorded walkthroughs.
license: MIT
---

# Comparing product demos

A comparison is only as good as the reports behind it. The value is in
separating genuine divergences from shared industry conventions, and that
requires every video to have been analysed the same way.

## Order of operations

1. **Check readiness.** Call `flowscope_health_check` and stop if `ok` is false.
2. **Check what is already analysed.** Call `flowscope_list_videos` and note
   which of the target videos already have `has_report: true`. Those are free to
   reuse.
3. **Analyse only what is missing.** Call `flowscope_analyze_video` with the
   URLs that are not yet analysed, all in one call. It returns the finished
   reports. **Tell the user how many videos will be analysed before you start** —
   each one is minutes of processing and several vision-model calls.
4. **Compare.** Call `flowscope_compare_videos` with the resulting `video_id`s
   (at least two). Results are cached until one of the videos is re-analysed, so
   repeating the call is free unless you pass `force_refresh: true`.

Do not compare the raw text of two reports yourself if
`flowscope_compare_videos` is available: the comparison call is a single
synthesis that has already weighed all the reports against each other, and it
produces the stage matrix you would otherwise have to construct by hand.

## Reading the comparison

`flowscope_compare_videos` returns three things:

- **`common_patterns`** — what the products do the same way. This is where
  convention lives. A pattern shared by every product is usually a convention
  the user already expects, not a decision any of them made.
- **`divergences`** — where they differ. This is where the interesting design
  choices are. Each divergence implies a trade-off; name it.
- **`stage_matrix`** — the per-stage, per-product breakdown. This is the
  structured evidence. Prefer reasoning from it over impressionistic summary,
  because it keeps you from over-weighting whichever demo you happened to
  describe last.

## The shape of a comparison

1. **Shared patterns** — and, for each, why it is probably a convention.
2. **Where they diverge** — stage by stage, with the trade-off each choice implies.
3. **Standout choices** — the single most interesting decision in each product,
   naming the screen that shows it.
4. **What to borrow** — concrete and transferable, not "improve onboarding".

## Grounding the comparison

The comparison output is a synthesis and does not carry screenshots. When a
specific claim needs evidence — "Product A puts pricing before signup", "B hides
the CTA below the fold" — go back to the underlying report with
`flowscope_get_report` for that video, find the screen, and if the point is
visual, fetch it with `flowscope_frame_image`.

Cite the product and the screen for every substantive claim. A comparison
without evidence is an opinion about two products the reader cannot check.

## Fairness

- Compare like with like. Comparing a five-minute enterprise onboarding against
  a thirty-second consumer signup produces differences that come from the
  audience, not the design. Say so when the scope differs.
- A demo is a curated path, and demos differ in how much they show. One product
  may look simpler only because its demo skipped a step. Flag that rather than
  scoring it as a win.
- Do not rank products overall unless asked. The useful output is the set of
  trade-offs, and "better" depends on the product's audience and constraints.
- If a video has an error state or a partial report, say which one and compare
  the rest rather than silently dropping it.

## Cost discipline

Analysing N videos costs roughly N times a single analysis, and a comparison
adds one more synthesis call. Before starting more than two videos, tell the
user the count and that cached videos are free. If the user only wants a
qualitative read on one axis, a single video plus its report is usually cheaper
and just as useful.
