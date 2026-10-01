"""Dataset / upload endpoints."""
from __future__ import annotations

import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import new_uid
from app.core.config import settings
from app.core.constants import UPLOADED
from app.db.models import (
    Dataset,
    EntityRecord,
    MasterEntity,
    ProcessingJob,
    Source,
    SourceRecord,
)
from app.db.session import get_session
from app.schemas import (
    DatasetOut,
    JobOut,
    SourceCreate,
    SourceDetail,
    SourceOut,
    UploadAccepted,
)
from app.services.parsers import detect_file_type
from app.services.repository import source_breakdown

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sources", tags=["datasets"])

ALLOWED_EXT = {".csv", ".tsv", ".txt", ".sql", ".dump", ".ddl"}


def slugify(value: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "source"
    return base[:120]


def safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).name)[:180] or "upload.dat"


@router.get("", response_model=list[SourceOut])
async def list_sources(
    session: AsyncSession = Depends(get_session),
    skip: int = 0,
    limit: int = 100,
) -> list[Source]:
    stmt = (
        select(Source)
        .order_by(Source.id)
        .offset(max(0, skip))
        .limit(min(max(limit, 1), 500))
    )
    return list((await session.execute(stmt)).scalars())


@router.get("/breakdown")
async def breakdown(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Per-source record counts and how many master entities they feed."""
    return {"sources": await source_breakdown(session)}


@router.get("/datasets", response_model=list[DatasetOut])
async def list_datasets(
    session: AsyncSession = Depends(get_session),
    source_id: int | None = None,
) -> list[Dataset]:
    stmt = select(Dataset).order_by(Dataset.id)
    if source_id is not None:
        stmt = stmt.where(Dataset.source_id == source_id)
    return list((await session.execute(stmt)).scalars())


@router.post("", response_model=SourceDetail, status_code=201)
async def create_source(
    payload: SourceCreate, session: AsyncSession = Depends(get_session)
) -> Source:
    source = Source(
        name=payload.name,
        slug=slugify(payload.name) + "-" + uuid.uuid4().hex[:6],
        description=payload.description,
        system_key=payload.system_key,
        source_type="csv",
        status="active",
    )
    session.add(source)
    await session.commit()
    await session.refresh(source)
    return source


@router.get("/{source_id}", response_model=SourceDetail)
async def get_source(
    source_id: int, session: AsyncSession = Depends(get_session)
) -> Source:
    source = await session.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "source not found")
    return source


@router.delete("/{source_id}", status_code=204, response_model=None)
async def delete_source(
    source_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    source = await session.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "source not found")
    if source.file_path and Path(source.file_path).exists():
        Path(source.file_path).unlink(missing_ok=True)
    await session.delete(source)   # cascades to datasets/records/jobs
    await session.commit()
    await _rebuild_clusters()


# ------------------------------------------------------------------ upload ---
@router.post("/upload", response_model=UploadAccepted, status_code=202)
async def upload_dataset(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
    system_key: str | None = Form(default=None),
    source_id: int | None = Form(default=None),
    auto_confirm: bool = Form(default=False),
    mapping_json: str | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
) -> UploadAccepted:
    """Stream an upload to disk, register it, and start profiling.

    Multi-part imports: pass an existing ``source_id`` to add another file to a
    dataset that is already loaded. Records are deduplicated on
    ``(dataset_id, record_hash)`` so overlapping slices never double up.
    """
    filename = safe_filename(file.filename or "upload.dat")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(
            400, f"unsupported file type '{ext}'. Allowed: {sorted(ALLOWED_EXT)}"
        )

    # --- persist to disk in streaming fashion ---------------------------
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{new_uid()}_{filename}"
    dest = upload_dir / stored_name

    size = 0
    with dest.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > settings.MAX_UPLOAD_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "file exceeds the maximum upload size")
            out.write(chunk)
    await file.close()

    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "uploaded file is empty")

    head = dest.open("rb").read(2048)
    file_type = detect_file_type(filename, head)

    # --- resolve the source ---------------------------------------------
    source: Source | None = None
    if source_id is not None:
        source = await session.get(Source, source_id)
        if source is None:
            dest.unlink(missing_ok=True)
            raise HTTPException(404, "source not found for multi-part import")

    if source is None:
        display = name or Path(filename).stem.replace("_", " ").title()
        source = Source(
            name=display,
            slug=slugify(display) + "-" + new_uid()[:6],
            description=description,
            source_type=file_type,
            original_filename=filename,
            file_path=str(dest),
            system_key=(system_key or "").upper()[:64] or None,
            status="active",
        )
        session.add(source)
        await session.flush()
    else:
        source.file_type = file_type
        source.metadata_json = {
            **(source.metadata_json or {}),
            "last_file": filename,
            "multipart": True,
        }

    job = ProcessingJob(
        job_uid=new_uid(),
        source_id=source.id,
        filename=filename,
        file_path=str(dest),
        file_type=file_type,
        file_size=size,
        status=UPLOADED,
        chunk_size=settings.CHUNK_SIZE,
        progress=0.0,
    )
    session.add(job)
    await session.commit()
    await session.refresh(source)
    await session.refresh(job)

    background.add_task(
        _dispatch_import, job.id, auto_confirm, mapping_json
    )

    return UploadAccepted(
        job_uid=job.job_uid,
        job_id=job.id,
        source_id=source.id,
        filename=filename,
        file_type=file_type,
        size_bytes=size,
        message="Upload received; profiling and column detection started.",
    )


def _dispatch_import(job_id: int, auto_confirm: bool, mapping_json: str | None) -> None:
    """Fire the Celery pipeline; falls back to inline execution if no broker."""
    from app.workers.tasks import run_import, run_ingest

    try:
        async_result = run_import.apply_async(args=[job_id], queue="ingest")
        if auto_confirm or mapping_json:
            import json as _json

            mapping = None
            if mapping_json:
                try:
                    parsed = _json.loads(mapping_json)
                    mapping = parsed.get("mapping") or parsed
                except _json.JSONDecodeError:
                    logger.warning("could not parse mapping_json; ignoring")
            if mapping:
                run_ingest.apply_async(
                    args=[job_id, mapping, new_uid()], queue="ingest"
                )
        return async_result
    except Exception:  # noqa: BLE001
        # No broker reachable (e.g. running the API without Redis): degrade to a
        # synchronous in-process run so local development still works.
        logger.exception(
            "could not dispatch job %s to celery; running inline", job_id
        )
        try:
            run_import.apply(args=[job_id])
        except Exception:  # noqa: BLE001
            logger.exception("inline import failed for job %s", job_id)


# -------------------------------------------------------------------- jobs ---
@router.get("/jobs/all", response_model=list[JobOut])
async def list_jobs(
    session: AsyncSession = Depends(get_session),
    limit: int = 50,
    source_id: int | None = None,
    status: str | None = None,
) -> list[ProcessingJob]:
    stmt = select(ProcessingJob).order_by(ProcessingJob.id.desc()).limit(
        min(max(limit, 1), 200)
    )
    if source_id is not None:
        stmt = stmt.where(ProcessingJob.source_id == source_id)
    if status:
        stmt = stmt.where(ProcessingJob.status == status)
    return list((await session.execute(stmt)).scalars())


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(
    job_id: int, session: AsyncSession = Depends(get_session)
) -> ProcessingJob:
    job = await session.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@router.get("/jobs/by-uid/{job_uid}", response_model=JobOut)
async def get_job_by_uid(
    job_uid: str, session: AsyncSession = Depends(get_session)
) -> ProcessingJob:
    job = await session.scalar(
        select(ProcessingJob).where(ProcessingJob.job_uid == job_uid)
    )
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
async def retry_job(
    job_id: int, session: AsyncSession = Depends(get_session)
) -> ProcessingJob:
    job = await session.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    job.status = UPLOADED
    job.progress = 0.0
    job.error = None
    job.chunks_completed = 0
    await session.commit()
    from app.workers.tasks import run_import

    try:
        run_import.apply_async(args=[job_id], queue="ingest")
    except Exception:  # noqa: BLE001
        logger.exception("could not dispatch retry for job %s", job_id)
    await session.refresh(job)
    return job


# ---------------------------------------------------------------- exports ---
@router.get("/{source_id}/export.csv")
async def export_source_csv(
    source_id: int, session: AsyncSession = Depends(get_session)
) -> Any:
    """Stream a source's raw rows back out as CSV (raw, not normalized)."""
    import io

    from fastapi.responses import StreamingResponse

    source = await session.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "source not found")

    rows = await session.execute(
        select(SourceRecord.raw_payload, SourceRecord.row_number)
        .where(SourceRecord.source_id == source_id)
        .order_by(SourceRecord.row_number)
    )
    data = list(rows.all())
    if not data:
        raise HTTPException(404, "no records for this source")

    columns: list[str] = []
    for payload, _n in data:
        for k in payload:
            if k not in columns:
                columns.append(k)

    def gen():
        buffer = io.StringIO()
        buffer.write(",".join(_csv(c) for c in columns) + "\n")
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        for payload, _n in data:
            buffer.write(",".join(_csv(payload.get(c)) for c in columns) + "\n")
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)

    return StreamingResponse(
        gen(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{source.slug or source.id}.csv"'},
    )


def _csv(value: Any) -> str:
    if value is None:
        return ""
    s = str(value)
    if any(ch in s for ch in (",", '"', "\n")):
        return '"' + s.replace('"', '""') + '"'
    return s


# ------------------------------------------------------------------ helper ---
async def _rebuild_clusters() -> None:
    try:
        from app.workers.tasks import rebuild_clusters

        rebuild_clusters.apply_async(queue="resolve")
    except Exception:  # noqa: BLE001
        logger.debug("cluster rebuild not dispatched", exc_info=True)


def cleanup_stale_spool(max_age_hours: int = 12) -> int:
    """Remove spool directories abandoned by crashed workers."""
    root = Path(settings.UPLOAD_DIR) / "_chunks"
    if not root.exists():
        return 0
    import time

    removed = 0
    cutoff = time.time() - max_age_hours * 3600
    for d in root.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


@router.get("/{source_id}/summary")
async def source_summary(
    source_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Counts used by the dataset cards in the UI."""
    source = await session.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "source not found")
    records = int(
        (
            await session.execute(
                select(func.count()).select_from(SourceRecord).where(
                    SourceRecord.source_id == source_id
                )
            )
        ).scalar()
        or 0
    )
    entities = int(
        (
            await session.execute(
                select(func.count(distinct(EntityRecord.master_entity_id)))
                .join(SourceRecord, SourceRecord.id == EntityRecord.source_record_id)
                .where(SourceRecord.source_id == source_id)
            )
        ).scalar()
        or 0
    )
    return {
        "source_id": source_id,
        "name": source.name,
        "records": records,
        "entities": entities,
        "imported_rows": source.imported_rows,
        "failed_rows": source.failed_rows,
    }


@router.get("/entities/count")
async def entity_count(session: AsyncSession = Depends(get_session)) -> dict[str, int]:
    total = int(
        (await session.execute(select(func.count()).select_from(MasterEntity))).scalar()
        or 0
    )
    return {"total": total}
