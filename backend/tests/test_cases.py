"""Tests for the cases API.

Patterns mirror ``test_clips.py`` / ``test_events.py``:

* Probe the dev Postgres at import time; skip cleanly when unreachable.
* Each test gets a fresh session whose outer transaction is rolled back
  at teardown. Because the cases router calls ``session.commit()`` on
  every mutation, we open the session with
  ``join_transaction_mode="create_savepoint"`` so those commits release
  SAVEPOINTs instead of breaking the per-test rollback contract.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.auth import Principal
from app.config import settings
from app.db import get_session
from app.main import app
from app.models.audit import AuditLog
from app.models.case import Case, CaseStatus
from app.models.case_clip import CaseClip
from app.models.clip import Clip
from app.models.tenant import Tenant
from app.models.truck import Truck
from app.models.user import User
from app.schemas.case import CaseDetail, CaseListResponse, CaseRow

# ---------------------------------------------------------------------------
# DB reachability gate
# ---------------------------------------------------------------------------


def _can_connect(url: str) -> bool:
    async def probe() -> bool:
        engine = create_async_engine(url, pool_pre_ping=True)
        try:
            async with engine.connect() as conn:
                await conn.execute(select(1))
            return True
        except Exception:
            return False
        finally:
            await engine.dispose()

    try:
        return asyncio.run(probe())
    except Exception:
        return False


_DB_AVAILABLE = _can_connect(settings.database_url)

_SKIP_REASON = (
    "Dev Postgres not reachable at DATABASE_URL. "
    "Run `docker compose -f infra/docker-compose.dev.yml up -d postgres` "
    "and `uv run alembic upgrade head` first."
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def http_client_with_session() -> AsyncIterator[tuple[httpx.AsyncClient, AsyncSession]]:
    """HTTPX client + shared, rollback-safe session.

    See ``test_clips.py`` / ``test_events.py`` for the savepoint trick.
    """
    if not _DB_AVAILABLE:
        pytest.skip(_SKIP_REASON)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    async with engine.connect() as connection:
        outer_txn = await connection.begin()
        session_factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            class_=AsyncSession,
            join_transaction_mode="create_savepoint",
        )
        async with session_factory() as s:

            async def _override_get_session() -> AsyncIterator[AsyncSession]:
                yield s

            app.dependency_overrides[get_session] = _override_get_session
            transport = httpx.ASGITransport(app=app)
            try:
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    yield client, s
            finally:
                app.dependency_overrides.pop(get_session, None)
                await outer_txn.rollback()
    await engine.dispose()


def _principal(
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> Principal:
    return Principal(
        user_id=user_id or uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        roles=["viewer"],
        email="t@example.com",
        name="Test User",
    )


def _dev_headers(principal: Principal) -> dict[str, str]:
    return {
        "X-Dev-User-Id": str(principal.user_id),
        "X-Dev-Tenant-Id": str(principal.tenant_id),
    }


@pytest.fixture
def dev_settings() -> Iterator[None]:
    """Force dev mode so X-Dev-* headers authenticate."""
    from app.auth import get_settings

    dev_cfg = settings.model_copy(update={"app_env": "dev"})
    app.dependency_overrides[get_settings] = lambda: dev_cfg
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_settings, None)


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


async def _seed_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    tenant = Tenant(id=tenant_id, name=f"Tenant {tenant_id}")
    session.add(tenant)
    await session.flush()
    return tenant


async def _seed_user(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    email: str | None = None,
) -> User:
    uid = user_id or uuid.uuid4()
    user = User(
        id=uid,
        tenant_id=tenant_id,
        email=email or f"{uid}@dev.local",
        name="Seeded User",
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_truck(session: AsyncSession, *, tenant_id: uuid.UUID, label: str) -> Truck:
    truck = Truck(id=uuid.uuid4(), tenant_id=tenant_id, label=label)
    session.add(truck)
    await session.flush()
    return truck


async def _seed_clip(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    truck_id: uuid.UUID,
    started_at: datetime,
    duration_s: int = 30,
) -> Clip:
    clip_id = uuid.uuid4()
    clip = Clip(
        id=clip_id,
        tenant_id=tenant_id,
        truck_id=truck_id,
        driver_id=None,
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=duration_s),
        duration_s=duration_s,
        storage_key=f"{tenant_id}/2026/06/29/{clip_id}.mp4",
    )
    session.add(clip)
    await session.flush()
    return clip


async def _seed_tenant_with_creator(
    session: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Common setup: tenant + a user owning the principal's user_id.

    Returns ``(tenant_id, user_id)`` so the caller can build a principal
    that satisfies the ``cases.created_by`` FK.
    """
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant(session, tenant_id)
    await _seed_user(session, tenant_id=tenant_id, user_id=user_id)
    return tenant_id, user_id


