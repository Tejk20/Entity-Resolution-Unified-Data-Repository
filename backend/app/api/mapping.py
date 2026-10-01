"""Mapping preview + confirmation endpoints."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import new_uid
from app.core.config import settings
from app.core.constants import CLEANING, PENDING_CONFIRMATION
from app.db.models import Dataset, ProcessingJob
from app.db.session import get_session
from app.schemas import JobOut, MappingConfirm
from app.services.cache import cache_invalidate_prefix
from app.services.field_mapping import build_mapping
from app.services.parsers import sample_for_mapping

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mapping", tags=["field-mapping"])


@router.get("/jobs/{job_id}")
async def mapping_preview(
    job_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Auto-detected column -> canonical field suggestions with confidence."""
    job = await session.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")

    tables = (job.column_mapping or {}).get("tables")
    if not tables or not job.file_path:
        # nothing cached yet - recompute on demand
        return await _recompute_preview(session, job)

    return {
        "job_uid": job.job_uid,
        "job_id": job.id,
        "status": job.status,
        "total_rows": job.total_rows,
        "chunk_size": job.chunk_size,
        "chunk_count": job.chunk_count,
        "warnings": job.warnings or [],
        "confidence": job.mapping_confidence,
        "tables": [
            {
                "table_name": t["table_name"],
                "mapping": t["mapping"],
                "confidence": t["confidence"],
                "columns": t["columns"],
                "unmapped_columns": t["unmapped_columns"],
                "ambiguous": t["ambiguous"],
                "sample_rows": t.get("sample_rows", []),
            }
            for t in tables
        ],
    }


async def _recompute_preview(
    session: AsyncSession, job: ProcessingJob
) -> dict[str, Any]:
    samples = sample_for_mapping(job.file_path or "", job.file_type, limit=300)
    out: list[dict[str, Any]] = []
    for table, columns, rows in samples:
        res = build_mapping(rows, columns)
        out.append(
            {
                "table_name": table,
                "mapping": res.mapping,
                "confidence": round(res.confidence, 4),
                "columns": [c.as_dict() for c in res.columns],
                "unmapped_columns": res.unmapped_columns,
                "ambiguous": res.ambiguous,
                "sample_rows": rows[:5],
            }
        )
    return {
        "job_uid": job.job_uid,
        "job_id": job.id,
        "status": job.status,
        "total_rows": job.total_rows,
        "warnings": job.warnings or [],
        "confidence": job.mapping_confidence,
        "tables": out,
    }


@router.post("/jobs/{job_id}/rescan")
async def rescan(
    job_id: int, background: BackgroundTasks, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Re-run detection, e.g. after the file is re-uploaded or a worker failed."""
    job = await session.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    job.column_mapping = None
    job.status = "Mapping"
    job.progress = 0.08
    await session.commit()

    from app.workers.tasks import run_import

    try:
        run_import.apply_async(args=[job_id], queue="ingest")
    except Exception:  # noqa: BLE001
        logger.exception("rescan dispatch failed for job %s", job_id)
    return {"job_id": job_id, "status": "Mapping"}


@router.post("/jobs/{job_id}/confirm", response_model=JobOut)
async def confirm_mapping(
    job_id: int,
    payload: MappingConfirm,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ProcessingJob:
    """Approve the mapping and start chunked cleaning + indexing."""
    job = await session.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status not in (PENDING_CONFIRMATION, "Mapping", "Failed"):
        raise HTTPException(
            409, f"job is '{job.status}' and cannot be confirmed right now"
        )
    if payload.skip:
        job.status = "Completed"
        job.progress = 1.0
        job.warnings = [*(job.warnings or []), "Import skipped by operator."]
        await session.commit()
        return job

    mapping = {k: v for k, v in payload.mapping.items() if v}
    if not mapping:
        raise HTTPException(400, "at least one canonical field must be mapped")

    job.status = CLEANING
    job.progress = 0.18
    job.chunk_size = job.chunk_size or settings.CHUNK_SIZE
    batch_id = new_uid()
    job.batch_id = batch_id
    job.column_mapping = {
        **(job.column_mapping or {}),
        "confirmed": mapping,
        "table_mappings": payload.table_mappings,
    }
    await session.commit()

    # persist the mapping onto the datasets so the UI shows what was used
    for table, cols in (payload.table_mappings or {}).items():
        ds = await session.scalar(
            select(Dataset).where(
                Dataset.source_id == job.source_id, Dataset.table_name == table
            )
        )
        if ds is not None:
            ds.column_mapping = cols
    await session.commit()

    # re-load the (just-committed) job so response serialization does not
    # trigger a lazy refresh outside the async greenlet
    await session.refresh(job)

    background.add_task(_dispatch_ingest, job_id, mapping, batch_id)
    return job


def _dispatch_ingest(job_id: int, mapping: dict[str, str], batch_id: str) -> None:
    from app.workers.tasks import run_ingest

    try:
        run_ingest.apply_async(args=[job_id, mapping, batch_id], queue="ingest")
    except Exception:  # noqa: BLE001
        logger.exception("could not dispatch ingest for job %s", job_id)


@router.get("/canonical-fields")
async def canonical_fields() -> dict[str, Any]:
    """Reference data for the mapping UI dropdowns."""
    from app.services.field_mapping import EXACT_ALIASES, HINTS, TYPE_PRIOR
    from app.services.normalize import CANONICAL_FIELDS

    return {
        "fields": [
            {
                "canonical_type": f,
                "prior": TYPE_PRIOR.get(f, 0.5),
                "known_aliases": sorted(EXACT_ALIASES.get(f, set()))[:12],
                "hints": [h[1] for h in HINTS if h[0] == f][:6],
                "description": _DESCRIPTIONS.get(f, ""),
            }
            for f in CANONICAL_FIELDS
        ],
        "weights": settings.IDENTIFIER_WEIGHTS,
        "linkable_types": settings.LINKABLE_TYPES,
    }


_DESCRIPTIONS: dict[str, str] = {
    "email": "Primary join key. Lowercased and validated; strongest identifier.",
    "phone": "Digits only, E.164-style. Tolerates country codes and extensions.",
    "name": "Display name. Used for labelling; too weak to merge on alone.",
    "username": "Handle without '@'. A strong secondary join key.",
    "member_id": "Identifier from the owning system. Very strong join key.",
    "address": "Postal address. Descriptive only; never used to merge.",
    "company": "Employer or organisation. Descriptive only; never used to merge.",
}
