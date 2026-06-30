# Architecture & Developer Onboarding

> A guided tour of the fleet dashcam portal codebase, written for a developer who is about to take ownership of it. Read top to bottom on day one. Bookmark the **Pattern Recipes** section and come back to it whenever you add a feature.

This is not a runbook — for "how do I start the server" see `README.md`. This is a **mental model**: how the pieces fit together, why we made the calls we made, and how to extend the code without breaking the things that keep it correct.

---

## 1. Five-minute mental model

The portal is a **multi-tenant evidence-retrieval app** for fleet operators. Compliance staff log in, find dashcam footage, and assemble cases (claim/audit packets). The product was scoped intentionally narrow:

- Search clips by truck / driver / time / event type.
- Play a clip with telemetry overlay and event markers.
- Triage flagged events; promote some into formal Cases.
- Attach evidence to a Case, leave notes, close it.

That's it. Things you might *expect* but won't find: a live ops map, driver scorecards, an admin console, share links, watermarking, retention TTL. Those are deferred to v2 — see `docs/plans/2026-06-29-dashcam-portal-design.md`.

### Architecture in one sketch

```
                ┌──────────────────────────┐
   Browser ───▶ │  React + Vite (port 5173)│
                │  - DashcamPage (Fleet Cam)│
                │  - ClipPage              │
                │  - EventTimelinePage     │
                │  - CaseListPage / Detail │
                └────────────┬─────────────┘
                             │ fetch (Authorization: Bearer …
                             │        OR X-Dev-User-Id+X-Dev-Tenant-Id)
                             ▼
                ┌──────────────────────────┐
                │   FastAPI (port 8000)    │
                │  - /me, /clips, /events  │
                │  - /cases, /audit, /ingest│
                │  - /trucks, /drivers     │
                └──────┬───────────┬───────┘
                       │           │
                       ▼           ▼
            ┌──────────────┐  ┌──────────────────────┐
            │  Postgres    │  │  MinIO (S3-compat)    │
            │  (port 5432) │  │  Bucket: dashcam-clips│
            │              │  │  Keys are TENANT-     │
            │  9 tables +  │  │  PREFIXED so the      │
            │  audit_log   │  │  storage layer can    │
            │  (append-    │  │  refuse cross-tenant  │
            │  only)       │  │  signing.             │
            └──────────────┘  └──────────────────────┘
```

Three boundaries you'll touch most:

1. **HTTP** — FastAPI routers in `backend/app/routers/`.
2. **DB** — SQLAlchemy 2.0 async models in `backend/app/models/`, plus migrations in `backend/migrations/versions/`.
3. **UI** — React pages in `frontend/src/pages/` driven by hooks in `frontend/src/hooks/`.

Everything else (auth, storage, audit, schemas) is *plumbing* in service of these three.

---

## 2. The stack and why

| Layer | Choice | Reason worth internalising |
|---|---|---|
| Backend framework | **FastAPI** | Async-native, automatic OpenAPI, dependency injection that we use heavily (`current_user`, `get_session`). |
| ORM | **SQLAlchemy 2.0** (async) | Mature, supports Postgres-native ENUMs / JSONB / advisory locks. The 2.0 typing (`Mapped[T]`, `mapped_column`) gives mypy real teeth. |
| Migrations | **Alembic** | Async env wired in `backend/migrations/env.py`. Auto-generate is enabled. |
| DB | **Postgres 16** | Native ENUMs, JSONB, advisory locks for `(tenant, year)` serialisation. |
| Object store | **Local filesystem** (default in dev) or **MinIO** (S3-compatible) | The `storage` module branches on `STORAGE_BACKEND`. Local mode serves bytes via `GET /clips/{id}/stream` with HTTP Range; S3 mode mints SigV4 presigned URLs. Switching is a single env var. |
| Auth | **JWT (HS256)** + dev-header shortcut | Portal does not mint tokens; an upstream IdP does. Dev headers bypass JWT only when `APP_ENV=dev`. |
| Frontend framework | **React 19 + Vite + TypeScript** | Vite for fast dev. TS strict, including `verbatimModuleSyntax`. |
| Styling | **Tailwind v3** + hand-rolled shadcn-style primitives | We chose to *not* depend on Radix to keep the dep tree small. The two primitives we use (`Button`, `Dropdown`) live in `frontend/src/components/ui/`. |
| Data fetching | **TanStack Query** (`@tanstack/react-query`) | `useQuery` for reads, `useMutation` for writes, `useInfiniteQuery` for cursor pagination. Cache invalidation + optimistic updates without writing reducers. |
| Forms / validation | **Zod** (sparingly) | Mostly we rely on TypeScript and FastAPI's Pydantic boundary — Zod is there for parsing values back from the URL. |
| Unit tests | **pytest** + **Vitest** | One test file per source file is the norm. |
| E2E tests | **Playwright** | Three smoke scenarios in `frontend/tests/e2e/smoke.spec.ts`. |