# ---------------------------------------------------------------------------
# Tests — POST /cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_case_assigns_number_per_tenant_year(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Fresh tenant: first case must get ``C-<current-year>-0001``."""
    client, s = http_client_with_session
    tenant_id, user_id = await _seed_tenant_with_creator(s)

    principal = _principal(tenant_id=tenant_id, user_id=user_id)
    resp = await client.post("/cases", json={}, headers=_dev_headers(principal))
    assert resp.status_code == 201, resp.text
    detail = CaseDetail.model_validate(resp.json())

    year = datetime.now(UTC).year
    assert detail.number == f"C-{year}-0001"
    assert detail.status == CaseStatus.open
    assert detail.created_by == user_id
    assert detail.tenant_id == tenant_id


@pytest.mark.asyncio
async def test_create_case_number_increments_within_tenant(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Three sequential creates inside one tenant: 0001, 0002, 0003."""
    client, s = http_client_with_session
    tenant_id, user_id = await _seed_tenant_with_creator(s)
    principal = _principal(tenant_id=tenant_id, user_id=user_id)

    year = datetime.now(UTC).year
    numbers: list[str] = []
    for _ in range(3):
        resp = await client.post("/cases", json={}, headers=_dev_headers(principal))
        assert resp.status_code == 201, resp.text
        numbers.append(CaseDetail.model_validate(resp.json()).number)

    assert numbers == [f"C-{year}-0001", f"C-{year}-0002", f"C-{year}-0003"]


@pytest.mark.asyncio
async def test_create_case_number_isolated_between_tenants(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Two tenants creating cases must each start at ``0001`` for the year."""
    client, s = http_client_with_session
    tenant_a, user_a = await _seed_tenant_with_creator(s)
    tenant_b, user_b = await _seed_tenant_with_creator(s)

    principal_a = _principal(tenant_id=tenant_a, user_id=user_a)
    principal_b = _principal(tenant_id=tenant_b, user_id=user_b)

    year = datetime.now(UTC).year

    resp = await client.post("/cases", json={}, headers=_dev_headers(principal_a))
    assert resp.status_code == 201
    assert CaseDetail.model_validate(resp.json()).number == f"C-{year}-0001"

    resp = await client.post("/cases", json={}, headers=_dev_headers(principal_a))
    assert resp.status_code == 201
    assert CaseDetail.model_validate(resp.json()).number == f"C-{year}-0002"

    # Tenant B starts at 0001 independently.
    resp = await client.post("/cases", json={}, headers=_dev_headers(principal_b))
    assert resp.status_code == 201
    assert CaseDetail.model_validate(resp.json()).number == f"C-{year}-0001"


@pytest.mark.asyncio
async def test_create_case_audit_row_written(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """``POST /cases`` must append a ``case.created`` audit row with the number."""
    client, s = http_client_with_session
    tenant_id, user_id = await _seed_tenant_with_creator(s)
    principal = _principal(tenant_id=tenant_id, user_id=user_id)

    resp = await client.post("/cases", json={}, headers=_dev_headers(principal))
    assert resp.status_code == 201
    case = CaseDetail.model_validate(resp.json())

    rows = (
        (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.target_type == "case",
                    AuditLog.target_id == case.id,
                    AuditLog.action == "case.created",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    audit = rows[0]
    assert audit.tenant_id == tenant_id
    assert audit.actor_user_id == user_id
    assert audit.payload == {"number": case.number}


# ---------------------------------------------------------------------------
# Tests — GET /cases
# ---------------------------------------------------------------------------


async def _create_via_api(
    client: httpx.AsyncClient, principal: Principal, **fields: object
) -> CaseDetail:
    resp = await client.post("/cases", json=fields, headers=_dev_headers(principal))
    assert resp.status_code == 201, resp.text
    return CaseDetail.model_validate(resp.json())


@pytest.mark.asyncio
async def test_list_cases_tenant_isolated(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    client, s = http_client_with_session
    tenant_a, user_a = await _seed_tenant_with_creator(s)
    tenant_b, user_b = await _seed_tenant_with_creator(s)
    principal_a = _principal(tenant_id=tenant_a, user_id=user_a)
    principal_b = _principal(tenant_id=tenant_b, user_id=user_b)

    case_a = await _create_via_api(client, principal_a)
    case_b = await _create_via_api(client, principal_b)

    resp = await client.get("/cases", headers=_dev_headers(principal_a))
    assert resp.status_code == 200
    body = CaseListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    assert case_a.id in ids
    assert case_b.id not in ids
    assert all(row.tenant_id == tenant_a for row in body.items)


@pytest.mark.asyncio
async def test_list_cases_filter_by_status(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """``?status=under_review&status=approved`` returns the union."""
    client, s = http_client_with_session
    tenant_id, user_id = await _seed_tenant_with_creator(s)
    principal = _principal(tenant_id=tenant_id, user_id=user_id)

    c_open = await _create_via_api(client, principal)
    c_review = await _create_via_api(client, principal)
    c_approved = await _create_via_api(client, principal)

    # Move c_review and c_approved off "open" via PATCH.
    resp = await client.patch(
        f"/cases/{c_review.id}",
        json={"status": "under_review"},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    resp = await client.patch(
        f"/cases/{c_approved.id}",
        json={"status": "approved"},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text

    resp = await client.get(
        "/cases",
        params=[("status", "under_review"), ("status", "approved")],
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    body = CaseListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    assert ids == {c_review.id, c_approved.id}
    assert c_open.id not in ids


@pytest.mark.asyncio
async def test_list_cases_filter_by_assignee(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    client, s = http_client_with_session
    tenant_id, user_id = await _seed_tenant_with_creator(s)
    # Seed two more users to act as assignees so the FK is satisfied.
    assignee = await _seed_user(s, tenant_id=tenant_id)
    other = await _seed_user(s, tenant_id=tenant_id)
    principal = _principal(tenant_id=tenant_id, user_id=user_id)

    matched = await _create_via_api(client, principal, assignee_user_id=str(assignee.id))
    not_matched = await _create_via_api(client, principal, assignee_user_id=str(other.id))
    unassigned = await _create_via_api(client, principal)

    resp = await client.get(
        "/cases",
        params={"assignee_user_id": str(assignee.id)},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200
    body = CaseListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    assert matched.id in ids
    assert not_matched.id not in ids
    assert unassigned.id not in ids


@pytest.mark.asyncio
async def test_list_cases_q_matches_number_or_requester(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """``?q=`` is a case-insensitive substring across the four text fields."""
    client, s = http_client_with_session
    tenant_id, user_id = await _seed_tenant_with_creator(s)
    principal = _principal(tenant_id=tenant_id, user_id=user_id)

    by_requester = await _create_via_api(client, principal, requester_name="Quincy Tang")
    by_org = await _create_via_api(client, principal, requester_org="QuincyCo")
    by_ref = await _create_via_api(client, principal, external_ref="REF-QCY-99")
    untouched = await _create_via_api(client, principal, requester_name="Alice")

    resp = await client.get("/cases", params={"q": "quincy"}, headers=_dev_headers(principal))
    assert resp.status_code == 200, resp.text
    body = CaseListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    assert by_requester.id in ids
    assert by_org.id in ids
    assert untouched.id not in ids

    # Try matching by external_ref (different substring).
    resp = await client.get("/cases", params={"q": "qcy"}, headers=_dev_headers(principal))
    assert resp.status_code == 200
    body = CaseListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    assert by_ref.id in ids
    assert untouched.id not in ids

    # And by number — every case starts with "C-".
    resp = await client.get("/cases", params={"q": "C-"}, headers=_dev_headers(principal))
    assert resp.status_code == 200
    body = CaseListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    # All four should match because they all have "C-" in their number.
    assert {by_requester.id, by_org.id, by_ref.id, untouched.id}.issubset(ids)


@pytest.mark.asyncio
async def test_list_cases_paginates(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    client, s = http_client_with_session
    tenant_id, user_id = await _seed_tenant_with_creator(s)
    principal = _principal(tenant_id=tenant_id, user_id=user_id)

    seeded: list[CaseDetail] = []
    for _ in range(5):
        seeded.append(await _create_via_api(client, principal))

    # Page 1
    resp = await client.get("/cases", params={"limit": 2}, headers=_dev_headers(principal))
    assert resp.status_code == 200, resp.text
    page1 = CaseListResponse.model_validate(resp.json())
    assert len(page1.items) == 2
    assert page1.next_cursor is not None

    # Page 2
    resp = await client.get(
        "/cases",
        params={"limit": 2, "cursor": page1.next_cursor},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200
    page2 = CaseListResponse.model_validate(resp.json())
    assert len(page2.items) == 2
    assert page2.next_cursor is not None

    # Page 3 — final page (1 row).
    resp = await client.get(
        "/cases",
        params={"limit": 2, "cursor": page2.next_cursor},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200
    page3 = CaseListResponse.model_validate(resp.json())
    assert len(page3.items) == 1
    assert page3.next_cursor is None

    seen_ids = [r.id for r in (*page1.items, *page2.items, *page3.items)]
    assert len(seen_ids) == len(set(seen_ids))

    created = [r.created_at for r in (*page1.items, *page2.items, *page3.items)]
    assert created == sorted(created, reverse=True)


# ---------------------------------------------------------------------------
# Tests — GET /cases/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_case_detail_includes_attached_clips_and_recent_audit(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Detail must surface the attached clips and the case-scoped audit log."""
    client, s = http_client_with_session
    tenant_id, user_id = await _seed_tenant_with_creator(s)
    principal = _principal(tenant_id=tenant_id, user_id=user_id)

    truck = await _seed_truck(s, tenant_id=tenant_id, label="T-Detail")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    clip = await _seed_clip(s, tenant_id=tenant_id, truck_id=truck.id, started_at=base)

    case = await _create_via_api(client, principal)

    # Attach the clip via the API so we exercise the same code path.
    resp = await client.post(
        f"/cases/{case.id}/clips",
        json={"clip_id": str(clip.id), "note": "first attach"},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text

    resp = await client.get(f"/cases/{case.id}", headers=_dev_headers(principal))
    assert resp.status_code == 200, resp.text
    detail = CaseDetail.model_validate(resp.json())

    assert len(detail.clips) == 1
    attached = detail.clips[0]
    assert attached.clip_id == clip.id
    assert attached.truck_label == "T-Detail"
    assert attached.note == "first attach"
    assert attached.started_at == base
    assert attached.attached_by == user_id

    # Recent audit must include at least the create and the attach.
    actions = [a.action for a in detail.recent_audit]
    assert "case.created" in actions
    assert "case.clip_attached" in actions
    assert all(a.target_id == case.id for a in detail.recent_audit)


@pytest.mark.asyncio
async def test_get_case_cross_tenant_returns_404(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    client, s = http_client_with_session
    tenant_a, user_a = await _seed_tenant_with_creator(s)
    tenant_b, user_b = await _seed_tenant_with_creator(s)
    principal_a = _principal(tenant_id=tenant_a, user_id=user_a)
    principal_b = _principal(tenant_id=tenant_b, user_id=user_b)

    case_a = await _create_via_api(client, principal_a)

    resp = await client.get(f"/cases/{case_a.id}", headers=_dev_headers(principal_b))
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Tests — POST /cases/{id}/clips
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attach_clip_writes_audit_and_appears_in_detail(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    client, s = http_client_with_session
    tenant_id, user_id = await _seed_tenant_with_creator(s)
    principal = _principal(tenant_id=tenant_id, user_id=user_id)

    truck = await _seed_truck(s, tenant_id=tenant_id, label="T-Att")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    clip = await _seed_clip(s, tenant_id=tenant_id, truck_id=truck.id, started_at=base)

    case = await _create_via_api(client, principal)

    resp = await client.post(
        f"/cases/{case.id}/clips",
        json={"clip_id": str(clip.id), "note": "good evidence"},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    detail = CaseDetail.model_validate(resp.json())
    assert len(detail.clips) == 1
    assert detail.clips[0].clip_id == clip.id

    rows = (
        (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.target_type == "case",
                    AuditLog.target_id == case.id,
                    AuditLog.action == "case.clip_attached",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].payload == {"clip_id": str(clip.id), "note": "good evidence"}


@pytest.mark.asyncio
async def test_attach_clip_is_idempotent(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Attaching the same clip twice must return 200 both times, no error."""
    client, s = http_client_with_session
    tenant_id, user_id = await _seed_tenant_with_creator(s)
    principal = _principal(tenant_id=tenant_id, user_id=user_id)

    truck = await _seed_truck(s, tenant_id=tenant_id, label="T-Idem")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    clip = await _seed_clip(s, tenant_id=tenant_id, truck_id=truck.id, started_at=base)

    case = await _create_via_api(client, principal)

    body = {"clip_id": str(clip.id)}
    resp1 = await client.post(f"/cases/{case.id}/clips", json=body, headers=_dev_headers(principal))
    assert resp1.status_code == 200, resp1.text

    resp2 = await client.post(f"/cases/{case.id}/clips", json=body, headers=_dev_headers(principal))
    assert resp2.status_code == 200, resp2.text

    # Only one join row.
    join_rows = (
        (
            await s.execute(
                select(CaseClip).where(CaseClip.case_id == case.id, CaseClip.clip_id == clip.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(join_rows) == 1

    # Only one audit row.
    audit_rows = (
        (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.target_type == "case",
                    AuditLog.target_id == case.id,
                    AuditLog.action == "case.clip_attached",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audit_rows) == 1


@pytest.mark.asyncio
async def test_attach_clip_cross_tenant_clip_returns_404(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Caller's tenant must own both the case AND the clip."""
    client, s = http_client_with_session
    tenant_a, user_a = await _seed_tenant_with_creator(s)
    tenant_b, user_b = await _seed_tenant_with_creator(s)
    principal_a = _principal(tenant_id=tenant_a, user_id=user_a)
    principal_b = _principal(tenant_id=tenant_b, user_id=user_b)

    # Case in tenant A; clip in tenant B.
    truck_b = await _seed_truck(s, tenant_id=tenant_b, label="T-B")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    clip_b = await _seed_clip(s, tenant_id=tenant_b, truck_id=truck_b.id, started_at=base)

    case_a = await _create_via_api(client, principal_a)

    # Tenant A trying to attach tenant B's clip → 404 on the clip.
    resp = await client.post(
        f"/cases/{case_a.id}/clips",
        json={"clip_id": str(clip_b.id)},
        headers=_dev_headers(principal_a),
    )
    assert resp.status_code == 404, resp.text

    # No audit row written for the failed attempt.
    audit_rows = (
        (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.target_type == "case",
                    AuditLog.target_id == case_a.id,
                    AuditLog.action == "case.clip_attached",
                )
            )
        )
        .scalars()
        .all()
    )
    assert audit_rows == []

    # Symmetric: tenant A trying to attach own clip to tenant B's case → 404 on the case.
    case_b = await _create_via_api(client, principal_b)
    truck_a = await _seed_truck(s, tenant_id=tenant_a, label="T-A")
    clip_a = await _seed_clip(s, tenant_id=tenant_a, truck_id=truck_a.id, started_at=base)
    resp = await client.post(
        f"/cases/{case_b.id}/clips",
        json={"clip_id": str(clip_a.id)},
        headers=_dev_headers(principal_a),
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Tests — PATCH /cases/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_case_updates_supplied_fields_and_writes_audit_with_changes(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Only supplied fields move; one audit row carries every change."""
    client, s = http_client_with_session
    tenant_id, user_id = await _seed_tenant_with_creator(s)
    assignee = await _seed_user(s, tenant_id=tenant_id)
    principal = _principal(tenant_id=tenant_id, user_id=user_id)

    case = await _create_via_api(
        client,
        principal,
        requester_name="Original",
        requester_org="OriginalCo",
    )

    resp = await client.patch(
        f"/cases/{case.id}",
        json={
            "requester_name": "Updated",
            "assignee_user_id": str(assignee.id),
            "status": "under_review",
        },
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    updated = CaseDetail.model_validate(resp.json())
    assert updated.requester_name == "Updated"
    assert updated.assignee_user_id == assignee.id
    assert updated.status == CaseStatus.under_review
    # Unchanged field stays put.
    assert updated.requester_org == "OriginalCo"

    audit_rows = (
        (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.target_type == "case",
                    AuditLog.target_id == case.id,
                    AuditLog.action == "case.updated",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audit_rows) == 1
    changes = audit_rows[0].payload["changes"]
    assert changes["requester_name"] == "Updated"
    assert changes["assignee_user_id"] == str(assignee.id)
    assert changes["status"] == "under_review"
    # Other fields not in body must NOT appear in the audit changes.
    assert "requester_org" not in changes


@pytest.mark.asyncio
async def test_patch_case_rejects_status_closed(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """``PATCH`` must refuse ``status="closed"`` — use ``/close`` instead."""
    client, s = http_client_with_session
    tenant_id, user_id = await _seed_tenant_with_creator(s)
    principal = _principal(tenant_id=tenant_id, user_id=user_id)

    case = await _create_via_api(client, principal)

    resp = await client.patch(
        f"/cases/{case.id}",
        json={"status": "closed"},
        headers=_dev_headers(principal),
    )
    # Pydantic Literal mismatch → 422 (FastAPI validation error).
    assert resp.status_code == 422, resp.text

    # Case status must still be open.
    refetched = (await s.execute(select(Case).where(Case.id == case.id))).scalar_one()
    assert refetched.status == CaseStatus.open


# ---------------------------------------------------------------------------
# Tests — POST /cases/{id}/close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_case_sets_status_and_writes_audit(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    client, s = http_client_with_session
    tenant_id, user_id = await _seed_tenant_with_creator(s)
    principal = _principal(tenant_id=tenant_id, user_id=user_id)

    case = await _create_via_api(client, principal)

    resp = await client.post(
        f"/cases/{case.id}/close",
        json={"reason": "investigation complete"},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    closed = CaseDetail.model_validate(resp.json())
    assert closed.status == CaseStatus.closed

    audit_rows = (
        (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.target_type == "case",
                    AuditLog.target_id == case.id,
                    AuditLog.action == "case.closed",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audit_rows) == 1
    assert audit_rows[0].payload == {"reason": "investigation complete"}


@pytest.mark.asyncio
async def test_close_already_closed_returns_409(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    client, s = http_client_with_session
    tenant_id, user_id = await _seed_tenant_with_creator(s)
    principal = _principal(tenant_id=tenant_id, user_id=user_id)

    case = await _create_via_api(client, principal)
    resp = await client.post(
        f"/cases/{case.id}/close",
        json={"reason": "done"},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text

    # Second close must 409.
    resp = await client.post(
        f"/cases/{case.id}/close",
        json={"reason": "still done"},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "case already closed"

    # Only ONE close audit row.
    audit_rows = (
        (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.target_type == "case",
                    AuditLog.target_id == case.id,
                    AuditLog.action == "case.closed",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audit_rows) == 1


@pytest.mark.asyncio
async def test_close_case_rejects_empty_reason(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """An empty reason fails schema validation — case stays open."""
    client, s = http_client_with_session
    tenant_id, user_id = await _seed_tenant_with_creator(s)
    principal = _principal(tenant_id=tenant_id, user_id=user_id)

    case = await _create_via_api(client, principal)

    resp = await client.post(
        f"/cases/{case.id}/close",
        json={"reason": ""},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 422, resp.text

    refetched = (await s.execute(select(Case).where(Case.id == case.id))).scalar_one()
    assert refetched.status == CaseStatus.open


# ---------------------------------------------------------------------------
# Touch the imports referenced via type checking to keep ruff/mypy happy.
# ---------------------------------------------------------------------------
_ = CaseRow  # re-exported for symmetry in case future tests need it
