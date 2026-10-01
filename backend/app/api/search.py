"""Progressive entity enrichment search endpoints."""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import new_uid
from app.core.config import settings
from app.db.models import EntityRecord, MasterEntity, ResolutionRun
from app.db.session import get_session
from app.schemas import EntityListResponse, EntityOut, SearchRequest, SearchResponse
from app.services.resolution import ClusterIndexer, ProgressiveResolver, persist_resolution
from app.services.cache import cache_get, cache_set
from app.services.repository import build_entity_view

logger = logging.getLogger(__name__)
router = APIRouter(tags=["search"])


def _cache_key(query: str, depth: int, weak: bool) -> str:
    h = hashlib.sha1(f"{query.strip().lower()}|{depth}|{weak}".encode()).hexdigest()[:16]
    return f"search:{h}"


@router.post("/search", response_model=SearchResponse)
async def search(
    payload: SearchRequest, session: AsyncSession = Depends(get_session)
) -> SearchResponse:
    """Progressive enrichment search.

    Runs the recursive BFS from the seed identifier, persists the outcome (so the
    entity stays in the repository) and returns the full discovery timeline.
    """
    query = payload.query.strip()
    if not query:
        raise HTTPException(400, "query must not be empty")

    depth = payload.max_depth or settings.MAX_BFS_DEPTH
    key = _cache_key(query, depth, payload.include_weak)

    cached = await cache_get(key)
    if cached is not None:
        return SearchResponse(**{**cached, "cached": True})

    resolver = ProgressiveResolver(session)
    result = await resolver.resolve(
        query, max_depth=depth, include_weak=payload.include_weak
    )

    run_uid = new_uid()
    entity_view: dict[str, Any] | None = None
    if result.record_ids and payload.persist:
        eid = await persist_resolution(session, result, query)
        if eid:
            # persist_resolution can merge per-source entities into one cluster;
            # refresh the denormalized rollups so the profile is consistent.
            await ClusterIndexer(session)._refresh_entity_rollups([eid])
            entity_view = await build_entity_view(session, eid)
    elif result.master_entity_id:
        entity_view = await build_entity_view(session, result.master_entity_id)

    source_breakdown: dict[str, int] = {}
    hop_breakdown: dict[str, int] = {}
    if entity_view:
        source_breakdown = entity_view["source_breakdown"]
        hop_breakdown = entity_view["hop_breakdown"]

    response = SearchResponse(
        query=query,
        seed_type=result.seed_type,
        seed_value=result.seed_value,
        found=bool(result.record_ids),
        run_uid=run_uid,
        master_entity_id=entity_view["id"] if entity_view else None,
        entity=entity_view,
        timeline=[h.as_dict() for h in result.hops],
        identifiers=result.identifiers[:200],
        source_breakdown=source_breakdown,
        hop_breakdown=hop_breakdown,
        record_count=len(result.record_ids),
        source_count=len(source_breakdown),
        hop_count=result.hop_count,
        duration_ms=result.duration_ms,
        cached=False,
        exhausted=result.exhausted,
        truncated=result.truncated,
    )

    # persist the trace for the activity feed
    run = ResolutionRun(
        run_uid=run_uid,
        seed_query=query[:1024],
        seed_type=result.seed_type,
        master_entity_id=response.master_entity_id,
        status="completed" if result.record_ids else "no_match",
        hop_count=result.hop_count,
        record_count=len(result.record_ids),
        source_count=len(source_breakdown),
        duration_ms=result.duration_ms,
        timeline={
            "hops": [h.as_dict() for h in result.hops],
            "identifiers": result.identifiers[:50],
        },
    )
    session.add(run)
    await session.commit()

    # cache only the successful, stable part of the response
    if result.record_ids:
        await cache_set(
            key,
            {
                "query": response.query,
                "seed_type": response.seed_type,
                "seed_value": response.seed_value,
                "found": True,
                "run_uid": None,
                "master_entity_id": response.master_entity_id,
                "entity": response.entity,
                "timeline": [t.model_dump() for t in response.timeline],
                "identifiers": response.identifiers,
                "source_breakdown": response.source_breakdown,
                "hop_breakdown": response.hop_breakdown,
                "record_count": response.record_count,
                "source_count": response.source_count,
                "hop_count": response.hop_count,
                "duration_ms": response.duration_ms,
                "exhausted": response.exhausted,
                "truncated": response.truncated,
            },
        )

    return response


