"""Export completed analyses out of the FlowScope database.

The database is the working store; this writes plain files next to a corpus so
the frame analyses and per-video syntheses can be read, diffed, committed or
handed to another tool without opening SQLite.

    <out>/INDEX.md                 one row per video: frames, screens, top insight
    <out>/<video_id>/frames.json   per-frame screen analysis, in timestamp order
    <out>/<video_id>/flow.md       reconstructed flow steps, insights, gallery

Only videos that actually have analyses or a synthesis are exported.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
_parser.add_argument("--data-dir", required=True, help="FlowScope DATA_DIR holding the SQLite DB")
_parser.add_argument("--out", required=True, help="Directory to write the export into")
_pre = _parser.parse_args()

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DATA_DIR"] = str(Path(_pre.data_dir).expanduser())

from app.db import db  # noqa: E402


def stamp(ms: int) -> str:
    return f"{ms // 60000:02d}:{(ms // 1000) % 60:02d}"


async def main() -> int:
    out_root = Path(_pre.out).expanduser()
    out_root.mkdir(parents=True, exist_ok=True)
    await db.connect()

    videos = await db.fetchall(
        "SELECT v.id, v.title, v.youtube_url, v.duration_seconds FROM videos v "
        "WHERE EXISTS (SELECT 1 FROM frame_analyses fa WHERE fa.video_id = v.id) "
        "   OR EXISTS (SELECT 1 FROM video_syntheses s WHERE s.video_id = v.id) "
        "ORDER BY v.created_at"
    )

    index_rows = []
    for video in videos:
        video_id = video["id"]
        title = video["title"] or video_id
        video_dir = out_root / video_id
        video_dir.mkdir(exist_ok=True)

        analyses = await db.fetchall(
            "SELECT fa.screen_name, fa.flow_step_label, fa.purpose, fa.ux_notes, fa.ui_elements_json, "
            "fa.transcript_excerpt, fa.model_used, f.timestamp_ms "
            "FROM frame_analyses fa JOIN frames f ON f.id = fa.frame_id "
            "WHERE fa.video_id = ? ORDER BY f.timestamp_ms",
            (video_id,),
        )
        frames = [
            {
                "timestamp_ms": row["timestamp_ms"],
                "timestamp": stamp(row["timestamp_ms"]),
                "screen_name": row["screen_name"],
                "flow_step_label": row["flow_step_label"],
                "purpose": row["purpose"],
                "ux_notes": row["ux_notes"],
                "ui_elements": json.loads(row["ui_elements_json"] or "[]"),
                "transcript_excerpt": row["transcript_excerpt"],
                "model_used": row["model_used"],
            }
            for row in analyses
        ]
        if frames:
            (video_dir / "frames.json").write_text(json.dumps(frames, indent=2) + "\n")

        synthesis = await db.fetchone(
            "SELECT * FROM video_syntheses WHERE video_id = ?", (video_id,)
        )
        insights: list[str] = []
        steps: list[dict] = []
        if synthesis is not None:
            steps = json.loads(synthesis["flow_steps_json"] or "[]")
            insights = json.loads(synthesis["ux_insights_json"] or "[]")
            gallery = json.loads(synthesis["screens_gallery_json"] or "[]")
            lines = [f"# {title}", "", f"- video_id: `{video_id}`",
                     f"- model: `{synthesis['model_used']}`",
                     f"- url: {video['youtube_url']}", ""]
            lines.append("## Flow steps")
            for step in steps:
                lines.append(f"{step.get('step_index')}. **{step.get('screen_name')}** "
                             f"(`{step.get('frame_id')}`) — {step.get('description')}")
            lines.append("")
            lines.append("## UX insights")
            lines += [f"- {i}" for i in insights] or ["- (none)"]
            lines.append("")
            lines.append("## Screens gallery")
            lines += [f"- `{g.get('frame_id')}` — {g.get('flow_stage')}" for g in gallery] or ["- (none)"]
            lines.append("")
            (video_dir / "flow.md").write_text("\n".join(lines))

        index_rows.append({
            "video_id": video_id, "title": title, "seconds": video["duration_seconds"],
            "screens": len(frames), "flow_steps": len(steps), "insights": len(insights),
            "top_insight": (insights[0] if insights else ""),
        })
        print(f"  {video_id}  screens={len(frames):<4} steps={len(steps):<4} insights={len(insights)}  {title[:48]}")

    index_lines = [
        "# FlowScope analysis export", "",
        f"{len(index_rows)} video(s) with analyses.", "",
        "| video_id | title | seconds | screens | flow steps | insights |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in index_rows:
        index_lines.append(
            f"| `{row['video_id']}` | {row['title'][:60]} | {row['seconds'] or ''} | "
            f"{row['screens']} | {row['flow_steps']} | {row['insights']} |"
        )
    (out_root / "INDEX.md").write_text("\n".join(index_lines) + "\n")
    print(f"\n{len(index_rows)} video(s) exported to {out_root}")
    await db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
