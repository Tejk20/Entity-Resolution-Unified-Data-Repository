"""Worker tasks for the ingestion + resolution pipeline.

Flow for a single upload:

    API                          Worker
    ───                          ─────
    POST /uploads
      save file, create Source
      + ProcessingJob(Uploaded)
      dispatch run_import ───────►  count rows
                                   detect columns, build mapping preview
                                   status = PendingConfirmation
                                        │
    POST /uploads/{id}/confirm ─────┘
      (mapping confirmed)
                                   status = Cleaning
                                   stream chunks ──► chord of ingest_chunk
                                                      (clean + index rows)
                                   status = Indexing
                                   status = Matching
                                   ClusterIndexer.run()  -> master entities
                                   status = Completed
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

from celery import chord
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.common import record_hash, utcnow
from app.core.config import settings
from app.core.constants import (
    CLEANING,
    COMPLETED,
    FAILED,
    INDEXING,
    MAPPING,
    MATCHING,
    PENDING_CONFIRMATION,
)
from app.db.models import (
    Dataset,
    EntityIdentifier,
    ProcessingJob,
    Source,
    SourceRecord,
)
from app.services.field_mapping import build_mapping
from app.services.normalize import dedupe_identifiers, is_null, normalize_field
from app.services.parsers import detect_file_type, iter_chunks, sample_for_mapping
from app.services.resolution import ClusterIndexer
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

#: module-level engine for workers (created lazily, per process)
_engine = None
_Session = None


def _get_sessionmaker():
    global _engine, _Session
    if _Session is None:
        _engine = create_async_engine(
            settings.ASYNC_DATABASE_URL, poolclass=NullPool, pool_pre_ping=True
        )
        _Session = async_sessionmaker(_engine, expire_on_commit=False, autoflush=False)
    return _Session


async def _reset_engine() -> None:
    global _engine, _Session
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _Session = None


def run(coro):
    """Execute an async coroutine from sync Celery task context."""
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# phase 1: profile the upload and produce a mapping preview
# --------------------------------------------------------------------------- #
@celery_app.task(name="app.workers.tasks.run_import", bind=True, max_retries=2)
def run_import(self, job_id: int) -> dict[str, Any]:
    return run(_run_import_async(job_id, task_id=self_id_of(self)))


async def _run_import_async(job_id: int, task_id: str | None = None) -> dict[str, Any]:
    Session = _get_sessionmaker()
    try:
        async with Session() as session:
            job = await session.get(ProcessingJob, job_id)
            if job is None:
                raise ValueError(f"job {job_id} not found")

            job.status = MAPPING
            job.started_at = job.started_at or utcnow()
            job.progress = 0.08
            await session.commit()

            if not job.file_path:
                raise ValueError("upload has no file")

            # Re-sample the file so the preview reflects the real content
            samples = sample_for_mapping(job.file_path, job.file_type, limit=300)
            preview: list[dict[str, Any]] = []
            best: dict[str, str] = {}
            overall_conf = 0.0

            for table, columns, rows in samples:
                res = build_mapping(rows, columns)
                preview.append(
                    {
                        "table_name": table,
                        "mapping": res.mapping,
                        "confidence": round(res.confidence, 4),
                        "columns": [c.as_dict() for c in res.columns],
                        "unmapped_columns": res.unmapped_columns,
                        "ambiguous": res.ambiguous,
                        "sample_rows": [
                            {k: (str(v)[:120] if v is not None else None) for k, v in r.items()}
                            for r in rows[:5]
                        ],
                    }
                )
                if res.confidence >= overall_conf:
                    best = res.mapping
                    overall_conf = res.confidence

            job.detected_columns = {"tables": [p["table_name"] for p in preview]}
            job.column_mapping = {"tables": preview, "default": best}
            job.mapping_confidence = overall_conf
            job.total_rows = await _count_rows(job.file_path, job.file_type)
            job.chunk_size = settings.CHUNK_SIZE
            job.chunk_count = max(
                1,
                (job.total_rows + settings.CHUNK_SIZE - 1) // settings.CHUNK_SIZE,
            ) if job.total_rows else 0
            job.status = PENDING_CONFIRMATION
            job.progress = 0.15
            job.warnings = _warnings_for(preview, best)
            job.celery_task_id = task_id
            await session.commit()

            return {
                "job_id": job_id,
                "status": job.status,
                "total_rows": job.total_rows,
                "chunk_count": job.chunk_count,
                "mapping": best,
                "confidence": overall_conf,
                "celery_task_id": task_id,
            }
    except Exception as exc:  # noqa: BLE001
        logger.exception("run_import failed for job %s", job_id)
        await _mark_failed(job_id, exc)
        raise
    finally:
        await _reset_engine()


def self_id_of(task) -> str | None:
    try:
        return task.request.id
    except Exception:
        return None


def _warnings_for(preview: list[dict[str, Any]], default: dict[str, str]) -> list[str]:
    warnings: list[str] = []
    if not default:
        warnings.append("No canonical fields detected - review mapping before continuing.")
    if "email" not in default and "phone" not in default and "username" not in default:
        warnings.append(
            "Dataset has no email/phone/username column; cross-source matching will be limited."
        )
    for p in preview:
        if p["confidence"] < 0.5:
            warnings.append(
                f"Table '{p['table_name']}' mapped with low confidence "
                f"({p['confidence']:.0%}); please verify."
            )
    return warnings


async def _count_rows(path: str, file_type: str) -> int:
    """Cheap row count that also validates the file parses."""
    count = 0
    try:
        for chunk in iter_chunks(path, file_type, batch_id="count", chunk_size=settings.CHUNK_SIZE):
            count += len(chunk.rows)
    except Exception as exc:
        logger.warning("row count failed: %s", exc)
    return count


# --------------------------------------------------------------------------- #
# phase 2: chunked cleaning + indexing
# --------------------------------------------------------------------------- #
def _spool_dir(batch_id: str) -> Path:
    d = Path(settings.UPLOAD_DIR) / "_chunks" / batch_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _spool_path(batch_id: str, table_name: str, chunk_index: int) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", table_name)[:60] or "table"
    return _spool_dir(batch_id) / f"{safe}.{chunk_index:06d}.ndjson"


@celery_app.task(name="app.workers.tasks.run_ingest", bind=True)
def run_ingest(self, job_id: int, mapping: dict[str, str], batch_id: str) -> dict[str, Any]:
    """Phase A: spool the file into per-chunk NDJSON shards on disk.

    Spooling first (rather than handing the parser to N workers) means each
    chunk task reads exactly one contiguous slice, so total parse work is O(n)
    instead of O(n * chunks), and no worker ever holds a whole file in memory.

    Phase B: fan the shards out with a chord and finalize when all land.
    """
    Session = _get_sessionmaker()

    async def _plan() -> tuple[list[dict[str, Any]], int]:
        async with Session() as session:
            job = await session.get(ProcessingJob, job_id)
            if job is None:
                raise ValueError(f"job {job_id} not found")
            job.status = CLEANING
            job.progress = 0.18
            job.column_mapping = {
                **(job.column_mapping or {}),
                "confirmed": mapping,
            }
            job.batch_id = batch_id
            await session.commit()
            file_path, file_type = job.file_path, job.file_type
            chunk_size = job.chunk_size or settings.CHUNK_SIZE

        plan: list[dict[str, Any]] = []
        total = 0
        seen: dict[str, int] = {}
        for chunk in iter_chunks(file_path, file_type, batch_id=batch_id, chunk_size=chunk_size):
            ordinal = seen.get(chunk.table_name, 0)
            seen[chunk.table_name] = ordinal + 1
            shard = _spool_path(batch_id, chunk.table_name, ordinal)
            with shard.open("w", encoding="utf-8") as fh:
                for i, row in enumerate(chunk.rows):
                    fh.write(
                        json.dumps(
                            {"__row__": chunk.rows[i].get("__row__", total + i + 1),
                             "row": {k: v for k, v in row.items() if k != "__row__"}},
                            default=str,
                        )
                        + "\n"
                    )
            plan.append(
                {
                    "table_name": chunk.table_name,
                    "start_row": total,
                    "row_count": len(chunk.rows),
                    "shard": str(shard),
                }
            )
            total += len(chunk.rows)

        async with Session() as session:
            job = await session.get(ProcessingJob, job_id)
            job.total_rows = total
            job.chunk_count = len(plan)
            job.updated_at = utcnow()
            await session.commit()
        return plan, total

    try:
        plan, total = run(_plan())
    except Exception as exc:  # noqa: BLE001
        logger.exception("run_ingest planning failed for job %s", job_id)
        run(_mark_failed(job_id, exc))
        raise

    if not plan:
        run(_finalize_import_async(job_id, batch_id, 0))
        return {"job_id": job_id, "chunks": 0}

    header = (
        ingest_chunk.s(job_id, batch_id, p["table_name"], p["start_row"],
                       p["row_count"], i, p["shard"])
        for i, p in enumerate(plan)
    )
    callback = finalize_import.s(job_id, batch_id, len(plan))
    chord(header)(callback)
    logger.info("job %s: dispatched %s chunk tasks (%s rows)", job_id, len(plan), total)
    return {"job_id": job_id, "chunks": len(plan), "rows": total, "dispatched": True}


@celery_app.task(
    name="app.workers.tasks.ingest_chunk",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def ingest_chunk(
    self,
    job_id: int,
    batch_id: str,
    table_name: str,
    start_row: int,
    row_count: int,
    chunk_index: int,
    shard_path: str,
) -> dict[str, Any]:
    """
    Clean + persist one pre-spooled chunk.

    Idempotency: rows are keyed by ``(dataset_id, record_hash)`` with an
    ``ON CONFLICT DO NOTHING`` insert. Re-running a job, or importing an
    overlapping slice of a dataset, therefore never duplicates records.
    """
    return run(
        _ingest_chunk_async(
            job_id, batch_id, table_name, start_row, row_count, chunk_index, shard_path
        )
    )


async def _ingest_chunk_async(
    job_id: int,
    batch_id: str,
    table_name: str,
    start_row: int,
    row_count: int,
    chunk_index: int,
    shard_path: str,
) -> dict[str, Any]:
    Session = _get_sessionmaker()
    try:
        async with Session() as session:
            job = await session.get(ProcessingJob, job_id)
            if job is None:
                raise ValueError(f"job {job_id} not found")
            mapping = ((job.column_mapping or {}).get("confirmed")) or {}
            if not mapping:
                raise ValueError("ingest requires a confirmed column mapping")
            source_id = job.source_id
            total_rows = job.total_rows or 0

            # resolve/create the dataset this chunk belongs to
            dataset = await session.scalar(
                select(Dataset).where(
                    Dataset.source_id == source_id, Dataset.table_name == table_name
                )
            )
            if dataset is None:
                dataset = Dataset(
                    source_id=source_id,
                    name=f"{table_name}",
                    table_name=table_name,
                    status="processing",
                    column_mapping=mapping,
                )
                session.add(dataset)
                await session.flush()
            dataset_id = dataset.id

            source = await session.get(Source, source_id)
            system_key = source.system_key if source else None
            weights = dict(settings.IDENTIFIER_WEIGHTS)

            # ---- read this chunk's pre-spooled shard --------------------
            rows: list[dict[str, Any]] = []
            shard = Path(shard_path)
            if shard.exists():
                with shard.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rows.append(json.loads(line)["row"])
                        except (json.JSONDecodeError, KeyError):
                            continue

            if not rows:
                return {"chunk_index": chunk_index, "imported": 0, "identifiers": 0,
                        "skipped": 0, "invalid": 0, "processed": 0}

            # ---- clean + hash -----------------------------------------
            record_rows: list[dict[str, Any]] = []
            ident_payload: list[dict[str, Any]] = []
            invalid_counter: Counter[str] = Counter()
            total_ident = 0

            for i, row in enumerate(rows):
                raw = {k: v for k, v in row.items() if k != "__row__"}
                rownum = start_row + i + 1
                r_hash = record_hash(dataset_id, raw)
                clean: dict[str, Any] = {}
                idents: list[dict[str, Any]] = []

                for ctype, column in mapping.items():
                    if not column or column not in raw:
                        continue
                    raw_value = raw.get(column)
                    res = normalize_field(ctype, raw_value)
                    clean[ctype] = res.normalized
                    if not is_null(raw_value):
                        clean[f"{ctype}__raw"] = str(raw_value)[:500]
                    if res.normalized and ctype in ("email", "phone", "name",
                                                     "username", "member_id"):
                        idents.append(
                            {
                                "canonical_type": ctype,
                                "normalized_value": res.normalized,
                                "raw_value": None if is_null(raw_value) else str(raw_value)[:500],
                                "confidence": weights.get(ctype, 0.5),
                                "is_primary": ctype in ("email", "phone"),
                            }
                        )
                        total_ident += 1
                    elif not res.ok and not is_null(raw_value):
                        invalid_counter[f"{ctype}:{res.note or 'malformed'}"] += 1

                idents = dedupe_identifiers(idents)
                record_rows.append(
                    {
                        "source_id": source_id,
                        "dataset_id": dataset_id,
                        "batch_id": batch_id,
                        "chunk_index": chunk_index,
                        "row_number": rownum,
                        "source_table": table_name,
                        "source_pk": None,
                        "raw_payload": raw,
                        "clean_payload": clean,
                        "record_hash": r_hash,
                        "row_hash": r_hash,
                        "ingest_job_id": job_id,
                        "_idents": idents,
                    }
                )

            # ---- insert records (dedupe-safe) --------------------------
            payload = [{k: v for k, v in r.items() if k != "_idents"} for r in record_rows]
            stmt = (
                pg_insert(SourceRecord)
                .values(payload)
                .on_conflict_do_nothing(constraint="uq_source_record_dataset_hash")
                .returning(SourceRecord.id, SourceRecord.record_hash)
            )
            inserted = (await session.execute(stmt)).all()
            # hash -> id for the rows that were genuinely new
            hash_to_id: dict[str, int] = {r[1]: r[0] for r in inserted}
            skipped = len(record_rows) - len(inserted)

            # Rows that already existed (duplicate slice of a multi-part import)
            # still need their identifiers indexed, so resolve their ids too.
            missing = [
                r["record_hash"] for r in record_rows if r["record_hash"] not in hash_to_id
            ]
            if missing:
                hash_to_id.update(await _find_record_ids(session, dataset_id, missing))

            # ---- insert identifiers ------------------------------------
            new_ident_rows: list[dict[str, Any]] = []
            for rec in record_rows:
                rid = hash_to_id.get(rec["record_hash"])
                if rid is None:
                    continue
                for ident in rec["_idents"]:
                    new_ident_rows.append(
                        {
                            "canonical_type": ident["canonical_type"],
                            "normalized_value": ident["normalized_value"],
                            "raw_value": ident["raw_value"],
                            "source_record_id": rid,
                            "source_id": source_id,
                            "dataset_id": dataset_id,
                            "confidence": ident["confidence"],
                            "is_primary": ident.get("is_primary", False),
                        }
                    )
            if new_ident_rows:
                await session.execute(
                    pg_insert(EntityIdentifier)
                    .values(new_ident_rows)
                    .on_conflict_do_nothing(constraint="uq_entity_identifier_unique")
                )

            dataset.row_count = (dataset.row_count or 0) + len(inserted)
            dataset.indexed_count = (dataset.indexed_count or 0) + len(new_ident_rows)
            await session.flush()

            # ---- progress ----------------------------------------------
            # `chunks_completed` is monotonic (chunks finish out of order) so
            # the bar is derived from it, not from this chunk's position.
            job.status = CLEANING
            job.chunks_completed = (job.chunks_completed or 0) + 1
            done = job.chunks_completed
            planned = max(job.chunk_count or 0, 1)
            job.progress = round(0.20 + (0.60 * min(1.0, done / planned)), 4)
            job.processed_rows = min(total_rows, done * max(row_count, 1))
            job.imported_rows = (job.imported_rows or 0) + len(inserted)
            job.dupe_rows = (job.dupe_rows or 0) + skipped
            job.updated_at = utcnow()

            stats = dict(job.stats or {})
            invalid_map = dict(stats.get("invalid_values") or {})
            for k, v in invalid_counter.items():
                invalid_map[k] = invalid_map.get(k, 0) + v
            # keep the report bounded - it is surfaced in the UI
            stats["invalid_values"] = dict(
                sorted(invalid_map.items(), key=lambda kv: -kv[1])[:25]
            )
            if invalid_counter:
                stats["invalid_cells"] = stats.get("invalid_cells", 0) + sum(
                    invalid_counter.values()
                )
            stats["tables"] = sorted({*stats.get("tables", []), table_name})
            job.stats = stats
            await session.commit()

            return {
                "chunk_index": chunk_index,
                "imported": len(inserted),
                "identifiers": len(new_ident_rows),
                "skipped": skipped,
                "invalid": sum(invalid_counter.values()),
                "processed": len(rows),
                "table": table_name,
            }
    except Exception as exc:  # noqa: BLE001
        logger.exception("ingest_chunk %s failed", chunk_index)
        await _record_chunk_error(job_id, chunk_index, exc)
        raise
    finally:
        await _reset_engine()


async def _find_record_ids(
    session, dataset_id: int, hashes: list[str]
) -> dict[str, int]:
    """Resolve existing record ids for hashes we did not just insert.

    Needed so a re-imported chunk can still attach its identifiers to the
    already-present record rather than silently skipping them.
    """
    if not hashes:
        return {}
    out: dict[str, int] = {}
    for i in range(0, len(hashes), 900):  # stay under the bind-param limit
        window = hashes[i : i + 900]
        rows = await session.execute(
            select(SourceRecord.record_hash, SourceRecord.id).where(
                SourceRecord.dataset_id == dataset_id,
                SourceRecord.record_hash.in_(window),
            )
        )
        out.update({r[0]: r[1] for r in rows.all()})
    return out


async def _record_chunk_error(job_id: int, chunk_index: int, exc: Exception) -> None:
    Session = _get_sessionmaker()
    try:
        async with Session() as session:
            job = await session.get(ProcessingJob, job_id)
            if job is None:
                return
            warnings = list(job.warnings or [])
            warnings.append(f"chunk {chunk_index} failed: {exc}")
            job.warnings = warnings[-50:]
            job.failed_rows = (job.failed_rows or 0) + 1
            job.updated_at = utcnow()
            await session.commit()
    except Exception:  # pragma: no cover
        logger.error("could not record chunk error", exc_info=True)


# --------------------------------------------------------------------------- #
# phase 3: finalize -> index -> cluster
# --------------------------------------------------------------------------- #
@celery_app.task(name="app.workers.tasks.finalize_import", bind=True, max_retries=2)
def finalize_import(self, results, job_id: int, batch_id: str, chunk_total: int) -> dict[str, Any]:
    # `results` is the chord's header return list (unused here); celery 5 always
    # prepends it to the callback arguments.
    return run(_finalize_import_async(job_id, batch_id, chunk_total))


async def _finalize_import_async(
    job_id: int, batch_id: str | None = None, chunk_total: int = 0
) -> dict[str, Any]:
    Session = _get_sessionmaker()
    try:
        async with Session() as session:
            job = await session.get(ProcessingJob, job_id)
            if job is None:
                raise ValueError(f"job {job_id} not found")

            job.status = INDEXING
            job.progress = 0.82
            job.updated_at = utcnow()
            await session.commit()

            dataset_ids = list(
                (
                    await session.execute(
                        select(Dataset.id).where(Dataset.source_id == job.source_id)
                    )
                ).scalars()
            )

        # cluster in a fresh session so the heavy work is not held in one tx
        async with Session() as session:
            indexer = ClusterIndexer(session)
            result = await indexer.run(dataset_ids=dataset_ids)

        async with Session() as session:
            job = await session.get(ProcessingJob, job_id)
            source = await session.get(Source, job.source_id)

            job.status = COMPLETED
            job.progress = 1.0
            job.completed_at = utcnow()
            job.updated_at = utcnow()
            stats = dict(job.stats or {})
            stats["resolution"] = result
            if batch_id:
                stats["batch_id"] = batch_id
            job.stats = stats

            if source is not None:
                source.imported_rows = int(
                    (
                        await session.execute(
                            select(func.count()).select_from(SourceRecord).where(
                                SourceRecord.source_id == source.id
                            )
                        )
                    ).scalar()
                    or 0
                )
                source.total_rows = max(source.total_rows or 0, source.imported_rows)
                for d in source.datasets:
                    d.status = "indexed"
            await session.commit()

        await _invalidate_search_cache()
        return {"job_id": job_id, "status": COMPLETED, "resolution": result}
    except Exception as exc:  # noqa: BLE001
        logger.exception("finalize_import failed for job %s", job_id)
        await _mark_failed(job_id, exc)
        raise
    finally:
        if batch_id:
            shutil.rmtree(_spool_dir(batch_id), ignore_errors=True)
        await _reset_engine()


@celery_app.task(name="app.workers.tasks.rebuild_clusters", bind=True, max_retries=2)
def rebuild_clusters(self, dataset_ids: list[int] | None = None) -> dict[str, Any]:
    async def _go() -> dict[str, Any]:
        Session = _get_sessionmaker()
        async with Session() as session:
            indexer = ClusterIndexer(session)
            return await indexer.run(dataset_ids=dataset_ids)

    try:
        result = run(_go())
        run(_invalidate_search_cache())
        return result
    finally:
        run(_reset_engine())


async def _mark_failed(job_id: int, exc: Exception) -> None:
    try:
        Session = _get_sessionmaker()
        async with Session() as session:
            job = await session.get(ProcessingJob, job_id)
            if job is None:
                return
            job.status = FAILED
            job.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-2000:]}"[:4000]
            job.progress = 1.0
            job.completed_at = utcnow()
            job.updated_at = utcnow()
            await session.commit()
    except Exception:  # pragma: no cover
        logger.error("failed to mark job %s as failed", job_id, exc_info=True)


async def _invalidate_search_cache() -> None:
    try:
        from app.services.cache import cache_invalidate_prefix

        await cache_invalidate_prefix("search:")
    except Exception:  # pragma: no cover
        logger.debug("cache invalidation skipped", exc_info=True)