When you reach for a new dependency, ask: does it pay rent on its size? We didn't add date-fns, axios, lodash, or react-hook-form. The codebase stays small enough that someone new can read all of it.

---

## 3. Codebase tour

```
dashcam/
├── backend/
│   ├── app/
│   │   ├── main.py             ← FastAPI app + router wiring + CORS
│   │   ├── config.py           ← Pydantic-settings, env-driven
│   │   ├── db.py               ← Async engine, session factory, get_session dep
│   │   ├── auth.py             ← Principal model + current_user dep
│   │   ├── storage.py          ← local-fs / S3 adapter, tenant-prefixed keys
│   │   ├── audit.py            ← record() + record_system() + AuditEntry schema
│   │   ├── seed.py             ← CLI: python -m app.seed --reset
│   │   ├── models/             ← SQLAlchemy 2.0 models (one per aggregate)
│   │   ├── schemas/            ← Pydantic request/response models
│   │   └── routers/            ← FastAPI routers (one per resource)
│   ├── migrations/             ← Alembic
│   └── tests/                  ← pytest (~one file per router)
├── frontend/
│   ├── src/
│   │   ├── app/router.tsx      ← Routes
│   │   ├── lib/                ← api, auth, env, queryClient, types
│   │   ├── hooks/              ← One hook per query / mutation
│   │   ├── pages/              ← Routes' top-level components
│   │   ├── components/         ← Layout, ErrorBoundary, ui/, dashcam/, timeline/, cases/
│   │   └── test/setup.ts       ← Vitest + jest-dom
│   └── tests/e2e/              ← Playwright smoke specs
├── infra/docker-compose.dev.yml
├── samples/                    ← drop MP4s here for real playback
└── docs/                       ← this file + plans/
```

You don't have to memorise this. You **do** have to internalise the *grammar*:

- A new resource (say, "trips") adds **one model** + **one schema file** + **one router file** + **one test file**. Then it gets wired into `main.py`. That's the unit of change on the backend.
- A new page adds **one or two hooks** in `frontend/src/hooks/` + **one page component** in `frontend/src/pages/` + **maybe a subcomponent or two** under `frontend/src/components/<area>/`. Then it gets a route in `app/router.tsx`.

If you find yourself touching files outside this rhythm, pause and check: am I leaking concerns?

---

## 4. The pattern recipes — *learn these by heart*

These are the load-bearing patterns. Master them and the codebase opens up.

### 4.1 Tenant isolation (the single most important invariant)

**Rule:** every query that returns user data filters by `principal.tenant_id`. There is no exception. A user from tenant A who requests tenant B's resource by ID gets a `404 "not found"` — **not** a 403, because that would leak existence.

How it's enforced in practice:

```python
# backend/app/routers/cases.py (excerpt)
@router.get("/cases/{case_id}")
async def get_case(
    case_id: UUID,
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseDetail:
    stmt = (
        select(Case)
        .where(Case.id == case_id, Case.tenant_id == principal.tenant_id)
        .options(selectinload(Case.clips).selectinload(CaseClip.clip))
    )
    case = (await session.execute(stmt)).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="not found")
    ...
```

Note three things:

- `principal.tenant_id` is the *only* tenant the request can see. There is no `?tenant_id=` query parameter anywhere.
- The 404 detail is **exactly** `"not found"` — not "case not found" (would still leak), not "forbidden" (would leak that *something* exists).
- `selectinload` pre-loads the relationships you'll use. We set `lazy="raise"` on relationships in `models/` so any forgotten preload **throws** instead of silently issuing N+1 queries.

