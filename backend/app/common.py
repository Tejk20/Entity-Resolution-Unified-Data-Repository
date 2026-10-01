"""Shared helpers: hashing, job-state mutation, progress math."""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import PHASE_PROGRESS
from app.db.models import ProcessingJob

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uid() -> str:
    return uuid.uuid4().hex


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def record_hash(dataset_id: int, row: dict[str, Any], pk_column: str | None = None) -> str:
    """Stable identity for a row within a dataset.

    When a primary-key-ish column is mapped we hash *that* so a multi-part
    import of the same logical row is recognised as a duplicate even if the
    rest of the row shifted. Otherwise we hash the full canonical payload.
    """
    if pk_column and pk_column in row and row[pk_column] not in (None, ""):
        return sha256_text(f"{dataset_id}|pk|{pk_column}|{row[pk_column]}")
    payload = {k: v for k, v in row.items() if k != "__row__"}
    return sha256_text(f"{dataset_id}|row|{json.dumps(payload, sort_keys=True, default=str)}")


async def get_job(session: AsyncSession, job_id: int) -> ProcessingJob | None:
    return await session.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id))


async def get_job_by_uid(session: AsyncSession, uid: str) -> ProcessingJob | None:
    return await session.scalar(select(ProcessingJob).where(ProcessingJob.job_uid == uid))


async def set_job_status(
    session: AsyncSession,
    job_id: int,
    status: str,
    *,
    progress: float | None = None,
    error: str | None = None,
    commit: bool = True,
) -> None:
    values: dict[str, Any] = {"status": status, "updated_at": utcnow()}
    if progress is not None:
        values["progress"] = max(0.0, min(1.0, progress))
    if error is not None:
        values["error"] = error[:4000]
    if status == "Mapping" and "started_at" in ProcessingJob.__table__.c:
        values.setdefault("started_at", utcnow())
    await session.execute(
        update(ProcessingJob).where(ProcessingJob.id == job_id).values(**values)
    )
    if commit:
        await session.commit()


async def update_job_progress(
    session: AsyncSession,
    job_id: int,
    *,
    status: str,
    processed_rows: int | None = None,
    chunks_completed: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Progress = phase weight + intra-phase completion fraction."""
    base = PHASE_PROGRESS.get(status, 0.0)
    if status == "Cleaning":
        upper = PHASE_PROGRESS["Indexing"]
        span = upper - base
    elif status == "Indexing":
        upper = PHASE_PROGRESS["Matching"]
        span = upper - base
    elif status == "Matching":
        upper = PHASE_PROGRESS["Completed"]
        span = upper - base
    else:
        span = 0.0

    job = await get_job(session, job_id)
    fraction = 0.0
    if job is not None and job.total_rows and processed_rows is not None:
        fraction = min(1.0, processed_rows / max(job.total_rows, 1))
    elif job is not None and job.chunk_count and chunks_completed is not None:
        fraction = min(1.0, chunks_completed / max(job.chunk_count, 1))

    values: dict[str, Any] = {
        "status": status,
        "progress": round(min(1.0, base + span * fraction), 4),
        "updated_at": utcnow(),
    }
    if processed_rows is not None:
        values["processed_rows"] = processed_rows
    if chunks_completed is not None:
        values["chunks_completed"] = chunks_completed
    if extra:
        values.update(extra)
    await session.execute(
        update(ProcessingJob).where(ProcessingJob.id == job_id).values(**values)
    )
    await session.commit()


def merge_counts(a: dict[str, int] | None, b: dict[str, int] | None) -> dict[str, int]:
    out = dict(a or {})
    for k, v in (b or {}).items():
        out[k] = out.get(k, 0) + v
    return out
