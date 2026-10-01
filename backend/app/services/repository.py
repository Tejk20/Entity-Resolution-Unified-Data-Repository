"""Repository-level operations: stats, entity assembly, record browsing."""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import FAILED
from app.db.models import (
    Dataset,
    EntityIdentifier,
    EntityRecord,
    MasterEntity,
    ProcessingJob,
    ResolutionRun,
    Source,
    SourceRecord,
)


async def dashboard_stats(session: AsyncSession) -> dict[str, Any]:
    t0 = time.perf_counter()

    total_datasets = int(
        (await session.execute(select(func.count(Dataset.id)))).scalar() or 0
    )
    total_sources = int(
        (await session.execute(select(func.count(Source.id)))).scalar() or 0
    )
    total_records = int(
        (await session.execute(select(func.count(SourceRecord.id)))).scalar() or 0
    )
    total_identifiers = int(
        (await session.execute(select(func.count(EntityIdentifier.id)))).scalar() or 0
    )
    total_entities = int(
        (await session.execute(select(func.count(MasterEntity.id)))).scalar() or 0
    )

    # matched entities = entities spanning >1 source record (a real merge)
    matched = int(
        (
            await session.execute(
                select(func.count(distinct(EntityRecord.master_entity_id))).where(
                    EntityRecord.master_entity_id.isnot(None)
                )
            )
        ).scalar()
        or 0
    )
    merged_entities = int(
        (
            await session.execute(
                select(func.count()).select_from(MasterEntity).where(
                    MasterEntity.record_count > 1
                )
            )
        ).scalar()
        or 0
    )

    total_failures = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(ProcessingJob.failed_rows), 0)).where(
                    ProcessingJob.status == FAILED
                )
            )
        ).scalar()
        or 0
    )
    failed_jobs = int(
        (
            await session.execute(
                select(func.count()).select_from(ProcessingJob).where(
                    ProcessingJob.status == FAILED
                )
            )
        ).scalar()
        or 0
    )
    active_jobs = int(
        (
            await session.execute(
                select(func.count()).select_from(ProcessingJob).where(
                    ProcessingJob.status.notin_([FAILED, "Completed"])
                )
            )
        ).scalar()
        or 0
    )

    exec_row = (
        await session.execute(
            select(
                func.coalesce(func.avg(
                    func.extract("epoch", ProcessingJob.completed_at - ProcessingJob.started_at)
                ), 0),
                func.coalesce(func.max(
                    func.extract("epoch", ProcessingJob.completed_at - ProcessingJob.started_at)
                ), 0),
                func.coalesce(func.sum(
                    func.extract("epoch", ProcessingJob.completed_at - ProcessingJob.started_at)
                ), 0),
            ).where(ProcessingJob.completed_at.isnot(None))
        )
    ).one()
    avg_exec, max_exec, total_exec = float(exec_row[0] or 0), float(exec_row[1] or 0), float(exec_row[2] or 0)

    search_row = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.avg(ResolutionRun.duration_ms), 0),
                func.coalesce(func.avg(ResolutionRun.hop_count), 0),
            ).select_from(ResolutionRun)
        )
    ).one()

    dupes = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(ProcessingJob.dupe_rows), 0))
            )
        ).scalar()
        or 0
    )

    return {
        "total_datasets": total_datasets,
        "total_sources": total_sources,
        "total_records": total_records,
        "total_identifiers": total_identifiers,
        "total_entities": total_entities,
        "matched_entities": merged_entities,
        "linked_records": matched,
        "processing_failures": total_failures + failed_jobs,
        "failed_jobs": failed_jobs,
        "active_jobs": active_jobs,
        "duplicate_rows_skipped": dupes,
        "execution_time": {
            "avg_seconds": round(avg_exec, 3),
            "max_seconds": round(max_exec, 3),
            "total_seconds": round(total_exec, 3),
        },
        "search": {
            "total_runs": int(search_row[0] or 0),
            "avg_duration_ms": round(float(search_row[1] or 0), 2),
            "avg_hops": round(float(search_row[2] or 0), 2),
        },
        "computed_in_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


async def identifier_type_breakdown(session: AsyncSession) -> list[dict[str, Any]]:
    rows = await session.execute(
        select(
            EntityIdentifier.canonical_type,
            func.count(),
            func.count(distinct(EntityIdentifier.normalized_value)),
        )
        .group_by(EntityIdentifier.canonical_type)
        .order_by(func.count().desc())
    )
    return [
        {"type": r[0], "count": r[1], "distinct_values": r[2]} for r in rows.all()
    ]


async def source_breakdown(session: AsyncSession) -> list[dict[str, Any]]:
    rows = await session.execute(
        select(
            Source.id,
            Source.name,
            Source.system_key,
            Source.source_type,
            func.count(SourceRecord.id),
            func.count(distinct(EntityRecord.master_entity_id)),
        )
        .select_from(Source)
        .outerjoin(SourceRecord, SourceRecord.source_id == Source.id)
        .outerjoin(
            EntityRecord, EntityRecord.source_record_id == SourceRecord.id
        )
        .group_by(Source.id, Source.name, Source.system_key, Source.source_type)
        .order_by(Source.id)
    )
    return [
        {
            "source_id": r[0],
            "name": r[1],
            "system_key": r[2],
            "source_type": r[3],
            "records": r[4],
            "entities": r[5],
        }
        for r in rows.all()
    ]


async def build_entity_view(session: AsyncSession, entity_id: int) -> dict[str, Any] | None:
    entity = await session.get(MasterEntity, entity_id)
    if entity is None:
        return None

    rows = await session.execute(
        select(
            EntityRecord.source_record_id,
            EntityRecord.discovered_at_hop,
            EntityRecord.matched_on_type,
            EntityRecord.matched_on_value,
            EntityRecord.match_method,
            SourceRecord.raw_payload,
            SourceRecord.clean_payload,
            SourceRecord.source_table,
            SourceRecord.row_number,
            Source.name,
            Source.system_key,
            Dataset.name,
        )
        .join(SourceRecord, SourceRecord.id == EntityRecord.source_record_id)
        .join(Source, Source.id == SourceRecord.source_id)
        .outerjoin(Dataset, Dataset.id == SourceRecord.dataset_id)
        .where(EntityRecord.master_entity_id == entity_id)
        .order_by(EntityRecord.discovered_at_hop, SourceRecord.id)
    )

    records: list[dict[str, Any]] = []
    for r in rows.all():
        records.append(
            {
                "source_record_id": r[0],
                "discovered_at_hop": r[1],
                "matched_on": (
                    {"type": r[2], "value": r[3]} if r[2] else None
                ),
                "match_method": r[4],
                "raw": r[5],
                "clean": r[6],
                "source_table": r[7],
                "row_number": r[8],
                "source_name": r[9],
                "system_key": r[10],
                "dataset_name": r[11],
            }
        )

    idents = await session.execute(
        select(
            EntityIdentifier.canonical_type,
            EntityIdentifier.normalized_value,
            EntityIdentifier.raw_value,
            func.count(),
        )
        .where(EntityIdentifier.master_entity_id == entity_id)
        .group_by(
            EntityIdentifier.canonical_type,
            EntityIdentifier.normalized_value,
            EntityIdentifier.raw_value,
        )
        .order_by(func.count().desc())
    )
    identifiers: dict[str, list[dict[str, Any]]] = {}
    for ctype, value, raw, cnt in idents.all():
        identifiers.setdefault(ctype, []).append(
            {"value": value, "raw": raw, "record_count": cnt}
        )

    by_source: dict[str, int] = {}
    for rec in records:
        by_source[rec["source_name"]] = by_source.get(rec["source_name"], 0) + 1

    by_hop: dict[int, int] = {}
    for rec in records:
        by_hop[rec["discovered_at_hop"]] = by_hop.get(rec["discovered_at_hop"], 0) + 1

    return {
        "id": entity.id,
        "entity_key": entity.entity_key,
        "display_name": entity.display_name,
        "primary_email": entity.primary_email,
        "primary_phone": entity.primary_phone,
        "status": entity.status,
        "confidence": entity.confidence,
        "source_count": entity.source_count,
        "record_count": entity.record_count,
        "identifier_count": entity.identifier_count,
        "attributes": entity.attributes or {},
        "first_seen": entity.first_seen.isoformat() if entity.first_seen else None,
        "last_seen": entity.last_seen.isoformat() if entity.last_seen else None,
        "identifiers": identifiers,
        "records": records,
        "source_breakdown": by_source,
        "hop_breakdown": {str(k): v for k, v in sorted(by_hop.items())},
    }
