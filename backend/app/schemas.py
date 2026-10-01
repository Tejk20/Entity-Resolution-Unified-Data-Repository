"""Pydantic API schemas."""
from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.constants import UPLOADED


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- sources ---
class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    system_key: str | None = Field(default=None, max_length=64)
    # when re-importing into an existing source (multi-part import)
    source_id: int | None = None


class SourceOut(ORMModel):
    id: int
    name: str
    slug: str | None
    description: str | None
    source_type: str
    original_filename: str | None
    system_key: str | None
    status: str
    total_rows: int
    imported_rows: int
    failed_rows: int
    metadata_json: dict[str, Any] | None = None
    created_at: dt.datetime | None = None


class DatasetOut(ORMModel):
    id: int
    source_id: int
    name: str
    table_name: str | None
    status: str
    row_count: int
    indexed_count: int
    column_mapping: dict[str, Any] | None = None


class SourceDetail(SourceOut):
    datasets: list[DatasetOut] = Field(default_factory=list)


# ------------------------------------------------------------------- jobs ---
class JobOut(ORMModel):
    id: int
    job_uid: str
    source_id: int
    dataset_id: int | None
    filename: str | None
    file_type: str
    file_size: int
    status: str
    progress: float
    total_rows: int
    processed_rows: int
    imported_rows: int
    failed_rows: int
    dupe_rows: int
    chunk_size: int
    chunk_count: int
    chunks_completed: int
    column_mapping: dict[str, Any] | None = None
    detected_columns: dict[str, Any] | None = None
    mapping_confidence: float | None = None
    error: str | None
    warnings: list[str] | None = None
    stats: dict[str, Any] | None = None
    celery_task_id: str | None = None
    created_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None
    started_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None


class MappingConfirm(BaseModel):
    """Operator-approved column mapping, per table.

    ``mapping`` keys are canonical field names; values are source column names.
    """
    mapping: dict[str, str] = Field(default_factory=dict)
    #: optional per-table overrides keyed by table name
    table_mappings: dict[str, dict[str, str]] = Field(default_factory=dict)
    skip: bool = False

    @field_validator("mapping")
    @classmethod
    def _check_keys(cls, v: dict[str, str]) -> dict[str, str]:
        allowed = {
            "email", "phone", "name", "username", "member_id", "address", "company",
        }
        bad = set(v) - allowed
        if bad:
            raise ValueError(f"unknown canonical fields: {sorted(bad)}")
        return {k: val for k, val in v.items() if val}


class UploadAccepted(BaseModel):
    job_uid: str
    job_id: int
    source_id: int
    filename: str
    file_type: str
    size_bytes: int
    status: str = UPLOADED
    message: str = "Upload received; profiling started."


# ----------------------------------------------------------------- search ---
class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1024)
    max_depth: int | None = Field(default=None, ge=1, le=32)
    include_weak: bool = True
    persist: bool = True


class HopOut(BaseModel):
    hop: int
    seed_identifiers: list[dict[str, Any]] = Field(default_factory=list)
    matched_records: int
    new_records: int
    new_identifiers: list[dict[str, Any]] = Field(default_factory=list)
    datasets_touched: list[str] = Field(default_factory=list)
    sources_touched: list[str] = Field(default_factory=list)
    duration_ms: int = 0


class SearchResponse(BaseModel):
    query: str
    seed_type: str
    seed_value: str | None
    found: bool
    run_uid: str | None = None
    master_entity_id: int | None = None
    entity: dict[str, Any] | None = None
    timeline: list[HopOut] = Field(default_factory=list)
    identifiers: list[dict[str, Any]] = Field(default_factory=list)
    source_breakdown: dict[str, int] = Field(default_factory=dict)
    hop_breakdown: dict[str, int] = Field(default_factory=dict)
    record_count: int
    source_count: int
    hop_count: int
    duration_ms: int
    cached: bool = False
    exhausted: bool = True
    truncated: bool = False


# --------------------------------------------------------------- entities ---
class EntityOut(ORMModel):
    id: int
    entity_key: str
    display_name: str | None
    primary_email: str | None
    primary_phone: str | None
    status: str
    confidence: float
    source_count: int
    record_count: int
    identifier_count: int
    attributes: dict[str, Any] | None = None
    first_seen: dt.datetime | None = None
    last_seen: dt.datetime | None = None


class EntityListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[EntityOut] = Field(default_factory=list)


# ------------------------------------------------------------------ stats ---
class StatsResponse(BaseModel):
    total_datasets: int
    total_sources: int
    total_records: int
    total_identifiers: int
    total_entities: int
    matched_entities: int
    linked_records: int
    processing_failures: int
    failed_jobs: int
    active_jobs: int
    duplicate_rows_skipped: int
    execution_time: dict[str, Any]
    search: dict[str, Any]
    computed_in_ms: float


class HealthResponse(BaseModel):
    status: str
    database: str
    redis: str
    worker: str
    version: str


# ------------------------------------------------------------------- auth -----
class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=6, max_length=1024)
    username: str | None = Field(default=None, min_length=2, max_length=64)
    full_name: str | None = Field(default=None, max_length=255)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not v or "@" not in v:
            raise ValueError("a valid email address is required")
        return v


class UserLogin(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        return v.strip().lower()


class UserOut(ORMModel):
    id: int
    email: str
    username: str | None
    full_name: str | None
    is_active: bool
    is_superuser: bool
    created_at: dt.datetime | None = None


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
