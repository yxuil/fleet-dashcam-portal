# Dashcam Portal

## What this is

The fleet dashcam portal MVP: a customer-facing web app for compliance and
evidence-retrieval staff at a truck-fleet operator. This MVP is the lean slice
of the larger design — Search, Player, Event Timeline, and a minimal Cases
workflow. Authentication is owned upstream; the portal trusts an
`Authorization: Bearer <jwt>` header (with a dev-only escape hatch, below).

Backend is FastAPI + SQLAlchemy + Alembic over async Postgres. Frontend is
React 19 + Vite + Tailwind + React Query. Object storage is MinIO in dev
(S3 in prod). The feature design lives at
[`docs/plans/2026-06-29-dashcam-portal-design.md`](docs/plans/2026-06-29-dashcam-portal-design.md).

## Prerequisites

- macOS or Linux
- Docker Desktop or Docker Engine (with `docker compose`)
- Python 3.11+ (the actual interpreter is managed by `uv`, but `>=3.11` must be
  available for `uv` to fetch)
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- Node 20+ (Node 22 also works)
- `npm` (ships with Node)
- ~5 GB free disk for Docker images and `node_modules`

## Getting started

From a fresh clone, run each step in its own shell unless noted. The
backend and frontend dev servers both run in the foreground.

```bash
# 1. Copy env defaults (both backend and frontend read from these).
cp .env.example .env
cp frontend/.env.example frontend/.env.local

# 2. Bring up Postgres + MinIO (the `minio-init` job creates the bucket).
docker compose -f infra/docker-compose.dev.yml up -d

# 3. Install backend deps, apply migrations, seed the database.
cd backend
uv sync
uv run alembic upgrade head
uv run python -m app.seed --reset --no-upload-samples

# 4. Start the backend (FastAPI on :8000). Leave this running.
uv run uvicorn app.main:app --reload

# 5. In a new shell: install frontend deps and start Vite on :5173.
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The bottom-right corner shows a dev-user picker;
pick any seeded user and the app loads with that tenant's data.

## Repo layout

```
backend/      FastAPI app, SQLAlchemy models, Alembic migrations, S3 client
frontend/     React 19 + Vite + Tailwind + React Query UI
infra/        docker-compose for Postgres + MinIO + bucket-init job
samples/      drop MP4s here to attach playable bytes during seed (optional)
docs/         feature design and notes
```

## Dev auth

The backend only ever sees a `Principal` (user + tenant + roles). How that
principal materialises depends on `APP_ENV`:

- **`APP_ENV != "dev"` (prod):** the dependency expects
  `Authorization: Bearer <jwt>`. Tokens are HS256, signed with `JWT_SECRET`,
  and must carry `sub`, `tenant_id`, `roles`, `email`, `name`.
- **`APP_ENV=dev`:** the same dependency *also* accepts
  `X-Dev-User-Id` and `X-Dev-Tenant-Id` headers. If both are present, the
  backend mints a synthetic `Principal` (no JWT verification).

The frontend, when `VITE_APP_ENV=dev`, renders a dropdown picker in the
bottom-right corner that writes the selected user's UUIDs into
`localStorage` and attaches them as `X-Dev-*` headers on every API call.

Seeded users (see `frontend/src/lib/auth.ts` for the UUIDs):

| Tenant            | Email              | Label             |
| ----------------- | ------------------ | ----------------- |
| Acme Logistics    | admin@acme.dev     | Acme — Admin      |
| Acme Logistics    | viewer@acme.dev    | Acme — Viewer     |
| Northwind Freight | admin@northwind.dev | Northwind — Admin |
| Northwind Freight | viewer@northwind.dev | Northwind — Viewer |

**Known limitation:** the synthetic principal's `name` is always
`"Dev User"`, regardless of which picker entry is active. The picker label
exists for the developer's benefit only. Tenant scoping is real, however —
switching tenants in the picker really does change which rows the API
returns.

## Running tests

```bash
# Backend unit + integration tests (in-process; Postgres must be up).
cd backend && uv run pytest