The storage layer enforces tenancy too — `storage._validate_tenant_prefix` refuses to mint a playback URL whose key doesn't start with `{tenant_id}/`. This is defence in depth: even if you slip up at the router layer, the storage call fails. The stream endpoint (`GET /clips/{id}/stream`, local-mode playback) layers a second containment check: after resolving `STORAGE_ROOT / storage_key` it asserts the resolved path is still inside `STORAGE_ROOT`, so a malformed key with `..` segments yields 404 rather than serving an arbitrary file off the host.

### 4.2 The audit log (append-only by convention)

Every mutating endpoint writes an audit row. The helper is `app.audit.record()`:

```python
# backend/app/audit.py (excerpt)
async def record(
    session: AsyncSession,
    *,
    principal: Principal,
    action: str,
    target_type: str,
    target_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Insert an audit row. Does NOT commit — caller owns the transaction."""
    session.add(AuditLog(
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        payload=payload or {},
    ))
    await session.flush()
```

Two rules that fall out of this:

1. **`record()` does not commit.** The caller is in the middle of a unit-of-work (e.g., "create case + write `case.created` audit"). The audit is part of that same transaction so it can't be partially written.
2. **The module contains no UPDATE / DELETE.** There's a regression test (`tests/test_audit.py::test_audit_module_has_no_update_or_delete_code`) that tokenises the source and grep-asserts the absence of mutation patterns. If you add a "fix this audit row" endpoint in a moment of weakness, that test will fail.

For system events that have no human actor (the ingest pipeline writing a `clip.ingested` row), there's a sibling `audit.record_system(...)` that omits `actor_user_id`.

#### Action names — keep them flat

The convention is `<noun>.<verb>` where the noun is the target_type:

```
clip.play_url_minted    clip.play         clip.scrub        clip.closed
clip.ingested
event.triage
case.created   case.clip_attached   case.updated   case.closed   case.note_added
```

This is searchable, sortable, and friendly to dashboards. Don't invent hierarchical names like `cases/lifecycle/closed`.

### 4.3 Cursor pagination (you will write this five times)

We use **opaque base64-JSON cursors** instead of integer offsets, because (a) offsets misbehave under concurrent inserts, and (b) we want stable ordering across pages.

The shape is always:

```
encode_cursor({"timestamp_iso": "...", "id": "<uuid>"})  → "eyJ0..."
```

And the SQL is:

```sql
WHERE (occurred_at, id) < (:cursor_ts, :cursor_id)
ORDER BY occurred_at DESC, id DESC
LIMIT :limit + 1            -- +1 so we know if there's a next page
```

You'll see this pattern in `audit.py`, `clips.py`, `events.py`, and `cases.py`. The duplication is **intentional** — generic cursor helpers would have to bend around UUID vs `int` IDs and different sort keys. Three copies is cheaper than the wrong abstraction.

If you write a new paginated endpoint, copy the encode/decode helpers from the closest sibling and adapt. The test pattern is:

```python
def test_list_paginates():
    # seed 25 rows, request limit=10, follow cursor, confirm no overlap and stable order.
```

### 4.4 The transaction boundary

Routers own the transaction. The dependency `get_session()` (in `app/db.py`) yields an `AsyncSession` wrapped in a context manager — when the handler returns successfully, the session commits; on exception, it rolls back.

That means inside a handler:

```python
await audit_record(session, principal=p, action="case.updated", ...)
case.status = body.status                       # mutate
await session.commit()                          # explicit commit IF you need
                                                # the row to be visible before
                                                # the response (e.g., the URL
                                                # mint in clips.py)
return CaseDetail.model_validate(case)
```

Most handlers don't need an explicit commit — the dependency will commit on success. The cases that **do** explicitly commit are the ones that need to observe their own write before returning (e.g., minting a presigned URL that the audit row must already exist for).

**The test fixture trick:** Tests use a savepoint pattern (`join_transaction_mode="create_savepoint"`) so that handlers calling `session.commit()` only release a savepoint inside the test's outer transaction, which gets rolled back at teardown. See `backend/tests/test_cases.py` for the canonical setup.

