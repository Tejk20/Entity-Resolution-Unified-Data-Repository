"""ORM models mirroring the alembic schema."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        Index("ix_users_email", "email"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), unique=True)
    full_name: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    is_superuser: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Source(Base, TimestampMixin):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str | None] = mapped_column(String(255), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(32), default="csv", server_default="csv")
    original_filename: Mapped[str | None] = mapped_column(String(512))
    file_path: Mapped[str | None] = mapped_column(String(1024))
    system_key: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active")
    total_rows: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    imported_rows: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    failed_rows: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)

    datasets: Mapped[list["Dataset"]] = relationship(
        back_populates="source", cascade="all, delete-orphan", lazy="selectin"
    )
    records: Mapped[list["SourceRecord"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["ProcessingJob"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class Dataset(Base, TimestampMixin):
    __tablename__ = "datasets"
    __table_args__ = (
        UniqueConstraint("source_id", "table_name", name="uq_datasets_source_table"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    table_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="pending", server_default="pending")
    column_mapping: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)
    row_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    indexed_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")

    source: Mapped[Source] = relationship(back_populates="datasets")
    records: Mapped[list["SourceRecord"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )


class SourceRecord(Base):
    __tablename__ = "source_records"
    __table_args__ = (
        UniqueConstraint("dataset_id", "record_hash", name="uq_source_record_dataset_hash"),
        Index("ix_source_records_dataset_row", "dataset_id", "row_number"),
        Index("ix_source_records_batch_id", "batch_id"),
        Index("ix_source_records_ingest_job", "ingest_job_id"),
        Index("ix_source_records_raw_gin", "raw_payload", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    row_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_table: Mapped[str | None] = mapped_column(String(255))
    source_pk: Mapped[str | None] = mapped_column(String(255))
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    clean_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    record_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_hash: Mapped[str | None] = mapped_column(String(64))
    ingest_job_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    source: Mapped[Source] = relationship(back_populates="records")
    dataset: Mapped[Dataset] = relationship(back_populates="records")
    identifiers: Mapped[list["EntityIdentifier"]] = relationship(
        back_populates="record", cascade="all, delete-orphan", lazy="selectin"
    )


class MasterEntity(Base, TimestampMixin):
    __tablename__ = "master_entities"
    __table_args__ = (
        UniqueConstraint("entity_key", name="uq_master_entities_key"),
        Index("ix_master_entities_email", "primary_email"),
        Index("ix_master_entities_phone", "primary_phone"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_key: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(512))
    primary_email: Mapped[str | None] = mapped_column(String(512))
    primary_phone: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active")
    confidence: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    source_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    record_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    identifier_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)
    first_seen: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    links: Mapped[list["EntityRecord"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan", lazy="selectin"
    )


class EntityIdentifier(Base):
    __tablename__ = "entity_identifiers"
    __table_args__ = (
        UniqueConstraint(
            "source_record_id", "canonical_type", "normalized_value",
            name="uq_entity_identifier_unique",
        ),
        Index("ix_entity_identifiers_lookup", "canonical_type", "normalized_value"),
        Index("ix_entity_identifiers_master", "master_entity_id"),
        Index("ix_entity_identifiers_record", "source_record_id"),
        Index("ix_entity_identifiers_dataset", "dataset_id"),
        Index("ix_entity_identifiers_value_only", "normalized_value"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    canonical_type: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(512), nullable=False)
    raw_value: Mapped[str | None] = mapped_column(String(1024))
    source_record_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_records.id", ondelete="CASCADE"), nullable=False
    )
    master_entity_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("master_entities.id", ondelete="SET NULL")
    )
    source_id: Mapped[int | None] = mapped_column(BigInteger)
    dataset_id: Mapped[int | None] = mapped_column(BigInteger)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    record: Mapped[SourceRecord] = relationship(back_populates="identifiers")


class EntityRecord(Base):
    __tablename__ = "entity_records"
    __table_args__ = (
        UniqueConstraint("master_entity_id", "source_record_id", name="uq_entity_records_unique"),
        Index("ix_entity_records_master", "master_entity_id"),
        Index("ix_entity_records_record", "source_record_id"),
        Index("ix_entity_records_dataset", "dataset_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    master_entity_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("master_entities.id", ondelete="CASCADE"), nullable=False
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_records.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[int | None] = mapped_column(BigInteger)
    source_id: Mapped[int | None] = mapped_column(BigInteger)
    discovered_at_hop: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    matched_on_type: Mapped[str | None] = mapped_column(String(32))
    matched_on_value: Mapped[str | None] = mapped_column(String(512))
    match_method: Mapped[str] = mapped_column(
        String(32), default="identifier", server_default="identifier"
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    entity: Mapped[MasterEntity] = relationship(back_populates="links")
    record: Mapped[SourceRecord] = relationship(lazy="selectin")


class ProcessingJob(Base, TimestampMixin):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        UniqueConstraint("job_uid", name="uq_processing_jobs_uid"),
        Index("ix_processing_jobs_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_uid: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[int | None] = mapped_column(BigInteger)
    filename: Mapped[str | None] = mapped_column(String(512))
    file_path: Mapped[str | None] = mapped_column(String(1024))
    file_type: Mapped[str] = mapped_column(String(32), default="csv", server_default="csv")
    file_size: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(32), default="Uploaded", server_default="Uploaded")
    progress: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")
    total_rows: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    processed_rows: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    imported_rows: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    failed_rows: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    dupe_rows: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    chunk_size: Mapped[int] = mapped_column(Integer, default=5000, server_default="5000")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    chunks_completed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    batch_id: Mapped[str | None] = mapped_column(String(64))
    column_mapping: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)
    detected_columns: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)
    mapping_confidence: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list[str] | None] = mapped_column(JSONB, default=list)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)
    celery_task_id: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped[Source] = relationship(back_populates="jobs")


class ResolutionRun(Base):
    __tablename__ = "resolution_runs"
    __table_args__ = (UniqueConstraint("run_uid", name="uq_resolution_runs_uid"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_uid: Mapped[str] = mapped_column(String(64), nullable=False)
    seed_query: Mapped[str] = mapped_column(String(1024), nullable=False)
    seed_type: Mapped[str | None] = mapped_column(String(32))
    master_entity_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), default="completed", server_default="completed")
    hop_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    record_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    source_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    timeline: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