# Backend lint + type-check.
uv run ruff check . && uv run mypy app

# Frontend unit tests (Vitest + Testing Library).
cd frontend && npm test

# Frontend build check (catches type and bundling errors).
npm run build
```

### End-to-end (Playwright)

```bash
cd frontend
npx playwright install chromium   # one-time browser install
npm run test:e2e
```

Prerequisites for the E2E suite:

- Docker stack up (`docker compose -f infra/docker-compose.dev.yml up -d`)
- Backend running on `:8000` (`uv run uvicorn app.main:app --reload`)
- Frontend dev server on `:5173` — Playwright will start it automatically via
  the config's `webServer` block if it's not already up
- `uv` on `PATH` — the test's `globalSetup` shells out to
  `uv run python -m app.seed --reset --no-upload-samples` to reset the DB
  before the suite runs

To re-run tests without re-seeding, set `E2E_SKIP_SEED=1`:

```bash
E2E_SKIP_SEED=1 npm run test:e2e
```

## Seed dev data

```bash
cd backend
uv run python -m app.seed --reset --no-upload-samples
```

What you get:

- 2 tenants (Acme Logistics, Northwind Freight)
- 4 users (admin + viewer per tenant)
- 6 trucks, 8 drivers
- ~200 clips spread over the last 30 days
- ~80 events with a mix of types and severities
- No cases — the frontend's "open case" flow exercises that path

Flags:

- `--reset` truncates every app table in FK-safe order before seeding. Without
  it, a second run will collide on uniqueness constraints.
- `--upload-samples` (default) tries to upload every `samples/*.mp4` to MinIO
  round-robin. Failures (no samples present, MinIO down) are non-fatal — the
  DB rows still get a canonical `storage_key`.
- `--no-upload-samples` skips MinIO entirely. Clips still play in the UI
  insofar as MinIO returns a signed URL, but the response body is a 404 until
  you drop real MP4s into `samples/` and reseed with `--upload-samples`.

Sample MP4s are **not** committed. Drop any short H.264/AAC MP4 into
`samples/` (any filename, just `*.mp4`) to get playable video — see
[`samples/README.md`](samples/README.md).

## API overview

All routes require a `Principal` (real JWT or dev headers). All reads are
auto-scoped to the caller's tenant.

```
# Auth
GET    /me

# Clips
GET    /clips
GET    /clips/{clip_id}
POST   /clips/{clip_id}/audit

# Events
GET    /events
GET    /trucks/{truck_id}/events
POST   /events/{event_id}/triage

# Cases
POST   /cases
GET    /cases
GET    /cases/{case_id}
PATCH  /cases/{case_id}
POST   /cases/{case_id}/clips
POST   /cases/{case_id}/close
POST   /cases/{case_id}/audit

# Audit
GET    /audit

# Trucks / drivers (read-only, for filter chips)
GET    /trucks
GET    /trucks/{truck_id}
GET    /drivers

# Ingest stub
POST   /ingest/clips

# Health
GET    /healthz
```

## Environment variables

### Backend (`/.env`)

| Var             | Default                                                   | Notes                                                    |
| --------------- | --------------------------------------------------------- | -------------------------------------------------------- |
| `APP_ENV`       | `dev`                                                     | Set to anything else to disable the `X-Dev-*` shortcut. |
| `DATABASE_URL`  | `postgresql+asyncpg://dashcam:dashcam@localhost:5432/dashcam` | asyncpg driver required.                                |
| `JWT_SECRET`    | `dev-secret-change-me`                                    | HS256 shared secret with the upstream IdP.              |
| `JWT_ALGORITHM` | `HS256`                                                   | Only HS256 supported today.                             |
| `S3_ENDPOINT`   | `http://localhost:9000`                                   | MinIO endpoint in dev.                                  |
| `S3_ACCESS_KEY` | `minioadmin`                                              | MinIO root user in dev.                                 |
| `S3_SECRET_KEY` | `minioadmin`                                              | MinIO root password in dev.                             |
| `S3_BUCKET`     | `dashcam-clips`                                           | Created by the `minio-init` compose job.                |
| `S3_REGION`     | `us-east-1`                                               | Required by boto3 even against MinIO.                   |

### Frontend (`frontend/.env.local`)

| Var             | Default                  | Notes                                                |
| --------------- | ------------------------ | ---------------------------------------------------- |
| `VITE_API_BASE` | `http://localhost:8000`  | Backend base URL; CORS allows `:5173` against `:8000`. |
| `VITE_APP_ENV`  | `dev`                    | Set to `prod` to hide the dev-user picker.           |

## Services and ports

| Service     | URL                                  | Credentials             |
| ----------- | ------------------------------------ | ----------------------- |
| Backend     | http://localhost:8000                | dev headers or JWT      |
| Frontend    | http://localhost:5173                | (uses picker in dev)    |
| Postgres    | localhost:5432, db `dashcam`         | `dashcam` / `dashcam`   |
| MinIO API   | http://localhost:9000                | `minioadmin` / `minioadmin` |
| MinIO UI    | http://localhost:9001                | `minioadmin` / `minioadmin` |

## Database migrations

```bash
cd backend
uv run alembic upgrade head                          # apply all migrations
uv run alembic downgrade base                        # roll everything back
uv run alembic revision --autogenerate -m "<msg>"    # new migration
```

## Known limitations / v2

- **Ingest auth.** `POST /ingest/clips` is a stub with no HMAC verification —
  fine for the seed/test path, never for real cameras.
- **Watermarking and chain-of-custody export.** The design calls for
  per-viewer watermarks on playback and a sealed-ZIP export with SHA-256
  manifest + audit-log PDF; neither is implemented.
- **External share links.** Time-limited, password-protected, watermarked
  share URLs are deferred.
- **Legal Holds + retention TTL job.** Schema scaffolding only; no background
  job enforces TTLs, and there's no UI to create or release holds.
- **Admin / RBAC UI.** Roles are carried on the `Principal` but the portal
  does not expose role-management screens.
- **Notifications and email.** No transport wired up.
- **Multi-camera switching, geofence search, saved searches.** Out of MVP
  scope.
- **Remove-attached-clip flow.** A case can attach clips but the UI has no
  "detach" affordance.
- **Case assignee picker.** Assignee is editable as a free-text user_id; no
  users-picker yet.
- **Driver name on `EventRow`.** The event list shows truck but not driver
  display name.
- **Clip `text` filter wildcards.** Backend uses `ILIKE %q%` without escaping
  `%` or `_` — a query of just `%` matches everything. Document-only risk for
  now; tighten before exposing the API externally.
- **Dev principal display name.** Always `"Dev User"` regardless of picker
  selection — see *Dev auth* above.

## Troubleshooting

- **Postgres healthy but backend can't connect.** Confirm `.env` `DATABASE_URL`
  matches the credentials and port in `infra/docker-compose.dev.yml`
  (`dashcam:dashcam` on `localhost:5432`).
- **MinIO bucket missing.** Re-run the init job:
  `docker compose -f infra/docker-compose.dev.yml run --rm minio-init`.
- **E2E test fails in `globalSetup` with `failed to spawn 'uv run ...'`.**
  Ensure `uv` is on `PATH` in the same shell that runs `npm run test:e2e`. Or
  seed manually and bypass: `E2E_SKIP_SEED=1 npm run test:e2e`.
- **Playwright complains the browser isn't installed.** Run
  `npx playwright install chromium` inside `frontend/`.
- **Frontend can't reach the API.** Check `frontend/.env.local` has
  `VITE_API_BASE=http://localhost:8000` and that the backend is actually
  listening (`curl http://localhost:8000/healthz`).

## License / contributing

Private project. See `LICENSE` (placeholder; no license file is committed yet).
Contributions welcome from team members — open a PR against the default branch.