### 4.5 Frontend: hook per query, page consumes hooks

Every backend resource gets a hook in `frontend/src/hooks/`. The hook handles:

- The fetch (via `apiGet`/`apiPost`).
- The query key (so cache invalidation is predictable).
- The optimistic update strategy on mutations.

```typescript
// frontend/src/hooks/useCaseDetail.ts (sketch)
export const caseDetailQueryKey = (id: string) => ["case", id];

export function useCaseDetail(id: string) {
  return useQuery({
    queryKey: caseDetailQueryKey(id),
    queryFn: () => apiGet<CaseDetail>(`/cases/${id}`),
  });
}
```

The page (`CaseDetailPage.tsx`) then composes hooks:

```typescript
const { data: detail, isLoading, error } = useCaseDetail(id);
const patchCase = usePatchCase(id);
const closeCase = useCloseCase(id);
...
```

The shared `queryKey` factory matters because **mutations write fresh detail back into the cache** on success (see `frontend/src/hooks/useCaseMutations.ts`). That means after `usePatchCase` succeeds, every component reading `useCaseDetail(id)` re-renders with the new data — no refetch needed.

#### Per-user state: upsert on first write

For data like the Fleet Cam truck row order, the canonical place is a JSONB `preferences` column on `users` exposed via `PATCH /me/preferences`. There's a subtlety the dev-mode escape hatch exposes: the principal might not have a `users` row yet (the dev picker mints a `Principal` directly from headers, without seeding `users`). The PATCH handler therefore **upserts** the user row from the principal's claims on first write. This is the canonical pattern for "per-user state that has to work in dev mode too" — see `app/routers/me.py::patch_preferences`. A new feature that stores per-user state should reuse this column rather than spinning up a new table.

### 4.6 The dev-mode escape hatch

The frontend uses `X-Dev-User-Id` + `X-Dev-Tenant-Id` headers (not minted JWTs) to authenticate during development. The header source is `frontend/src/lib/auth.ts`'s `DEV_USERS` constants — UUIDs derived deterministically via `uuid5(NAMESPACE_DNS, "<slug>.dashcam")` so they match what `python -m app.seed` produces.

The backend's `current_user` dep accepts those headers **only when `APP_ENV=dev`**. In production (`APP_ENV=prod` or anything else), the dev path is dead code and a JWT is required.

**This is the only thing standing between you and a "set X-Dev-Tenant-Id to win" production vulnerability.** When you deploy, set `APP_ENV=prod` explicitly. Don't rely on the default.

**One exception to the headers-everywhere rule: the local-mode stream endpoint.** `GET /clips/{id}/stream` also accepts a short-lived signed token in `?t=<jwt>`. The browser's `<video>` element fetches the URL cross-origin (`:5173` → `:8000`) and does *not* attach the dev-headers or any `Authorization` header on media fetches, so the token rides in the URL itself. It's HS256-signed with `settings.jwt_secret` (same secret as session JWTs), carries `purpose="clip-stream"` plus a `clip_id` bound to the path, and expires in `DEFAULT_SIGNED_URL_TTL_S` (1h). The endpoint still accepts the dev-headers / `Authorization` when no `?t=` is present, so curl-based ops/debug keeps working. See `storage._mint_stream_token` and `routers/clips._verify_stream_token`.

---

## 5. Three end-to-end walkthroughs

Trace these in the actual code with the file open. They will teach you more than any description.

### 5.1 "Show me a clip"

