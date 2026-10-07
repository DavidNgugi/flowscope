"""Per-call AI usage attribution and historical cost estimation.

The Anthropic Messages API returns token counts on every successful response,
which gives more precise attribution to a FlowScope run and step than the
organization-level Usage API. Rates are snapshotted onto every record so a
future pricing change does not rewrite historical estimates.
"""

import logging
from dataclasses import dataclass
from typing import Any

from app.db import db
from app.llm.catalog import profile_for
from app.llm.types import ModelPricing
from app.models import new_id, utcnow_iso

logger = logging.getLogger("flowscope.llm.usage")


@dataclass(frozen=True)
class AIUsageContext:
    analysis_type: str
    analysis_id: str
    stage: str
    job_id: str | None = None
    video_id: str | None = None
    operation_id: str | None = None


def pricing_for_model(model: str, provider: str = "anthropic") -> ModelPricing | None:
    """Published USD-per-MTok rates for a model, or None when the catalog does
    not know it. None is recorded as a null cost rather than a guess."""
    return profile_for(provider, model).pricing


def estimate_cost_usd(
    *,
    pricing: ModelPricing,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int,
    cache_read_input_tokens: int,
) -> float:
    return (
        input_tokens * pricing.input_per_mtok
        + output_tokens * pricing.output_per_mtok
        + cache_creation_input_tokens * pricing.cache_creation_per_mtok
        + cache_read_input_tokens * pricing.cache_read_per_mtok
    ) / 1_000_000


async def record_ai_usage(
    *,
    context: AIUsageContext,
    model: str,
    request_id: str | None,
    usage: Any,
    duration_ms: int,
    provider: str = "anthropic",
) -> None:
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cache_creation = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    service_tier = getattr(usage, "service_tier", None)
    inference_geo = getattr(usage, "inference_geo", None)
    pricing = pricing_for_model(model, provider)
    if pricing and inference_geo == "us":
        pricing = ModelPricing(
            input_per_mtok=pricing.input_per_mtok * 1.1,
            output_per_mtok=pricing.output_per_mtok * 1.1,
            cache_creation_per_mtok=pricing.cache_creation_per_mtok * 1.1,
            cache_read_per_mtok=pricing.cache_read_per_mtok * 1.1,
        )
    cost = (
        estimate_cost_usd(
            pricing=pricing,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
        )
        if pricing
        else None
    )

    try:
        async with db.write() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO ai_usage_records "
                "(id, analysis_type, analysis_id, job_id, video_id, stage, operation_id, provider, "
                "model, request_id, input_tokens, output_tokens, cache_creation_input_tokens, "
                "cache_read_input_tokens, input_cost_per_mtok, output_cost_per_mtok, "
                "cache_creation_cost_per_mtok, cache_read_cost_per_mtok, estimated_cost_usd, "
                "service_tier, inference_geo, duration_ms, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    new_id("usage"), context.analysis_type, context.analysis_id, context.job_id,
                    context.video_id, context.stage, context.operation_id, provider, model,
                    request_id, input_tokens, output_tokens, cache_creation, cache_read,
                    pricing.input_per_mtok if pricing else None,
                    pricing.output_per_mtok if pricing else None,
                    pricing.cache_creation_per_mtok if pricing else None,
                    pricing.cache_read_per_mtok if pricing else None,
                    cost, service_tier, inference_geo, duration_ms, utcnow_iso(),
                ),
            )
    except Exception:
        # Usage telemetry must never turn a successful AI result into a failed analysis.
        logger.exception("failed to record AI usage for request %s", request_id)


def summarize_usage_rows(rows: list[dict]) -> dict:
    def aggregate(items: list[dict]) -> dict:
        known_costs = [row["estimated_cost_usd"] for row in items if row["estimated_cost_usd"] is not None]
        return {
            "call_count": len(items),
            "input_tokens": sum(row["input_tokens"] for row in items),
            "output_tokens": sum(row["output_tokens"] for row in items),
            "cache_creation_input_tokens": sum(row["cache_creation_input_tokens"] for row in items),
            "cache_read_input_tokens": sum(row["cache_read_input_tokens"] for row in items),
            "estimated_cost_usd": sum(known_costs) if known_costs else None,
            "cost_complete": len(known_costs) == len(items),
        }

    by_stage: dict[str, list[dict]] = {}
    for row in rows:
        by_stage.setdefault(row["stage"], []).append(row)

    return {
        **aggregate(rows),
        "by_stage": [
            {"stage": stage, **aggregate(stage_rows)}
            for stage, stage_rows in sorted(by_stage.items())
        ],
        "models": sorted({row["model"] for row in rows}),
    }


async def usage_for_analysis(analysis_type: str, analysis_id: str) -> dict:
    rows = await db.fetchall(
        "SELECT * FROM ai_usage_records WHERE analysis_type=? AND analysis_id=? ORDER BY created_at",
        (analysis_type, analysis_id),
    )
    return summarize_usage_rows([dict(row) for row in rows])


async def video_usage_history(video_id: str) -> list[dict]:
    jobs = await db.fetchall(
        "SELECT id, status, created_at, finished_at FROM jobs WHERE video_id=? ORDER BY created_at DESC",
        (video_id,),
    )
    history = []
    for job in jobs:
        summary = await usage_for_analysis("video", job["id"])
        history.append(
            {
                "job_id": job["id"],
                "status": job["status"],
                "created_at": job["created_at"],
                "finished_at": job["finished_at"],
                **summary,
            }
        )
    return history
