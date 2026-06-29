# Dashcam Portal

Fleet dashcam portal MVP — FastAPI backend, React (Vite + TS) frontend, Postgres + MinIO infra.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python 3.11+ managed via `uv`)
- Node.js 20+ and `npm`
- Docker + Docker Compose

## Getting started

From the repo root:

```bash
# 1. Copy env defaults
cp .env.example .env

# 2. Start Postgres + MinIO
docker compose -f infra/docker-compose.dev.yml up -d

# 3. Backend (FastAPI on :8000)
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000
# In another shell, verify:  curl http://localhost:8000/healthz  ->  {"status":"ok"}

# 4. Frontend (Vite on :5173)
cd ../frontend
npm install
npm run dev
# Open http://localhost:5173
```

## Services

| Service     | URL                                        | Credentials             |
| ----------- | ------------------------------------------ | ----------------------- |
| Backend     | http://localhost:8000                      | n/a                     |
| Frontend    | http://localhost:5173                      | n/a                     |
| Postgres    | localhost:5432 (db `dashcam`)              | `dashcam` / `dashcam`   |
| MinIO API   | http://localhost:9000                      | `minioadmin` / `minioadmin` |
| MinIO UI    | http://localhost:9001                      | `minioadmin` / `minioadmin` |

The `minio-init` service runs automatically as part of `docker compose up -d` and
creates the `dashcam-clips` bucket. If you need to (re)run it manually (works on
macOS / Windows / Linux because it uses the compose network):

```bash
docker compose -f infra/docker-compose.dev.yml run --rm minio-init
```

## Tests

```bash
# Backend smoke tests
cd backend && uv run pytest

# Frontend unit tests
cd frontend && npm test

# Frontend e2e (filled in T15)
cd frontend && npm run e2e
```

## Layout

```
backend/      FastAPI app, SQLAlchemy + Alembic, S3 client
frontend/    React + Vite + Tailwind + shadcn/ui scaffolding
infra/       Docker Compose for Postgres + MinIO
samples/     Sample clips + telemetry (added in T9)
docs/        Design notes and plans
```