1. User lands on `/dashcam` (the index redirects there). `frontend/src/pages/DashcamPage.tsx` mounts.
2. The page reads filters from `?truck_id=...&driver_id=...&from=...&to=...&q=...` via `useSearchParams`. The single popover `TruckDriverPicker` in `components/dashcam/` writes back to those params.
3. `TruckRowList` resolves the truck display order: `prefs.truck_order` first (from `usePrefs()` → `GET /me/preferences`), then any remaining trucks appended alphabetically.
4. Each `TruckRow` calls `useTruckDays(truck.id, filters)` → `GET /trucks/{id}/days`. Backend handler: `backend/app/routers/trucks.py::list_truck_days`, which groups clips by `date_trunc('day', started_at)` and returns one representative `first_clip_id` per day.
5. The user clicks a `<DayCard>` → `navigate(\`/clips/${first_clip_id}\`)`.
6. `ClipPage.tsx` mounts. `useClipDetail(id)` fires `GET /clips/{id}?play=true`.
7. Backend: `clips.py::get_clip` loads the clip (tenant-scoped 404), calls `audit_record(... action="clip.play_url_minted" ...)`, calls `storage.get_playback_url(tenant_id=..., user_id=..., key=storage_key, clip_id=...)` (which re-verifies the tenant prefix and dispatches on `STORAGE_BACKEND`), commits, returns `ClipDetail` with `playback_url`.
8. The page sets `<video src={playback_url}>` (the frontend prefixes a relative URL with `API_BASE`). In local mode the URL is `/clips/{id}/stream?t=<jwt>` — the token authenticates the cross-origin `<video>` fetch (see §4.6) — and the browser fetches the bytes from the backend. In s3 mode the URL is a presigned S3 GET URL and the browser fetches from MinIO directly. Either way the bytes flow back over HTTP Range requests.
9. On mount, the page also calls `useAuditEmitter`'s `emitPlay()`, which POSTs `/clips/{id}/audit` — a different audit row marking "user actually started playback" vs "URL minted".
10. On unmount: `emitClosed({ view_duration_s })` fires.

Total moving parts: 1 React page, 2 hooks, 2 (or 3 in local mode) backend endpoints, 3 audit rows in postgres, 1 file fetch (local stream or MinIO signed URL). Read every file in the chain once.

### 5.2 "Triage an event into a case"

