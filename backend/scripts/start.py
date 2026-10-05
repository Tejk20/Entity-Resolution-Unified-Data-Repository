"""Native process launcher: migrations + Celery worker + uvicorn.

This is the whole backend lifecycle in one plain Python process tree, so the
project runs on Render's *native* Python runtime (no Dockerfile, no shell
entrypoint) and locally the same way.

    python scripts/start.py                 # production: migrate, work, serve
    python scripts/start.py --reload        # local dev: uvicorn hot reload
    python scripts/start.py --api-only      # serve without a worker
    python scripts/start.py --migrate-only  # only apply migrations

Layout of the process tree
--------------------------
    start.py (this file)            <- owns the lifetime of the service
      +-- uvicorn                   <- runs in this process, main thread, so
      |                                SIGTERM reaches it directly (Render
      |                                sends SIGTERM on deploy/restart)
      +-- celery worker (subprocess)- supervised by a thread; restarted with
                                     backoff if it ever dies, so a crashing
                                     worker degrades to "jobs queue up"
                                     instead of taking the API down with it

Why the worker is a subprocess and not a thread: Celery's prefork/solo pools
expect to own their event loop and process table, and the tasks call
``asyncio.run`` per task. Running the worker out-of-process keeps those
lifetimes clean while still letting the API dispatch work over Redis.

Memory notes (free tier has 0.5 GB / 1 CPU)
-------------------------------------------
``CELERY_POOL`` defaults to ``solo`` -- one worker process that executes tasks
inline, so the service holds two interpreters instead of three (prefork would
add a pool parent + N children). ``-Q ingest,resolve,default`` keeps the same
queue routing the Docker topology used. Celery is configured with
``task_acks_late`` + ``task_reject_on_worker_lost``, so a task interrupted by a
restart is redelivered by the broker.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402  (needs sys.path above)

#: how long to wait for a graceful uvicorn shutdown before Render force-kills
GRACEFUL_SHUTDOWN_SECONDS = int(os.environ.get("GRACEFUL_SHUTDOWN_SECONDS", "30"))

#: worker restart backoff (seconds); index 0 is used for the first restart
RESTART_BACKOFF = (2, 5, 10, 20, 30)


def log(message: str, level: str = "INFO") -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] start.py: {message}", flush=True)


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    # Celery resolves the app by module path, so make sure the backend package
    # is importable no matter what cwd the platform hands us.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(BACKEND_ROOT), env["PYTHONPATH"]] if env.get("PYTHONPATH") else [str(BACKEND_ROOT)]
    )
    return env


# --------------------------------------------------------------------------- #
# migrations
# --------------------------------------------------------------------------- #
def run_migrations() -> None:
    """Apply alembic migrations to ``head``.

    Non-fatal on purpose: ``app.main``'s lifespan hook re-runs the same
    migrations (plus a ``create_all`` safety net) when the service boots, so a
    failure here still lets the process listen on ``$PORT`` and report the real
    error on ``/health`` instead of crash-looping before it can be diagnosed.
    """
    log("applying database migrations (alembic upgrade head)")
    started = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_ROOT),
        env=_child_env(),
        check=False,
    )
    if result.returncode == 0:
        log(f"migrations up to date in {time.perf_counter() - started:.1f}s")
    else:
        log(
            f"alembic upgrade exited {result.returncode}; deferring to app startup",
            "WARNING",
        )


# --------------------------------------------------------------------------- #
# celery worker supervision
# --------------------------------------------------------------------------- #
def worker_command() -> list[str]:
    pool = os.environ.get("CELERY_POOL", "solo").strip() or "solo"
    cmd = [
        sys.executable,
        "-u",
        "-m",
        "celery",
        "-A",
        "app.workers.celery_app:celery_app",
        "worker",
        "--loglevel=" + settings.LOG_LEVEL.lower(),
        "--pool=" + pool,
        "-Q",
        "ingest,resolve,default",
        "--without-gossip",
        "--without-mingle",
    ]
    if pool.startswith("prefork") or pool.startswith("eventlet") or pool.startswith("gevent"):
        cmd += ["--concurrency=" + os.environ.get("CELERY_CONCURRENCY", "1")]
    return cmd


class WorkerSupervisor(threading.Thread):
    """Keeps exactly one Celery worker alive for the life of the service."""

    def __init__(self, stop: threading.Event) -> None:
        super().__init__(name="celery-supervisor", daemon=True)
        self._stop = stop
        self._proc: subprocess.Popen[bytes] | None = None

    # -- lifecycle ---------------------------------------------------------- #
    def run(self) -> None:  # pragma: no cover - process orchestration
        attempt = 0
        while not self._stop.is_set():
            self._proc = subprocess.Popen(
                worker_command(), cwd=str(BACKEND_ROOT), env=_child_env()
            )
            log(f"celery worker started (pid={self._proc.pid})")
            attempt = 0

            code = self._proc.wait()
            if self._stop.is_set():
                break
            # A crash must not take the API down: queue the task back (acks_late)
            # and come back up.
            delay = RESTART_BACKOFF[min(attempt, len(RESTART_BACKOFF) - 1)]
            attempt += 1
            log(
                f"celery worker exited with code {code}; restarting in {delay}s",
                "WARNING",
            )
            self._stop.wait(delay)

        log("celery supervisor loop finished")

    # -- shutdown ----------------------------------------------------------- #
    def stop(self, timeout: float = 20.0) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        log(f"stopping celery worker (pid={proc.pid})")
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log("celery worker ignored SIGTERM; killing", "WARNING")
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover
                pass


# --------------------------------------------------------------------------- #
# api
# --------------------------------------------------------------------------- #
def serve(reload: bool) -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    log(f"serving FastAPI on 0.0.0.0:{port} (reload={reload}, pid={os.getpid()})")
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=reload,
        # Render terminates the instance over TLS termination + a private edge,
        # so trust the forwarded client protocol/host headers.
        proxy_headers=True,
        forwarded_allow_ips="*",
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
        log_level=settings.LOG_LEVEL.lower(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reload", action="store_true", help="uvicorn autoreload (dev)")
    parser.add_argument("--api-only", action="store_true", help="skip the celery worker")
    parser.add_argument(
        "--migrate-only", action="store_true", help="apply migrations and exit"
    )
    args = parser.parse_args(argv)

    log(
        f"database={settings.ASYNC_DATABASE_URL.split('@')[-1]} "
        f"uploads={settings.UPLOAD_DIR} log_level={settings.LOG_LEVEL}"
    )

    if settings.RUN_MIGRATIONS_ON_STARTUP:
        run_migrations()
    else:
        log("RUN_MIGRATIONS_ON_STARTUP is false; skipping alembic", "WARNING")

    if args.migrate_only:
        return 0

    stop = threading.Event()
    supervisor: WorkerSupervisor | None = None
    if not args.api_only:
        supervisor = WorkerSupervisor(stop)
        supervisor.start()

    def _bail(signum, _frame):  # pragma: no cover - signal path
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _bail)
        except (ValueError, OSError):  # pragma: no cover - non-main thread
            pass

    try:
        serve(reload=args.reload)
    finally:
        stop.set()
        if supervisor is not None:
            supervisor.join(timeout=5)
            supervisor.stop()
        log("shutdown complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
