"""Dashboard stats, health, seeding and maintenance endpoints."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Source
from app.db.session import get_session
from app.schemas import HealthResponse, StatsResponse
from app.services.repository import (
    dashboard_stats,
    identifier_type_breakdown,
    source_breakdown,
)
from app.services.seeding import seed_summary

logger = logging.getLogger(__name__)
router = APIRouter(tags=["dashboard"])


@router.get("/stats", response_model=StatsResponse)
async def get_stats(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return await dashboard_stats(session)


@router.get("/stats/breakdown")
async def stats_breakdown(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return {
        "identifier_types": await identifier_type_breakdown(session),
        "sources": await source_breakdown(session),
    }


@router.get("/health", response_model=HealthResponse)
async def health(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    db_state = "down"
    try:
        await session.execute(text("SELECT 1"))
        db_state = "up"
    except Exception as exc:  # noqa: BLE001
        logger.warning("database health check failed: %s", exc)

    redis_state = "down"
    try:
        from app.services.cache import get_redis

        await get_redis().ping()
        redis_state = "up"
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis health check failed: %s", exc)

    worker_state = "down"
    try:
        # liveness is determined by asking the broker to ping registered
        # workers (healthy === at least one worker replies), not by scanning
        # for heartbeat keys (those live on the broker/result DB, not the
        # api's cache DB).
        from app.workers.celery_app import celery_app

        replies = celery_app.control.ping(timeout=3) or []
        if replies:
            worker_state = "up"
    except Exception as exc:  # noqa: BLE001
        logger.warning("worker health check failed: %s", exc)

    status = "ok" if db_state == "up" else "degraded"
    return {
        "status": status,
        "database": db_state,
        "redis": redis_state,
        "worker": worker_state,
        "version": settings.VERSION,
    }


# ------------------------------------------------------------------ seeds ---
@router.get("/seed/summary")
async def seed_info() -> dict[str, Any]:
    return seed_summary()


@router.post("/seed", status_code=202)
async def seed_demo(
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    fmt: str = Query(default="csv", pattern="^(csv|tsv|sql)$"),
    force: bool = Query(default=False),
) -> dict[str, Any]:
    """Load the A/B/C/D demo datasets through the real ingestion pipeline."""
    from app.services.seeding import seed_all

    result = await seed_all(session, fmt=fmt, force=force)
    return result


@router.post("/maintenance/rebuild-clusters", status_code=202)
async def rebuild_clusters(
    background: BackgroundTasks,
    dataset_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Force a full re-clustering of the identifier graph."""
    from app.workers.tasks import rebuild_clusters as task

    try:
        async_result = task.apply_async(args=[dataset_ids], queue="resolve")
        return {"status": "queued", "task_id": async_result.id}
    except Exception as exc:  # noqa: BLE001
        logger.exception("rebuild dispatch failed")
        raise HTTPException(503, f"could not dispatch rebuild: {exc}") from exc


@router.post("/maintenance/refresh-source-counts")
async def refresh_source_counts(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Recompute denormalized counters (safe to run any time)."""
    from sqlalchemy import func, select, update

    from app.db.models import SourceRecord

    rows = await session.execute(
        select(SourceRecord.source_id, func.count()).group_by(SourceRecord.source_id)
    )
    counts = {r[0]: r[1] for r in rows.all()}
    for source_id, count in counts.items():
        await session.execute(
            update(Source).where(Source.id == source_id).values(
                imported_rows=count,
                total_rows=func.greatest(Source.total_rows, count),
                updated_at=func.now(),
            )
        )
    await session.commit()
    return {"updated": len(counts)}
