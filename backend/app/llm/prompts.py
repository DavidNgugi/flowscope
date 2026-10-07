"""Prompt/message builders and hand-written JSON schemas for the three LLM
calls (per-frame vision analysis, per-video synthesis, cross-video
comparison). Schemas are written explicitly rather than derived from the
Pydantic models in app/schemas.py, to keep full control over the flat shape
and field descriptions the model sees via tool_choice-forced tool use.
"""

import base64
import io
from pathlib import Path

from PIL import Image

MAX_IMAGE_DIM = 1280


def _resize_and_encode(image_path: Path) -> tuple[str, str]:
    with Image.open(image_path) as img:
        img = img.convert("RGB")
        w, h = img.size
        scale = MAX_IMAGE_DIM / max(w, h)
        if scale < 1:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
    return "image/jpeg", data


# ---- per-frame vision analysis ----

FRAME_ANALYSIS_SYSTEM = (
    "You are a senior product/UX researcher who benchmarks competitor products by analyzing "
    "screenshots from their demo videos. For the given screen, identify what it is, its purpose "
    "in the flow, and notable UX/design patterns. Be specific and concrete, referencing actual "
    "visible UI elements and labels rather than generic observations."
)


def build_frame_analysis_message(
    image_path: Path, transcript_excerpt: str, position: int, total_frames: int
) -> list[dict]:
    media_type, data = _resize_and_encode(image_path)
    context_lines = [f"This is screen {position + 1} of {total_frames} in chronological order."]
    if transcript_excerpt:
        context_lines.append(f'Narration around this moment in the video: "{transcript_excerpt}"')
    else:
        context_lines.append("No narration is available for this moment.")

    text = (
        "Analyze this screen from a product demo video as a UX researcher benchmarking "
        "product flows.\n\n" + "\n".join(context_lines)
    )

    return [
        {
            "role": "user",
            "content": [
                # Canonical part shape; each provider adapter translates it.
                {"type": "image", "media_type": media_type, "data": data},
                {"type": "text", "text": text},
            ],
        }
    ]


def frame_analysis_tool_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "screen_name": {
                "type": "string",
                "description": "Short human name for this screen/state, e.g. 'Login screen', 'Item checkout modal'",
            },
            "flow_step_label": {
                "type": "string",
                "description": "Short label for this step in the overall flow, e.g. 'Step 2: Authenticate'",
            },
            "purpose": {"type": "string", "description": "What this screen is for"},
            "ux_notes": {
                "type": "string",
                "description": "Notable UX/design patterns visible or described in the narration",
            },
            "ui_elements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "description": "e.g. button, input, nav item, modal, toggle"},
                        "label": {"type": "string", "description": "Visible text/label if any"},
                        "notes": {"type": "string", "description": "Why it matters / how it's used"},
                    },
                    "required": ["type"],
                },
            },
        },
        "required": ["screen_name", "flow_step_label", "purpose", "ux_notes", "ui_elements"],
    }


# ---- per-video synthesis ----

SYNTHESIS_SYSTEM = (
    "You are a senior product/UX researcher. You are given a chronological list of screens "
    "observed in a product demo video, each already analyzed individually. Reconstruct the "
    "end-to-end user flow, identify recurring UX/design patterns worth benchmarking against, "
    "and group screens into a tagged gallery by flow stage (e.g. onboarding, core-task, settings, "
    "checkout). Relate each visible screen to the narration at that moment so descriptions explain "
    "both what the viewer sees and what the presenter says or demonstrates. Do not claim that a "
    "narrated feature is visible unless the screen supports it. Reference frame_id values exactly "
    "as given -- do not invent new ones."
)


def build_synthesis_message(entries: list[dict]) -> list[dict]:
    lines = [
        f'- frame_id={e["frame_id"]} t={e["timestamp_ms"]}ms screen="{e["screen_name"]}" '
        f'step="{e["flow_step_label"]}" purpose="{e["purpose"]}" ux_notes="{e["ux_notes"]}" '
        f'narration="{e["transcript_excerpt"]}"'
        for e in entries
    ]
    text = "Screens in chronological order:\n" + "\n".join(lines)
    return [{"role": "user", "content": text}]


def video_synthesis_tool_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "flow_steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step_index": {"type": "integer"},
                        "screen_name": {"type": "string"},
                        "frame_id": {"type": "string", "description": "Must match a frame_id given in the input"},
                        "description": {"type": "string"},
                    },
                    "required": ["step_index", "screen_name", "frame_id", "description"],
                },
            },
            "ux_insights": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific, concrete UX/design pattern observations",
            },
            "screens_gallery": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "frame_id": {"type": "string"},
                        "flow_stage": {
                            "type": "string",
                            "description": "e.g. onboarding, core-task, settings, checkout",
                        },
                    },
                    "required": ["frame_id", "flow_stage"],
                },
            },
        },
        "required": ["flow_steps", "ux_insights", "screens_gallery"],
    }


# ---- cross-video comparison ----

COMPARISON_SYSTEM = (
    "You are a senior product/UX researcher comparing several competitor products based on "
    "their reconstructed onboarding/product flows. Identify concrete, specific common patterns "
    "and meaningful divergences -- avoid generic statements. Build a stage-by-stage matrix "
    "showing what each product does at comparable flow stages."
)


def build_comparison_message(video_syntheses: list[dict]) -> list[dict]:
    lines: list[str] = []
    for v in video_syntheses:
        lines.append(f'### {v["title"] or v["video_id"]} (video_id={v["video_id"]})')
        lines.append("Flow steps:")
        for step in v["flow_steps"]:
            lines.append(f'  {step["step_index"]}. {step["screen_name"]}: {step["description"]}')
        lines.append("UX insights:")
        for insight in v["ux_insights"]:
            lines.append(f"  - {insight}")
        lines.append("")
    return [{"role": "user", "content": "\n".join(lines)}]


def comparison_tool_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "common_patterns": {"type": "array", "items": {"type": "string"}},
            "divergences": {"type": "array", "items": {"type": "string"}},
            "stage_matrix": {
                "type": "array",
                "items": {"type": "object"},
                "description": "One entry per comparable flow stage, mapping video_id -> what that product does there",
            },
        },
        "required": ["common_patterns", "divergences", "stage_matrix"],
    }
