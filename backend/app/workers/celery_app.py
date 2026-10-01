"""Celery application: queues, routing, retry and backpressure policy.

Queue layout
------------
``ingest``   CPU/IO-bound chunk parsing, cleaning and index building
``resolve``  clustering + progressive resolution (heavier, lower concurrency)
``default``  catch-all for short tasks (mapping preview, seeding, stats)

Chunk ingestion uses a **chord**: the parent task fans out N chunk tasks and
runs an optional post-processing callback once all have finished. That keeps
progress reporting accurate and prevents one enormous file from monopolising a
worker.
"""
from __future__ import annotations

import os

from celery import Celery
from celery.signals import setup_logging, worker_process_init
from kombu import Exchange, Queue

from app.core.config import settings

BROKER = settings.CELERY_BROKER_URL
BACKEND = settings.CELERY_RESULT_BACKEND

INGEST_Q = Queue("ingest", Exchange("er"), routing_key="ingest", durable=True)
RESOLVE_Q = Queue("resolve", Exchange("er"), routing_key="resolve", durable=True)
DEFAULT_Q = Queue("default", Exchange("er"), routing_key="default", durable=True)

celery_app = Celery("entity_resolution", broker=BROKER, backend=BACKEND)

celery_app.conf.update(
    task_default_queue="default",
    task_default_exchange="er",
    task_default_exchange_type="direct",
    task_queues=(INGEST_Q, RESOLVE_Q, DEFAULT_Q),
    task_default_routing_key="default",
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    result_expires=60 * 60 * 24,
    task_track_started=True,
    task_acks_late=True,                 # redeliver if a worker dies mid-chunk
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,        # fair dispatch for long jobs
    worker_max_tasks_per_child=200,      # bound memory growth on big imports
    task_soft_time_limit=1800,
    task_time_limit=2400,
    task_routes={
        "app.workers.tasks.ingest_chunk": {"queue": "ingest"},
        "app.workers.tasks.run_import": {"queue": "ingest"},
        "app.workers.tasks.finalize_import": {"queue": "ingest"},
        "app.workers.tasks.rebuild_clusters": {"queue": "resolve"},
        "app.workers.tasks.seed_demo_data": {"queue": "resolve"},
        "app.workers.tasks.*": {"queue": "default"},
    },
    task_annotations={
        "app.workers.tasks.ingest_chunk": {
            "rate_limit": "120/m",
            "max_retries": 3,
            "default_retry_delay": 10,
        },
        "app.workers.tasks.rebuild_clusters": {"max_retries": 2},
    },
    broker_connection_retry_on_startup=True,
    result_extended=True,
    worker_send_task_events=True,
)

celery_app.autodiscover_tasks(["app.workers"])


@setup_logging.connect
def _configure_logging(**_kwargs) -> None:
    from logging.config import dictConfig

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                }
            },
            "handlers": {
                "console": {"class": "logging.StreamHandler", "formatter": "standard"}
            },
            "root": {"handlers": ["console"], "level": settings.LOG_LEVEL},
            "loggers": {
                "app": {"handlers": ["console"], "level": settings.LOG_LEVEL,
                        "propagate": False},
                "celery": {"handlers": ["console"], "level": settings.LOG_LEVEL,
                           "propagate": False},
            },
        }
    )


@worker_process_init.connect
def _reset_state(**_kwargs) -> None:
    """Each forked worker gets its own engine/redis pools."""
    for mod in ("app.db.session", "app.services.cache"):
        try:
            import importlib
            import sys

            sys.modules.pop(mod, None)
        except Exception:  # pragma: no cover
            pass


__all__ = ["celery_app", "INGEST_Q", "RESOLVE_Q", "DEFAULT_Q", "os"]