1. User on `/trucks/{id}/events` (`EventTimelinePage.tsx` with `scope="truck"`).
2. They click the triage menu on a row, choose "Open case".
3. `OpenCaseModal.tsx` opens. They fill `incident_at` (default = event time) and `requester_name`.
4. Submit fires `useCreateCase().mutate(...)`. That mutation:
   - POSTs `/cases` → backend `cases.py::create_case` acquires `pg_advisory_xact_lock(hashtext(tenant|year))`, selects MAX(number), increments, inserts row, writes `case.created` audit, commits.
   - Then POSTs `/cases/{newId}/clips` with `clip_id=<event.clip_id>` → backend attaches (idempotent), writes `case.clip_attached` audit, commits.
   - Then fire-and-forget POSTs `/events/{eventId}/triage` with `label="open_case"` → backend writes `event.triage` audit, commits. (Fire-and-forget so a triage-audit failure doesn't undo the case.)
5. On success, `navigate(\`/cases/${newId}\`)`.
6. `CaseDetailPage` mounts, `useCaseDetail` fetches the new case (with the clip already attached), Activity tab shows `case.created` and `case.clip_attached` ordered by `occurred_at DESC`.

This is the most complex flow in the app. The order matters: case → attach → triage audit. If you flip them you can get a case with no clip, or a triage audit on a case that doesn't exist yet.

### 5.3 "Close a case"

Short on purpose. Read `CaseDetailPage.tsx`'s close button handler, then `frontend/src/components/cases/CloseCaseModal.tsx`, then `backend/app/routers/cases.py::close_case`. Notice:

- The frontend disables every action when `status == "closed"` (the action bar is read-only).
- The backend rejects double-close with `409 "case already closed"` — *not* 400, because the request body was valid; the **state** was wrong. (HTTP status code design is real, not arbitrary.)
- The audit payload includes the reason. That's the whole point of having a separate `/close` endpoint instead of letting `PATCH` set status — you can't close without a recorded reason.

---

## 6. Adding a feature — a recipe

You will eventually be asked to add something like "let users tag events with custom labels". Here is the playbook.

### Step 0 — Brainstorm first

Open `docs/plans/2026-06-29-dashcam-portal-design.md`. Does the feature fit the current product scope, or is it a v2 item? If it's a real new feature, write a short design doc in `docs/plans/YYYY-MM-DD-<topic>-design.md` before coding. Aim for ~200 lines.

### Step 1 — Backend, bottom-up

1. **Migration**: `cd backend && uv run alembic revision --autogenerate -m "add event_tags table"`. Edit the generated file to confirm it does what you expect — autogenerate is good but sometimes misses Postgres enums.
2. **Model** in `backend/app/models/event_tag.py`. Include `tenant_id` even if it seems redundant — tenant_id columns are how we enforce isolation cheaply.
3. **Schema** in `backend/app/schemas/event_tag.py`. Pydantic request body + response model.
4. **Router** in `backend/app/routers/event_tags.py`. Wire `Depends(current_user)` and `Depends(get_session)`. Filter every query by `principal.tenant_id`. Write `audit_record(...)` on every mutation. Use `selectinload` for any relationship traversals.
5. **Wire** the router in `backend/app/main.py`.
6. **Test** in `backend/tests/test_event_tags.py`. At minimum:
   - happy-path create
   - tenant isolation (user from tenant A can't see tenant B's tags)
   - cross-tenant access returns honest 404
   - audit row written

   Run `cd backend && uv run pytest -k event_tags`. Then `uv run ruff check . && uv run mypy app`.

### Step 2 — Frontend, top-down

1. **Type** in `frontend/src/lib/types.ts` (mirror the backend schema).
2. **Hook(s)** in `frontend/src/hooks/useEventTags.ts`. `useEventTags(eventId)` for read; `useCreateEventTag(eventId)` for mutation. Pick a shared `queryKey` factory.
3. **UI**. Probably an addition to `EventRow.tsx` and a tiny `TagPicker.tsx` component under `frontend/src/components/timeline/`.
4. **Test**. Render the component with a mocked hook; fire events; assert mutations called with the expected payload.
5. **Wire** into the page if needed.

### Step 3 — E2E if it matters

Add a scenario to `frontend/tests/e2e/smoke.spec.ts` if the feature crosses major workflow boundaries (e.g., a new triage label option that creates an audit chain). For pure UI tweaks, the unit tests are enough.

### Step 4 — Update docs

If you introduce a new pattern (rare), update this doc. If you only follow existing patterns, just update the API list in `README.md`.

### Worked example: the upload modal (T20)

The most recent feature that exercises this recipe end-to-end is the browser upload modal on Fleet Cam:

- **Backend**: `POST /clips/upload` (multipart) in `backend/app/routers/clips.py`. Validates `truck_id` / `driver_id` against the caller's tenant, enforces `settings.max_upload_bytes`, hashes the body with SHA-256, writes through `storage.put_object`, inserts the clip row, writes a `clip.uploaded` audit row, commits.
- **Frontend**: `<UploadModal />` in `frontend/src/components/dashcam/`, fed by `uploadClip()` in `frontend/src/hooks/useUploadClip.ts` (an XHR-based uploader rather than a `useMutation` so we can wire `xhr.upload.onprogress` to a per-file progress bar). On close the modal invalidates `["trucks"]` + `["truck-days"]` so any successful uploads show up as new day cards immediately.

That's the *one* place in the codebase right now that uses XHR instead of `fetch` — see the comment in `useUploadClip.ts` for the rationale.

### The single rule

If you find yourself writing more than ~200 lines of new code in a single file, stop and ask whether there's a smaller piece you can ship first. The codebase rewards small, additive PRs.

---

## 7. Testing philosophy

Three layers, three roles. Don't conflate them.

| Layer | Tool | What it proves | When you write it |
|---|---|---|---|
| Unit | pytest / vitest | "This function does what I think it does." | Always. Even one test per file beats none. |
| Integration | pytest with a real Postgres + MinIO | "Tenant isolation, FK constraints, advisory locks, and signed URLs actually work." | When you touch a router or storage. |
| E2E | Playwright | "Real browser through real backend through real DB completes the user flow." | When a flow crosses major boundaries. |

A few rules we've earned the hard way:

- **Real DB, not in-memory.** SQLite would let us run faster, but it doesn't have Postgres ENUMs, advisory locks, or array columns. We ran into mock-vs-prod divergence on a previous project and won't again.
- **Real backend in E2E.** No stub server. The whole point of E2E is to catch wiring bugs.
- **Mock the boundary, not the unit.** When testing a hook, mock `apiGet` (the boundary), not the React Query internals.
- **Don't test the framework.** No test should be asserting that `useQuery` re-fetches when its key changes — that's TanStack's problem.

### When something is flaky

In order:

1. Read the test. Is it relying on `setTimeout` for sync? Replace with Playwright's auto-waiting or `await waitFor(...)`.
2. Is there a race between `setSearchParams` and a controlled input? Anchor your assertion on the *destination* (the URL or the network call), not the intermediate (the input).
3. Genuinely flaky? Mark `.skip` with a comment linking to a tracking issue and a date. Don't sweep it under `--repeat-each` retries.

---

## 8. Security model (the bits you must not break)

In rough order of "how badly will this hurt if you break it":

1. **Tenant isolation.** See §4.1. Every router query, every storage call. The integration tests catch most regressions but cannot catch all.
2. **Append-only audit.** See §4.2. The tokenising guardrail test will yell, but don't try to silence it.
3. **Honest 404s.** See §4.1 and §5.3. `"not found"` is the only string used for "you can't see this." Any other wording is a leak.
4. **Dev-mode gating.** See §4.6. `APP_ENV=prod` in production. No exceptions.
5. **No client-asserted tenant on `/ingest/clips`** *yet* — this is a known issue. The ingest stub trusts its caller. When you add a real ingest pipeline, the first thing to ship is HMAC-signed service-to-service auth so callers can't lie about tenant_id. Until then, that endpoint should be reachable only from an internal network.
6. **Signed URL TTL cap.** See `storage.MAX_SIGNED_URL_TTL_S = 6 * 3600`. If you raise it, you raise the blast radius of a leaked URL. Get sign-off. This cap applies to both modes: S3 presigned URLs *and* the local-mode `?t=<jwt>` stream tokens (`storage._mint_stream_token`). The token is signed with `settings.jwt_secret`, carries `purpose="clip-stream"` so it can never accidentally satisfy `current_user` (the session JWT path explicitly rejects any token with a non-empty `purpose`), and is bound to a specific `clip_id`. The token ends up in browser history and any access logs — that's the documented tradeoff for cross-origin `<video>` playback; the short TTL keeps the blast radius small.
7. **CORS.** Currently `["http://localhost:5173"]` — explicit, not `*`. When you deploy, set it to your production origin. Never widen for convenience.
8. **No secrets in the repo.** `.env.example` ships placeholder values. The real values live in your secrets manager.

When in doubt about a security tradeoff, **default closed**. It's much easier to widen a rule later than to find out you should have closed it earlier.

---

## 9. Performance and scale notes

The MVP runs cheerfully on a single Postgres + a single MinIO. Some things to be aware of:

- **Indexes are tenant-prefixed.** See `models/clip.py`, `event.py`, `case.py`. Always lead with `tenant_id` because every query filters on it.
- **N+1 protection.** `lazy="raise"` on every relationship in `models/`. If your handler hits a relationship without `selectinload`, the query fails loudly in tests rather than silently in prod.
- **Audit table is hot.** Every read/write to a clip writes audit rows. We're at 200 clips / 80 events in the seed and the table is small; at production scale (millions of clips, billions of audit rows) you'll want to partition by `tenant_id` and archive old rows. Don't optimise until you measure.
- **Bundle size.** Frontend is ~420 KB raw, ~125 KB gzipped at the time of writing. Route-splitting (lazy-load `ClipPage` and `CaseDetailPage`) will get you under 200 KB raw if it matters. Don't prematurely optimise.

---

## 10. Things that are deliberately incomplete

If you see one of these, **don't "fix" it without asking** — they were scoped out intentionally to ship the MVP.

| Area | Status | Why it's incomplete |
|---|---|---|
| External share links / watermarks / sealed-ZIP export | Not built | Out of MVP scope. See design doc. Sketches: pre-signed URL + viewer-stamped overlay; PDF manifest with SHA-256s. |
| Legal hold + retention TTL job | Not built | Same. Sketch: nightly job soft-deletes clips older than 30d unless they match an active hold. |
| Admin RBAC UI | Not built | `roles` is on the Principal but not consulted by any endpoint. When you add it, do `Depends(require_role("admin"))` and *do not* try to put role logic into the routers directly. |
| Users picker for case assignee | Open input field | No `GET /users` endpoint exists. Add one (mirror `/trucks` and `/drivers`), then swap the input for a combobox. |
| Detach clip from case | Not built | No `DELETE /cases/{id}/clips/{clip_id}`. Add it; remember to write `case.clip_detached` audit. |
| Driver name on `EventRow` | Always null | Events don't have a direct driver FK; they have a `clip_id` which has a `driver_id`. The schema flattener in `frontend/src/lib/types.ts` and the backend's `EventRow` could be extended to chase the clip → driver path. Decide whether it's worth the JOIN per row. |
| `clip.text` filter treats `%`/`_` as ILIKE wildcards | Known | Either escape user input or document it. UX call. |
| Dev mode display name is always "Dev User" | Known | `_dev_principal` in `auth.py` returns synthetic name regardless of which seeded user is picked. Fix: look up the user row in `users` table when minting the principal. Trivial, just never got prioritised. |
| Real ingest pipeline | Not built | The `POST /ingest/clips` endpoint exists as a forward-compat stub. It currently has *no auth*. Top of the v2 list. |
| Server-side date extraction from MP4 metadata via ffprobe | Not built | The upload modal currently parses recording timestamps from the filename (with `lastModified` fallback) — see `frontend/src/lib/uploadDate.ts`. A backend ffprobe pass over the uploaded bytes would give us a more authoritative `started_at`, but it adds `ffmpeg` to the runtime deps and the filename heuristic is good enough for MVP. |

If you tackle one of these, please:

1. Write a short design note in `docs/plans/YYYY-MM-DD-<topic>-design.md` first.
2. Cross-link it from this section.
3. Update the README's "Known limitations" section when it ships.

---

## 11. Workflow & habits

The codebase rewards a small set of habits:

- **Read before writing.** Open the closest sibling file when you're adding something. Mimic its shape.
- **Tests run locally, fast.** `uv run pytest` finishes in seconds. `npm test` likewise. If a test takes minutes, you've done something wrong (likely forgot to mock a network call).
- **Lint and type before commit.** `uv run ruff check . && uv run mypy app`. `npm run build` (which runs `tsc -b`).
- **Verify the E2E suite before merging anything in a frontend page.** It takes ~5 seconds and catches more than you'd expect.
- **Commit one logical change at a time.** The git log in this repo is part of the documentation — each task commit (`T1`, `T2`, …) is its own atomic story. Don't bundle a refactor with a feature.
- **Update the README's "Known limitations" section** when you ship something on the list.
- **When in doubt, default to the simpler thing.** This codebase is intentionally boring. Boring is what survives team turnover.

### A note on subagent-driven development

This codebase was built using a workflow where each task was implemented by a fresh subagent under the orchestration of a controller agent, with spec-compliance + code-quality reviews after each task. The history is in `docs/plans/` and individual git commits.

That's *how it was built*, not *how it must evolve*. You can keep using the same workflow (it produced consistent code with good test coverage), or you can take ownership the traditional way. Either is fine. What matters is the patterns, not the construction method.

---

## 12. Reading list

If you want to deepen your understanding of *why* the codebase looks the way it does:

- **SQLAlchemy 2.0 async tutorial** — https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- **TanStack Query — query keys + cache** — https://tanstack.com/query/latest/docs/framework/react/guides/query-keys
- **FastAPI dependency injection** — https://fastapi.tiangolo.com/tutorial/dependencies/
- **Postgres advisory locks** — https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS
- **OWASP Multi-Tenancy cheat sheet** — search "OWASP multi-tenancy"; the high-level reasoning behind §4.1.

And in this repo:

- `docs/plans/2026-06-29-dashcam-portal-design.md` — the original product design.
- `README.md` — the runbook.
- `frontend/tests/e2e/smoke.spec.ts` — the three scenarios. Read them as living documentation of "what the product does."

---

## 13. Welcome aboard

Three things to do on day one:

1. Run the full stack locally and play with it (`README.md` Getting Started).
2. Trace one of the three walkthroughs in §5 with the files open.
3. Pick one small item from §10 (the "deliberately incomplete" list) and write a one-page design note for how you'd tackle it. Don't code it yet — circulate the note.

If you can do those three and the patterns in §4 make sense, you own this codebase.
