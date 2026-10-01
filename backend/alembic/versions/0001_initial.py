"""initial schema for multi-database entity resolution

Revision ID: 0001_initial
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    # ---------------------------------------------------------------- sources
    op.create_table(
        "sources",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        # csv | sql | manual
        sa.Column("source_type", sa.String(32), nullable=False, server_default="csv"),
        sa.Column("original_filename", sa.String(512), nullable=True),
        sa.Column("file_path", sa.String(1024), nullable=True),
        # A | B | C | D for seeded demo datasets
        sa.Column("system_key", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("total_rows", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("imported_rows", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("failed_rows", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("metadata_json", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("slug", name="uq_sources_slug"),
    )
    op.create_index("ix_sources_system_key", "sources", ["system_key"])

    # ------------------------------------------------------------- datasets
    # A single source (SQL dump / uploaded file) may expose many logical
    # datasets (tables). CSV uploads create exactly one.
    op.create_table(
        "datasets",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.BigInteger, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("table_name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("column_mapping", JSONB, nullable=True),
        sa.Column("row_count", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("indexed_count", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], ondelete="CASCADE", name="fk_datasets_source"
        ),
        sa.UniqueConstraint("source_id", "table_name", name="uq_datasets_source_table"),
    )
    op.create_index("ix_datasets_source_id", "datasets", ["source_id"])

    # -------------------------------------------------------- source_records
    op.create_table(
        "source_records",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.BigInteger, nullable=False),
        sa.Column("dataset_id", sa.BigInteger, nullable=False),
        sa.Column("batch_id", sa.String(64), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column("row_number", sa.BigInteger, nullable=False),
        # original table name inside the dump, when applicable
        sa.Column("source_table", sa.String(255), nullable=True),
        sa.Column("source_pk", sa.String(255), nullable=True),
        # full audit trail: exactly what was uploaded vs. what we computed
        sa.Column("raw_payload", JSONB, nullable=False),
        sa.Column("clean_payload", JSONB, nullable=True),
        sa.Column("record_hash", sa.String(64), nullable=False),
        sa.Column("row_hash", sa.String(64), nullable=True),
        sa.Column("ingest_job_id", sa.BigInteger, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], ondelete="CASCADE", name="fk_sr_source"
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["datasets.id"], ondelete="CASCADE", name="fk_sr_dataset"
        ),
        # dedupe guard: re-importing an overlapping slice of the same dataset
        # must not create duplicate records.
        sa.UniqueConstraint(
            "dataset_id", "record_hash", name="uq_source_record_dataset_hash"
        ),
    )
    op.create_index("ix_source_records_dataset_row", "source_records", ["dataset_id", "row_number"])
    op.create_index("ix_source_records_source_id", "source_records", ["source_id"])
    op.create_index("ix_source_records_batch_id", "source_records", ["batch_id"])
    op.create_index("ix_source_records_ingest_job", "source_records", ["ingest_job_id"])
    op.create_index(
        "ix_source_records_row_hash", "source_records", ["row_hash"], postgresql_where=sa.text("row_hash IS NOT NULL")
    )
    op.create_index("ix_source_records_raw_gin", "source_records", ["raw_payload"], postgresql_using="gin")

    # ------------------------------------------------------- master_entities
    op.create_table(
        "master_entities",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("entity_key", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(512), nullable=True),
        sa.Column("primary_email", sa.String(512), nullable=True),
        sa.Column("primary_phone", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("source_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("record_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("identifier_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("attributes", JSONB, nullable=True),
        sa.Column(
            "first_seen",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("entity_key", name="uq_master_entities_key"),
    )
    op.create_index("ix_master_entities_email", "master_entities", ["primary_email"])
    op.create_index("ix_master_entities_phone", "master_entities", ["primary_phone"])
    op.create_index("ix_master_entities_status", "master_entities", ["status"])

    # ----------------------------------------------------- entity_identifiers
    # (canonical_type, normalized_value) -> source_record_id / master_entity_id
    op.create_table(
        "entity_identifiers",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("canonical_type", sa.String(32), nullable=False),
        sa.Column("normalized_value", sa.String(512), nullable=False),
        # the value exactly as it appeared upstream (audit)
        sa.Column("raw_value", sa.String(1024), nullable=True),
        sa.Column("source_record_id", sa.BigInteger, nullable=False),
        sa.Column("master_entity_id", sa.BigInteger, nullable=True),
        sa.Column("source_id", sa.BigInteger, nullable=True),
        sa.Column("dataset_id", sa.BigInteger, nullable=True),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id"],
            ["source_records.id"],
            ondelete="CASCADE",
            name="fk_ei_source_record",
        ),
        sa.ForeignKeyConstraint(
            ["master_entity_id"],
            ["master_entities.id"],
            ondelete="SET NULL",
            name="fk_ei_master_entity",
        ),
        sa.UniqueConstraint(
            "source_record_id",
            "canonical_type",
            "normalized_value",
            name="uq_entity_identifier_unique",
        ),
    )
    op.create_index(
        "ix_entity_identifiers_lookup",
        "entity_identifiers",
        ["canonical_type", "normalized_value"],
    )
    op.create_index("ix_entity_identifiers_master", "entity_identifiers", ["master_entity_id"])
    op.create_index("ix_entity_identifiers_record", "entity_identifiers", ["source_record_id"])
    op.create_index("ix_entity_identifiers_dataset", "entity_identifiers", ["dataset_id"])
    op.create_index(
        "ix_entity_identifiers_value_only",
        "entity_identifiers",
        ["normalized_value"],
    )

    # --------------------------------------------------------- entity_records
    # Link table: which source records belong to which master entity, and HOW
    # they were matched (provenance for the discovery timeline).
    op.create_table(
        "entity_records",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("master_entity_id", sa.BigInteger, nullable=False),
        sa.Column("source_record_id", sa.BigInteger, nullable=False),
        sa.Column("dataset_id", sa.BigInteger, nullable=True),
        sa.Column("source_id", sa.BigInteger, nullable=True),
        # hop index at which this record was discovered (0 = seed query)
        sa.Column("discovered_at_hop", sa.Integer, nullable=False, server_default="0"),
        # identifier that pulled this record into the entity
        sa.Column("matched_on_type", sa.String(32), nullable=True),
        sa.Column("matched_on_value", sa.String(512), nullable=True),
        sa.Column("match_method", sa.String(32), nullable=False, server_default="identifier"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["master_entity_id"],
            ["master_entities.id"],
            ondelete="CASCADE",
            name="fk_er_master",
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id"],
            ["source_records.id"],
            ondelete="CASCADE",
            name="fk_er_source_record",
        ),
        sa.UniqueConstraint(
            "master_entity_id", "source_record_id", name="uq_entity_records_unique"
        ),
    )
    op.create_index("ix_entity_records_master", "entity_records", ["master_entity_id"])
    op.create_index("ix_entity_records_record", "entity_records", ["source_record_id"])
    op.create_index("ix_entity_records_dataset", "entity_records", ["dataset_id"])

    # ------------------------------------------------------- processing_jobs
    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("job_uid", sa.String(64), nullable=False),
        sa.Column("source_id", sa.BigInteger, nullable=False),
        sa.Column("dataset_id", sa.BigInteger, nullable=True),
        sa.Column("filename", sa.String(512), nullable=True),
        sa.Column("file_path", sa.String(1024), nullable=True),
        sa.Column("file_type", sa.String(32), nullable=False, server_default="csv"),
        sa.Column("file_size", sa.BigInteger, nullable=False, server_default="0"),
        # Uploaded | Mapping | Cleaning | Indexing | Matching | Completed | Failed
        sa.Column("status", sa.String(32), nullable=False, server_default="Uploaded"),
        sa.Column("progress", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("total_rows", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("processed_rows", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("imported_rows", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("failed_rows", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("dupe_rows", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("chunk_size", sa.Integer, nullable=False, server_default="5000"),
        sa.Column("chunk_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("chunks_completed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("batch_id", sa.String(64), nullable=True),
        sa.Column("column_mapping", JSONB, nullable=True),
        sa.Column("detected_columns", JSONB, nullable=True),
        sa.Column("mapping_confidence", sa.Float, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("warnings", JSONB, nullable=True),
        sa.Column("stats", JSONB, nullable=True),
        sa.Column("celery_task_id", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("job_uid", name="uq_processing_jobs_uid"),
    )
    op.create_index("ix_processing_jobs_status", "processing_jobs", ["status"])
    op.create_index("ix_processing_jobs_source", "processing_jobs", ["source_id"])
    op.create_index("ix_processing_jobs_created", "processing_jobs", ["created_at"])

    # -------------------------------------------------------- resolution_runs
    # Persisted progressive-search traces for the step-by-step timeline.
    op.create_table(
        "resolution_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("run_uid", sa.String(64), nullable=False),
        sa.Column("seed_query", sa.String(1024), nullable=False),
        sa.Column("seed_type", sa.String(32), nullable=True),
        sa.Column("master_entity_id", sa.BigInteger, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="completed"),
        sa.Column("hop_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("record_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("source_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("timeline", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("run_uid", name="uq_resolution_runs_uid"),
    )
    op.create_index("ix_resolution_runs_entity", "resolution_runs", ["master_entity_id"])
    op.create_index("ix_resolution_runs_created", "resolution_runs", ["created_at"])


def downgrade() -> None:
    op.drop_table("resolution_runs")
    op.drop_table("processing_jobs")
    op.drop_table("entity_records")
    op.drop_table("entity_identifiers")
    op.drop_table("master_entities")
    op.drop_table("source_records")
    op.drop_table("datasets")
    op.drop_table("sources")
