"""FastAPI application factory."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, ORJSONResponse

from app.core.config import settings

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.db.session import migration_engine
    from app.services.cache import close_redis

    if settings.RUN_MIGRATIONS_ON_STARTUP:
        await _run_migrations()

    # idempotent fallback that guarantees every model (incl. new ones such as
    # ``User``) has a table even when no alembic revision exists yet.
    await _ensure_tables()

    if settings.AUTO_SEED_ON_STARTUP:
        try:
            await _auto_seed()
        except Exception:  # noqa: BLE001
            logger.exception("auto-seed skipped")

    # opportunistic cleanup of spool shards left by crashed workers
    try:
        from app.api.datasets import cleanup_stale_spool

        removed = cleanup_stale_spool()
        if removed:
            logger.info("cleaned %s stale spool directories", removed)
    except Exception:  # noqa: BLE001
        logger.debug("spool cleanup skipped", exc_info=True)

    yield

    await close_redis()
    from app.db.session import dispose_engine

    await dispose_engine()
    await migration_engine.dispose()


async def _run_migrations() -> None:
    """Run alembic migrations programmatically on boot."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from app.db.session import migration_engine

    backend_root = Path(__file__).resolve().parents[1]
    ini = backend_root / "alembic.ini"
    script_location = backend_root / "alembic"

    if not ini.exists():
        logger.warning("alembic.ini not found at %s; skipping migrations", ini)
        return

    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(script_location))
    cfg.set_main_option("sqlalchemy.url", settings.ASYNC_DATABASE_URL)
    cfg.attributes["connection"] = migration_engine

    logger.info("applying database migrations")
    # ``command.upgrade`` is synchronous and ``env.py`` drives its own event
    # loop, so it must not run on the loop uvicorn already has running.
    await asyncio.to_thread(command.upgrade, cfg, "head")
    logger.info("migrations up to date")


async def _ensure_tables() -> None:
    """Auto-create any missing tables from the ORM metadata.

    Uses ``Base.metadata.create_all`` on the async engine; it only creates
    tables that do not already exist, so it is safe to run alongside alembic.
    """
    import app.db.models  # noqa: F401  (register all tables on Base.metadata)
    from app.db.base import Base
    from app.db.session import engine

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("table schema verified (create_all)")
    except Exception:  # noqa: BLE001
        logger.warning("create_all skipped (will rely on migrations)", exc_info=True)


async def _auto_seed() -> None:
    from app.db.session import session_scope
    from app.services.seeding import auto_seed_if_empty

    async with session_scope() as session:
        await auto_seed_if_empty(session)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        description=(
            "Multi-database entity resolution and unified data repository with "
            "progressive entity enrichment: a recursive BFS across imported "
            "datasets that discovers new identifiers (phones, usernames, member "
            "ids) hop by hop until a consolidated master entity emerges, with "
            "exact source traceability for every record."
        ),
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Local-dev convenience: always allow the Vite dev-server origins even if the
    # CORS_ORIGINS env var omits one of them, so http://localhost:5173 and
    # http://127.0.0.1:5173 both work for the frontend.
    cors_origins = [
        *[o for o in (settings.CORS_ORIGINS or []) if o != "*"],
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    cors_origins = list(dict.fromkeys(cors_origins))

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def add_timing(request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Process-Time-Ms"] = str(
            round((time.perf_counter() - t0) * 1000, 2)
        )
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": f"internal error: {type(exc).__name__}"},
        )

    # routes
    from app.api import auth, datasets, mapping, search, stats

    app.include_router(stats.router, prefix=settings.API_V1_PREFIX)
    app.include_router(search.router, prefix=settings.API_V1_PREFIX)
    app.include_router(datasets.router, prefix=settings.API_V1_PREFIX)
    app.include_router(mapping.router, prefix=settings.API_V1_PREFIX)
    app.include_router(auth.router, prefix=settings.API_V1_PREFIX)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": settings.VERSION}

    @app.get("/", tags=["meta"])
    async def root() -> dict[str, Any]:
        return {
            "name": settings.PROJECT_NAME,
            "version": settings.VERSION,
            "docs": "/docs",
            "api": settings.API_V1_PREFIX,
        }

    return app


app = create_app()
