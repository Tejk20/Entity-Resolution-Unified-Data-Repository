# Deployment without Docker

The whole stack runs as plain processes:

| Piece        | Local                  | Render (free)                     | Vercel        |
| ------------ | ---------------------- | --------------------------------- | ------------- |
| API + worker | `python scripts/start.py` | native Python web service        | –             |
| Postgres     | local install          | managed `er-database`             | –             |
| Redis        | local install          | managed Key Value `er-kv`         | –             |
| UI           | `npm run dev`          | –                                 | static build  |

No `Dockerfile`, no `docker-compose.yml`, no `nginx.conf`: `backend/scripts/start.py`
is the only entrypoint, for local dev and production alike.

## Local development (no Docker)

Install Postgres and Redis natively, then point the env at them.

**Windows**

1. Postgres: <https://www.postgresql.org/download/windows/> → default install.
   ```powershell
   & "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -c "CREATE USER er WITH PASSWORD 'er_password' CREATEDB;"
   & "C:\Program Files\PostgreSQL\16\bin\createdb.exe" -U postgres -O er entity_resolution
   ```
2. Redis: Memurai (Windows-native Redis) or
   <https://github.com/microsoftarchive/redis/releases> → `redis-server.exe`.
3. `copy .env.example .env` and edit `DATABASE_URL` / `REDIS_URL` if your
   ports or credentials differ.

**macOS / Linux**

```bash
brew install postgresql@16 redis && brew services start postgresql@16 redis
createdb -O "$USER" entity_resolution
cp .env.example .env
```

Run the stack:

```bash
cd backend
pip install -r requirements.txt
python scripts/start.py --reload     # alembic migrations + celery worker + uvicorn on :8000
```

```bash
cd frontend
npm install
npm run dev                          # http://localhost:5173, /api proxied to :8000
```

Launcher flags: `--reload` (uvicorn autoreload), `--api-only` (no worker),
`--migrate-only` (apply migrations and exit).

`.env` is read from the current directory first, then `backend/.env`, so it can
live in either place. Environment variables always win over both.

## Render (backend)

`render.yaml` is a Blueprint that provisions all three resources. **No Docker
is used anywhere** — the service is on the native Python runtime
(`buildCommand: pip install -r requirements.txt`, `startCommand: python
scripts/start.py`).

1. Push this branch, then in Render: **New → Blueprint** → select the repo.
   Render reads `render.yaml` and creates `er-kv`, `er-database` and `er-api`.
2. Set `CORS_ORIGINS` on `er-api` to your Vercel origin (render.yaml ships a
   placeholder):
   ```
   ["https://your-app.vercel.app","http://localhost:5173","http://127.0.0.1:5173"]
   ```
   Blueprint edits can be made in the dashboard; the value will be overwritten
   on the next `render.yaml` sync, so keep render.yaml in sync.
3. Wait for the deploy. The log should show:
   `applying database migrations` → `migrations up to date` →
   `celery worker started` → `serving FastAPI on 0.0.0.0:<port>`.
4. Check `https://er-api.onrender.com/health`:
   `{"status":"ok","database":"up","broker":"up",...}`.

Using the official CLI (optional — Blueprints are created from the dashboard):

```bash
winget install render.cli        # Windows
brew install render              # macOS
render login                     # opens a browser to authorise
render blueprints validate       # checks render.yaml before you sync it
render deploys create <service>  # re-trigger a deploy after a push
```

Note: the npm package named `render-cli` is an unrelated Rails templating
tool — do not install it.

## Vercel (frontend)

`vercel.json` sets the Vite framework preset, `dist` as the output directory and
the SPA rewrite. The static build talks to Render directly, so:

1. `npm i -g vercel && cd frontend && vercel link`
2. Set the API origin **before** building (VITE_ vars are inlined at build time):
   ```bash
   vercel env add VITE_API_BASE_URL production   # -> https://er-api.onrender.com
   ```
   or Project → Settings → Environment Variables.
3. `vercel --prod` from the repo root (it honours `vercel.json`).

No trailing slash, no `/api/v1` suffix — the client appends `/api/v1` itself.

## Environment reference

| Variable | Where it comes from | Notes |
| --- | --- | --- |
| `PYTHON_VERSION` | render.yaml | pinned 3.12.8 (also `backend/.python-version`) |
| `DATABASE_URL` | `fromDatabase: er-database` | normalised to `+asyncpg` in `app/core/config.py` |
| `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | `fromService: er-kv` | all three point at the same Key Value (db 0) |
| `UPLOAD_DIR` | render.yaml | `/tmp/er-uploads`; the free disk is ephemeral |
| `CELERY_POOL` | render.yaml | `solo` — one worker process, fits in 0.5 GB |
| `CORS_ORIGINS` | render.yaml + your edit | JSON array of allowed origins |
| `JWT_SECRET_KEY` | `generateValue: true` | never reuse the dev default |
| `AUTO_SEED_ON_STARTUP` | render.yaml | `false` — it dispatches tasks and races migrations |
| `RUN_MIGRATIONS_ON_STARTUP` | render.yaml | `true` — plus the launcher runs alembic first |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | defaults 5/5 | free Postgres caps concurrent connections |
| `VITE_API_BASE_URL` | Vercel env var | Render origin, no path |

## Free-tier constraints to keep in mind

- **Cold start.** Free web services idle out after 15 min; the first request can
  take ~50 s. `frontend/src/lib/api.js` allows a 120 s timeout for this.
- **Ephemeral disk.** Uploads and the ingest spool under `UPLOAD_DIR` disappear
  on every redeploy or spin-down. Imported rows live in Postgres, so only a
  half-finished job that spans a restart needs to be re-run (the job row stays
  `failed`/`cleaning`; re-upload the file).
- **Single instance.** Celery beat is not running (there is no periodic
  schedule defined in `app/workers/celery_app.py`); the worker is supervised in
  process by `scripts/start.py`.
- **Free Postgres expires after 30 days.** Migrate to a paid/discounted plan or
  an external database (Neon/Supabase) and update `DATABASE_URL`; nothing else
  changes.
- **Key Value is capped at 25 MB** with `noeviction`, so queued tasks survive a
  full disk by failing loudly instead of being dropped silently.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Build fails on `pip install` | Check the log for a compiled C extension; pin the failing package or bump `PYTHON_VERSION`. All current deps ship 3.12 wheels. |
| `Build failed` on Vercel | `VITE_API_BASE_URL` missing is *not* a build failure; the console warning explains it. Real failures are usually the install step. |
| CORS error in the browser | `CORS_ORIGINS` does not contain the exact Vercel origin (scheme + host, no trailing slash). |
| Requests 404 on Vercel | `VITE_API_BASE_URL` was not set at build time, so the bundle calls its own origin. Re-deploy after adding the env var. |
| `/health` returns 503 | `database: down` means `DATABASE_URL`/network; `broker: down` means the Key Value connection string. |
| Jobs stay `uploaded`/`queued` | The worker died. `scripts/start.py` restarts it and logs the exit code; check the deploy log for the crash. |
| `alembic` fails on boot | Logged and retried by the app lifespan hook; `/health` reports the real error. |
