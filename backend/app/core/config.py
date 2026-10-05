"""Application configuration."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Later files win, so a .env in the current directory (how the dev
        # server is usually launched) overrides the backend/.env default.
        env_file=(str(BASE_DIR / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    PROJECT_NAME: str = "Entity Resolution & Unified Data Repository"
    VERSION: str = "1.0.0"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # --- storage -----------------------------------------------------------
    DATABASE_URL: str = "postgresql+asyncpg://er:er_password@localhost:5432/entity_resolution"
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    UPLOAD_DIR: str = str(BASE_DIR / "data" / "uploads")
    MAX_UPLOAD_BYTES: int = 2 * 1024 * 1024 * 1024  # 2 GB
    # Managed Postgres on the free tiers caps concurrent connections (Render's
    # free instance allows a handful), and the API + celery worker share one
    # service here, so keep the pools small instead of starving the database.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5

    # --- ingestion ---------------------------------------------------------
    CHUNK_SIZE: int = Field(default=5000, ge=1000, le=10000)
    SQL_PARSE_MAX_STATEMENT_BYTES: int = 8 * 1024 * 1024

    # --- resolution engine -------------------------------------------------
    MAX_BFS_DEPTH: int = Field(default=8, ge=1, le=32)
    BFS_BATCH_SIZE: int = Field(default=500, ge=50, le=5000)
    # canonical type -> base confidence weight used in scoring
    IDENTIFIER_WEIGHTS: dict[str, float] = Field(
        default_factory=lambda: {
            "email": 1.0,
            "member_id": 0.95,
            "phone": 0.9,
            "username": 0.75,
            "name": 0.4,
        }
    )
    # identifier types strong enough to transitively join records
    LINKABLE_TYPES: list[str] = Field(
        default_factory=lambda: ["email", "phone", "username", "member_id"]
    )
    SEARCH_CACHE_TTL: int = 300
    RUN_MIGRATIONS_ON_STARTUP: bool = True
    AUTO_SEED_ON_STARTUP: bool = True
    CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:8080"]
    )

    # --- auth ----------------------------------------------------------------
    # Dev-only default; MUST be overridden via JWT_SECRET_KEY in production.
    JWT_SECRET_KEY: str = "change-me-in-production-9f2c1a7b"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRES_MINUTES: int = 10080  # 7 days

    @field_validator("CORS_ORIGINS", "IDENTIFIER_WEIGHTS", "LINKABLE_TYPES", mode="before")
    @classmethod
    def _parse_json_lists(cls, v):
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                return json.loads(s)
            return [x.strip() for x in s.split(",") if x.strip()]
        return v

    @property
    def ASYNC_DATABASE_URL(self) -> str:
        if "+asyncpg" not in self.DATABASE_URL:
            return self.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
        return self.DATABASE_URL


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