@router.get("/search/suggest")
async def suggest(
    q: str, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Type-ahead over the identifier index."""
    from app.db.models import EntityIdentifier
    from app.services.resolution import infer_seed_type

    q = q.strip()
    if len(q) < 2:
        return {"suggestions": []}
    ctype, normalized = infer_seed_type(q)
    stmt = (
        select(
            EntityIdentifier.normalized_value,
            EntityIdentifier.canonical_type,
            func.count(),
        )
        .where(
            EntityIdentifier.normalized_value.ilike(f"%{normalized or q.lower()}%")
        )
        .group_by(EntityIdentifier.normalized_value, EntityIdentifier.canonical_type)
        .order_by(func.count().desc())
        .limit(12)
    )
    rows = (await session.execute(stmt)).all()
    return {
        "inferred_type": ctype,
        "normalized": normalized,
        "suggestions": [
            {"value": r[0], "type": r[1], "record_count": r[2]} for r in rows
        ],
    }


@router.get("/runs")
async def recent_runs(
    session: AsyncSession = Depends(get_session), limit: int = 25
) -> list[dict[str, Any]]:
    stmt = (
        select(ResolutionRun)
        .order_by(ResolutionRun.id.desc())
        .limit(min(max(limit, 1), 100))
    )
    runs = (await session.execute(stmt)).scalars().all()
    return [
        {
            "run_uid": r.run_uid,
            "query": r.seed_query,
            "seed_type": r.seed_type,
            "master_entity_id": r.master_entity_id,
            "status": r.status,
            "hop_count": r.hop_count,
            "record_count": r.record_count,
            "source_count": r.source_count,
            "duration_ms": r.duration_ms,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in runs
    ]


@router.get("/runs/{run_uid}")
async def get_run(
    run_uid: str, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    run = await session.scalar(select(ResolutionRun).where(ResolutionRun.run_uid == run_uid))
    if run is None:
        raise HTTPException(404, "run not found")
    return {
        "run_uid": run.run_uid,
        "query": run.seed_query,
        "seed_type": run.seed_type,
        "master_entity_id": run.master_entity_id,
        "status": run.status,
        "hop_count": run.hop_count,
        "record_count": run.record_count,
        "source_count": run.source_count,
        "duration_ms": run.duration_ms,
        "timeline": run.timeline,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


# --------------------------------------------------------------- entities ---
@router.get("/entities", response_model=EntityListResponse)
async def list_entities(
    session: AsyncSession = Depends(get_session),
    page: int = 1,
    page_size: int = 25,
    sort: str = "records",
    q: str | None = None,
) -> EntityListResponse:
    page = max(1, page)
    page_size = min(max(page_size, 1), 200)

    order = {
        "records": MasterEntity.record_count.desc(),
        "sources": MasterEntity.source_count.desc(),
        "confidence": MasterEntity.confidence.desc(),
        "recent": MasterEntity.last_seen.desc(),
        "name": MasterEntity.display_name.asc(),
    }.get(sort, MasterEntity.record_count.desc())

    count_stmt = select(func.count()).select_from(MasterEntity)
    stmt = select(MasterEntity)
    if q:
        like = f"%{q.strip().lower()}%"
        count_stmt = count_stmt.where(
            func.lower(func.coalesce(MasterEntity.primary_email, "")).like(like)
            | func.lower(func.coalesce(MasterEntity.display_name, "")).like(like)
            | func.lower(func.coalesce(MasterEntity.entity_key, "")).like(like)
        )
        stmt = stmt.where(
            func.lower(func.coalesce(MasterEntity.primary_email, "")).like(like)
            | func.lower(func.coalesce(MasterEntity.display_name, "")).like(like)
            | func.lower(func.coalesce(MasterEntity.entity_key, "")).like(like)
        )

    total = int((await session.execute(count_stmt)).scalar() or 0)
    rows = (
        await session.execute(
            stmt.order_by(order).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()

    return EntityListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[EntityOut.model_validate(r) for r in rows],
    )


@router.get("/entities/{entity_id}")
async def get_entity(
    entity_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    view = await build_entity_view(session, entity_id)
    if view is None:
        raise HTTPException(404, "entity not found")
    return view


@router.get("/entities/{entity_id}/records")
async def entity_records(
    entity_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Raw + normalized payloads for exact source traceability."""
    view = await build_entity_view(session, entity_id)
    if view is None:
        raise HTTPException(404, "entity not found")
    return {
        "entity_id": entity_id,
        "record_count": len(view["records"]),
        "records": view["records"],
    }


@router.get("/entities/{entity_id}/timeline")
async def entity_timeline(
    entity_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Records grouped by the hop at which they were discovered."""
    view = await build_entity_view(session, entity_id)
    if view is None:
        raise HTTPException(404, "entity not found")

    hops: dict[int, dict[str, Any]] = {}
    for rec in view["records"]:
        hop = rec["discovered_at_hop"] or 0
        entry = hops.setdefault(
            hop,
            {
                "hop": hop,
                "label": "Seed query" if hop == 0 else f"Hop {hop}",
                "records": [],
                "sources": set(),
            },
        )
        entry["records"].append(
            {
                "source_record_id": rec["source_record_id"],
                "source_name": rec["source_name"],
                "system_key": rec["system_key"],
                "dataset_name": rec["dataset_name"],
                "matched_on": rec["matched_on"],
                "raw": rec["raw"],
                "clean": rec["clean"],
            }
        )
        entry["sources"].add(rec["source_name"])

    for entry in hops.values():
        entry["sources"] = sorted(entry["sources"])
    return {
        "entity_id": entity_id,
        "entity_key": view["entity_key"],
        "display_name": view["display_name"],
        "hops": [hops[k] for k in sorted(hops)],
    }


@router.delete("/entities/{entity_id}", status_code=204, response_model=None)
async def delete_entity(
    entity_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    entity = await session.get(MasterEntity, entity_id)
    if entity is None:
        raise HTTPException(404, "entity not found")
    await session.execute(
        EntityRecord.__table__.delete().where(
            EntityRecord.__table__.c.master_entity_id == entity_id
        )
    )
    await session.delete(entity)
    await session.commit()
