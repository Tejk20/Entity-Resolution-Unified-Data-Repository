"""Seeding service: write demo files to disk and load them through the real
ingestion pipeline (so seeded data is indistinguishable from an upload).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import new_uid
from app.core.config import settings
from app.core.constants import COMPLETED, PENDING_CONFIRMATION, UPLOADED
from app.db.models import ProcessingJob, Source
from app.services.seed_builder import render_dataset, verify_bridge
from app.services.seed_datasets import ALL_SEED_SPECS, DATASETS, DEMO_QUERY, DatasetSpec

logger = logging.getLogger(__name__)


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def seed_dir() -> Path:
    d = Path(settings.UPLOAD_DIR) / "seeds"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def materialize_seed_files(fmt: str = "csv") -> list[Path]:
    """Render every demo dataset to disk in the requested format."""
    out: list[Path] = []
    for spec in ALL_SEED_SPECS:
        if fmt not in spec.formats:
            continue
        path = seed_dir() / f"{slugify(spec.label)}.{fmt}"
        path.write_text(render_dataset(spec, fmt), encoding="utf-8")  # type: ignore[arg-type]
        out.append(path)
    return out


async def create_seed_source(
    session: AsyncSession, spec: DatasetSpec, file_path: Path, file_type: str
) -> tuple[Source, ProcessingJob]:
    """Create the Source + ProcessingJob rows for a demo dataset."""
    slug = f"demo-{spec.system_key.lower()}"
    source = await session.scalar(select(Source).where(Source.slug == slug))
    if source is None:
        source = Source(
            name=spec.label,
            slug=slug,
            description=spec.description,
            source_type=file_type,
            original_filename=file_path.name,
            file_path=str(file_path),
            system_key=spec.system_key,
            status="active",
            total_rows=len(spec.rows),
            metadata_json={
                "seeded": True,
                "table_name": spec.table_name,
                "expected_mapping": spec.expected_mapping,
                "system_key": spec.system_key,
            },
        )
        session.add(source)
        await session.flush()
    else:
        source.file_path = str(file_path)
        source.original_filename = file_path.name
        source.metadata_json = {
            **(source.metadata_json or {}),
            "seeded": True,
            "table_name": spec.table_name,
            "expected_mapping": spec.expected_mapping,
        }

    job = ProcessingJob(
        job_uid=new_uid(),
        source_id=source.id,
        filename=file_path.name,
        file_path=str(file_path),
        file_type=file_type,
        file_size=file_path.stat().st_size,
        status=UPLOADED,
        chunk_size=settings.CHUNK_SIZE,
        progress=0.0,
        stats={"seeded": True, "system_key": spec.system_key},
    )
    session.add(job)
    await session.commit()
    return source, job


def seed_files_exist() -> bool:
    return all(
        (seed_dir() / f"{slugify(s.label)}.{_fmt_for(s)}").exists()
        for s in ALL_SEED_SPECS
    )


def _fmt_for(spec: DatasetSpec) -> str:
    return spec.default_format


async def seed_all(
    session: AsyncSession, *, fmt: str = "csv", force: bool = False
) -> dict[str, Any]:
    """Create sources/jobs for the demo datasets.

    Does **not** run the workers inline - the API layer dispatches Celery tasks
    so the demo exercises the same asynchronous pipeline as a real upload.
    """
    from app.workers.tasks import run_import  # local import avoids a cycle

    files = await materialize_seed_files(fmt)
    created: list[dict[str, Any]] = []

    for spec, path in zip(ALL_SEED_SPECS, files):
        file_type = path.suffix.lstrip(".").lower()
        if file_type == "tsv":
            file_type = "csv"
        source, job = await create_seed_source(session, spec, path, file_type)
        if force:
            job.status = UPLOADED
            job.progress = 0.0
        async_task = run_import.apply_async(args=[job.id], queue="ingest")
        job.celery_task_id = async_task.id
        await session.commit()
        created.append(
            {
                "system_key": spec.system_key,
                "label": spec.label,
                "source_id": source.id,
                "job_id": job.id,
                "job_uid": job.job_uid,
                "file": path.name,
                "rows": len(spec.rows),
                "task_id": async_task.id,
            }
        )

    return {
        "created": created,
        "demo_query": DEMO_QUERY,
        "expected_hops": 4,
        "bridge_check": verify_bridge(),
    }


def seed_summary() -> dict[str, Any]:
    return {
        "demo_query": DEMO_QUERY,
        "datasets": [
            {
                "system_key": s.system_key,
                "label": s.label,
                "description": s.description,
                "columns": s.columns,
                "rows": len(s.rows),
                "expected_mapping": s.expected_mapping,
            }
            for s in DATASETS
        ],
        "extra": [
            {
                "system_key": s.system_key,
                "label": s.label,
                "description": s.description,
                "format": s.default_format,
                "rows": len(s.rows),
            }
            for s in ALL_SEED_SPECS
            if s not in DATASETS
        ],
    }


async def auto_seed_if_empty(session: AsyncSession) -> bool:
    """Used on first boot so the dashboard is never empty."""
    existing = await session.scalar(select(Source.id).limit(1))
    if existing is not None:
        return False
    logger.info("no sources found - seeding demo datasets")
    await seed_all(session, fmt="csv")
    return True
