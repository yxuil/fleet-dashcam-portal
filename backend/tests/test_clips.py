"""Tests for ``GET /clips`` and ``GET /clips/{id}``.

Test pattern mirrors ``test_audit.py``:

* Probe the dev Postgres at import time; skip cleanly when unreachable.
* Each test gets a fresh session whose outer transaction is rolled back
  at teardown so no data leaks across tests.

There's a wrinkle vs. the audit tests: ``GET /clips/{id}?play=true``
calls ``session.commit()`` to persist the audit row. To keep the
rollback-at-teardown contract intact, we open the session with
``join_transaction_mode="create_savepoint"``. A connection-level outer
transaction wraps the whole test; the handler's ``commit()`` then merely
releases a SAVEPOINT, and the outer transaction stays rollback-able.

We also stub out :func:`app.storage.get_signed_url` so the suite doesn't
need a live MinIO to test the play-URL path.
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

from app import storage as storage_module
from app.auth import Principal
from app.config import settings
from app.db import get_session
from app.main import app
from app.models.audit import AuditLog
from app.models.clip import Clip
from app.models.driver import Driver
from app.models.tenant import Tenant
from app.models.truck import Truck
from app.schemas.clip import ClipDetail, ClipListResponse

# ---------------------------------------------------------------------------
# DB reachability gate (mirrors test_audit.py / test_schema.py)
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
async def http_client_with_session() -> AsyncIterator[
    tuple[httpx.AsyncClient, AsyncSession]
]:
    """HTTPX client wired to the FastAPI app with a shared, rollback-safe session.

    Uses the ``connection.begin() + join_transaction_mode="create_savepoint"``
    pattern so the handler can call ``session.commit()`` (e.g. the play-URL
    path) without breaking the per-test rollback contract — its commit just
    releases a SAVEPOINT inside our outer transaction.
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
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://test"
                ) as client:
                    yield client, s
            finally:
                app.dependency_overrides.pop(get_session, None)
                await outer_txn.rollback()
    await engine.dispose()


def _principal(tenant_id: uuid.UUID | None = None) -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
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


async def _seed_truck(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    label: str,
) -> Truck:
    truck = Truck(id=uuid.uuid4(), tenant_id=tenant_id, label=label)
    session.add(truck)
    await session.flush()
    return truck


async def _seed_driver(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
) -> Driver:
    driver = Driver(id=uuid.uuid4(), tenant_id=tenant_id, name=name)
    session.add(driver)
    await session.flush()
    return driver


async def _seed_clip(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    truck_id: uuid.UUID,
    driver_id: uuid.UUID | None,
    started_at: datetime,
    duration_s: int = 30,
    storage_key: str | None = None,
    sha256: str | None = None,
    dashcam_firmware: str | None = None,
) -> Clip:
    clip_id = uuid.uuid4()
    clip = Clip(
        id=clip_id,
        tenant_id=tenant_id,
        truck_id=truck_id,
        driver_id=driver_id,
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=duration_s),
        duration_s=duration_s,
        storage_key=storage_key or f"{tenant_id}/2026/06/29/{clip_id}.mp4",
        sha256=sha256,
        dashcam_firmware=dashcam_firmware,
    )
    session.add(clip)
    await session.flush()
    return clip


# ---------------------------------------------------------------------------
# Tests — list endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_clips_returns_caller_tenant_only(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Cross-tenant clips must not appear in the caller's list."""
    client, s = http_client_with_session

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _seed_tenant(s, tenant_a)
    await _seed_tenant(s, tenant_b)

    truck_a = await _seed_truck(s, tenant_id=tenant_a, label="Truck-A")
    truck_b = await _seed_truck(s, tenant_id=tenant_b, label="Truck-B")

    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    clip_a = await _seed_clip(
        s, tenant_id=tenant_a, truck_id=truck_a.id, driver_id=None, started_at=base
    )
    clip_b = await _seed_clip(
        s, tenant_id=tenant_b, truck_id=truck_b.id, driver_id=None, started_at=base
    )

    principal_a = _principal(tenant_id=tenant_a)
    resp = await client.get("/clips", headers=_dev_headers(principal_a))
    assert resp.status_code == 200, resp.text
    body = ClipListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    assert clip_a.id in ids
    assert clip_b.id not in ids
    assert all(row.tenant_id == tenant_a for row in body.items)


@pytest.mark.asyncio
async def test_list_clips_filter_by_truck_id(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck1 = await _seed_truck(s, tenant_id=tenant, label="T-1")
    truck2 = await _seed_truck(s, tenant_id=tenant, label="T-2")

    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    c1 = await _seed_clip(
        s, tenant_id=tenant, truck_id=truck1.id, driver_id=None, started_at=base
    )
    c2 = await _seed_clip(
        s,
        tenant_id=tenant,
        truck_id=truck2.id,
        driver_id=None,
        started_at=base + timedelta(minutes=1),
    )

    principal = _principal(tenant_id=tenant)
    resp = await client.get(
        "/clips", params={"truck_id": str(truck1.id)}, headers=_dev_headers(principal)
    )
    assert resp.status_code == 200, resp.text
    body = ClipListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    assert c1.id in ids
    assert c2.id not in ids


@pytest.mark.asyncio
async def test_list_clips_filter_by_driver_id(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-1")
    driver1 = await _seed_driver(s, tenant_id=tenant, name="Alice")
    driver2 = await _seed_driver(s, tenant_id=tenant, name="Bob")

    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    c1 = await _seed_clip(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        driver_id=driver1.id,
        started_at=base,
    )
    c2 = await _seed_clip(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        driver_id=driver2.id,
        started_at=base + timedelta(minutes=1),
    )
    # Driver-less clip must not match a driver_id filter.
    c3 = await _seed_clip(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        driver_id=None,
        started_at=base + timedelta(minutes=2),
    )

    principal = _principal(tenant_id=tenant)
    resp = await client.get(
        "/clips",
        params={"driver_id": str(driver1.id)},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    body = ClipListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    assert c1.id in ids
    assert c2.id not in ids
    assert c3.id not in ids


@pytest.mark.asyncio
async def test_list_clips_filter_by_date_range(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """``from`` and ``to`` are inclusive bounds on ``started_at``."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-1")

    # Five clips, one minute apart.
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    clips = []
    for i in range(5):
        c = await _seed_clip(
            s,
            tenant_id=tenant,
            truck_id=truck.id,
            driver_id=None,
            started_at=base + timedelta(minutes=i),
        )
        clips.append(c)

    principal = _principal(tenant_id=tenant)

    # Window covering exactly clips[1] .. clips[3] inclusive.
    from_ts = (base + timedelta(minutes=1)).isoformat()
    to_ts = (base + timedelta(minutes=3)).isoformat()
    resp = await client.get(
        "/clips",
        params={"from": from_ts, "to": to_ts},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    body = ClipListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    assert clips[0].id not in ids
    assert clips[1].id in ids
    assert clips[2].id in ids
    assert clips[3].id in ids
    assert clips[4].id not in ids


@pytest.mark.asyncio
async def test_list_clips_text_filter_matches_truck_label(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    matching = await _seed_truck(s, tenant_id=tenant, label="Freightliner-FOX-99")
    other = await _seed_truck(s, tenant_id=tenant, label="Volvo-V101")

    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    c1 = await _seed_clip(
        s, tenant_id=tenant, truck_id=matching.id, driver_id=None, started_at=base
    )
    c2 = await _seed_clip(
        s,
        tenant_id=tenant,
        truck_id=other.id,
        driver_id=None,
        started_at=base + timedelta(minutes=1),
    )

    principal = _principal(tenant_id=tenant)
    # Case-insensitive substring match — query "fox" should hit "FOX".
    resp = await client.get(
        "/clips", params={"text": "fox"}, headers=_dev_headers(principal)
    )
    assert resp.status_code == 200, resp.text
    body = ClipListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    assert c1.id in ids
    assert c2.id not in ids


@pytest.mark.asyncio
async def test_list_clips_text_filter_matches_driver_name(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="Plain-Truck")
    driver_match = await _seed_driver(s, tenant_id=tenant, name="Quinella Smith")
    driver_other = await _seed_driver(s, tenant_id=tenant, name="John Doe")

    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    c_match = await _seed_clip(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        driver_id=driver_match.id,
        started_at=base,
    )
    c_other = await _seed_clip(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        driver_id=driver_other.id,
        started_at=base + timedelta(minutes=1),
    )

    principal = _principal(tenant_id=tenant)
    # "Plain-Truck" does not contain "quinella"; only the driver name does.
    resp = await client.get(
        "/clips", params={"text": "Quinella"}, headers=_dev_headers(principal)
    )
    assert resp.status_code == 200, resp.text
    body = ClipListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    assert c_match.id in ids
    assert c_other.id not in ids


@pytest.mark.asyncio
async def test_list_clips_paginates(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """``limit=2`` plus the returned cursor must page newest-first with no overlap."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-pg")

    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    seeded: list[Clip] = []
    for i in range(5):
        c = await _seed_clip(
            s,
            tenant_id=tenant,
            truck_id=truck.id,
            driver_id=None,
            started_at=base + timedelta(minutes=i),
        )
        seeded.append(c)

    principal = _principal(tenant_id=tenant)

    # Page 1
    resp = await client.get(
        "/clips", params={"limit": 2}, headers=_dev_headers(principal)
    )
    assert resp.status_code == 200, resp.text
    page1 = ClipListResponse.model_validate(resp.json())
    assert len(page1.items) == 2
    assert page1.next_cursor is not None

    # Page 2
    resp = await client.get(
        "/clips",
        params={"limit": 2, "cursor": page1.next_cursor},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    page2 = ClipListResponse.model_validate(resp.json())
    assert len(page2.items) == 2
    assert page2.next_cursor is not None

    # Page 3 (last one)
    resp = await client.get(
        "/clips",
        params={"limit": 2, "cursor": page2.next_cursor},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    page3 = ClipListResponse.model_validate(resp.json())
    assert len(page3.items) == 1
    assert page3.next_cursor is None

    seen_ids = [r.id for r in (*page1.items, *page2.items, *page3.items)]
    assert len(seen_ids) == len(set(seen_ids))  # no duplicates across pages

    # Newest-first ordering — started_at descending.
    started = [r.started_at for r in (*page1.items, *page2.items, *page3.items)]
    assert started == sorted(started, reverse=True)


# ---------------------------------------------------------------------------
# Tests — detail endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_clip_by_id_returns_metadata_without_playback_url_by_default(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Bare GET must NOT mint a URL and must NOT write an audit row."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-detail")
    driver = await _seed_driver(s, tenant_id=tenant, name="Detail Driver")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    clip = await _seed_clip(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        driver_id=driver.id,
        started_at=base,
        sha256="cafebabe" * 8,
        dashcam_firmware="v1.2.3",
    )

    principal = _principal(tenant_id=tenant)
    resp = await client.get(f"/clips/{clip.id}", headers=_dev_headers(principal))
    assert resp.status_code == 200, resp.text
    detail = ClipDetail.model_validate(resp.json())
    assert detail.id == clip.id
    assert detail.truck_label == "T-detail"
    assert detail.driver_name == "Detail Driver"
    assert detail.sha256 == "cafebabe" * 8
    assert detail.dashcam_firmware == "v1.2.3"
    assert detail.playback_url is None

    # Audit row must NOT have been written.
    rows = (
        await s.execute(
            select(AuditLog).where(
                AuditLog.target_id == clip.id,
                AuditLog.action == "clip.play_url_minted",
            )
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_get_clip_play_true_returns_stream_route_in_local_mode(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``?play=true`` in local mode returns the relative stream route and audits."""
    client, s = http_client_with_session

    # Force the storage layer into local mode for this test so the playback
    # URL is the relative router path, not a signed S3 URL.
    monkeypatch.setattr(settings, "storage_backend", "local")

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-play-local")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    clip = await _seed_clip(
        s, tenant_id=tenant, truck_id=truck.id, driver_id=None, started_at=base
    )

    principal = _principal(tenant_id=tenant)
    resp = await client.get(
        f"/clips/{clip.id}",
        params={"play": "true"},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    detail = ClipDetail.model_validate(resp.json())
    assert detail.playback_url == f"/clips/{clip.id}/stream"

    # Audit row must exist with the documented action / target / payload.
    audit_rows = (
        await s.execute(
            select(AuditLog).where(
                AuditLog.target_id == clip.id,
                AuditLog.action == "clip.play_url_minted",
            )
        )
    ).scalars().all()
    assert len(audit_rows) == 1
    audit = audit_rows[0]
    assert audit.tenant_id == tenant
    assert audit.actor_user_id == principal.user_id
    assert audit.target_type == "clip"
    assert audit.payload == {
        "signed_url_ttl_s": storage_module.DEFAULT_SIGNED_URL_TTL_S
    }

    # Also verify the audit row is surfaced via GET /audit?target_id=...
    resp = await client.get(
        "/audit",
        params={"target_id": str(clip.id), "action": "clip.play_url_minted"},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    audit_listing = resp.json()
    assert len(audit_listing["items"]) == 1
    assert audit_listing["items"][0]["target_id"] == str(clip.id)


@pytest.mark.asyncio
async def test_get_clip_play_true_returns_signed_url_in_s3_mode(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same handler, but with ``storage_backend="s3"`` returns a signed URL.

    Stubs ``get_signed_url`` so the suite doesn't depend on a live MinIO.
    """
    client, s = http_client_with_session

    monkeypatch.setattr(settings, "storage_backend", "s3")

    sentinel_url = "https://signed.example.test/clip.mp4?sig=stub"

    async def _fake_get_signed_url(
        tenant_id: uuid.UUID, key: str, expires_s: int = 3600
    ) -> str:
        return sentinel_url

    # ``get_playback_url`` (in s3 mode) calls ``storage.get_signed_url`` —
    # patch the module attribute so the indirection still resolves.
    monkeypatch.setattr(storage_module, "get_signed_url", _fake_get_signed_url)

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-play-s3")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    clip = await _seed_clip(
        s, tenant_id=tenant, truck_id=truck.id, driver_id=None, started_at=base
    )

    principal = _principal(tenant_id=tenant)
    resp = await client.get(
        f"/clips/{clip.id}",
        params={"play": "true"},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    detail = ClipDetail.model_validate(resp.json())
    assert detail.playback_url == sentinel_url


@pytest.mark.asyncio
async def test_get_clip_cross_tenant_returns_404(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Tenant B asking for tenant A's clip must get an honest 404 (not 403)."""
    client, s = http_client_with_session

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _seed_tenant(s, tenant_a)
    await _seed_tenant(s, tenant_b)

    truck_a = await _seed_truck(s, tenant_id=tenant_a, label="T-A")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    clip_a = await _seed_clip(
        s, tenant_id=tenant_a, truck_id=truck_a.id, driver_id=None, started_at=base
    )

    principal_b = _principal(tenant_id=tenant_b)
    resp = await client.get(f"/clips/{clip_a.id}", headers=_dev_headers(principal_b))
    assert resp.status_code == 404, resp.text
    # No mention of "forbidden" — we don't want to leak that the clip exists
    # under another tenant.
    detail = resp.json().get("detail", "")
    assert "forbid" not in str(detail).lower()


@pytest.mark.asyncio
async def test_get_clip_unknown_id_returns_404(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)

    principal = _principal(tenant_id=tenant)
    bogus = uuid.uuid4()
    resp = await client.get(f"/clips/{bogus}", headers=_dev_headers(principal))
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Tests — POST /clips/{id}/audit (T12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_clip_audit_writes_row_with_allowed_action(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Player-emitted ``clip.play`` must persist as a tenant-scoped audit row."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-pa")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    clip = await _seed_clip(
        s, tenant_id=tenant, truck_id=truck.id, driver_id=None, started_at=base
    )

    principal = _principal(tenant_id=tenant)
    resp = await client.post(
        f"/clips/{clip.id}/audit",
        json={"action": "clip.play", "payload": {"view_duration_s": 0}},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 204, resp.text

    # The audit row should be readable via the model directly. We don't
    # rely on GET /audit here because the test session's connection is
    # different from a fresh request; instead we query the same session.
    rows = (
        await s.execute(
            select(AuditLog).where(
                AuditLog.target_id == clip.id,
                AuditLog.action == "clip.play",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    audit = rows[0]
    assert audit.tenant_id == tenant
    assert audit.actor_user_id == principal.user_id
    assert audit.target_type == "clip"
    assert audit.payload == {"view_duration_s": 0}


@pytest.mark.asyncio
async def test_post_clip_audit_rejects_unknown_action(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Anything outside the closed-set of player actions must 422/400 and audit nothing.

    Pydantic's ``Literal`` validator raises 422 on a bad action; the
    runtime guard in the handler catches anything that slips past
    (e.g. if the schema is ever relaxed) with 400. Either way the row
    must NOT land in audit_log.
    """
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-pa-bad")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    clip = await _seed_clip(
        s, tenant_id=tenant, truck_id=truck.id, driver_id=None, started_at=base
    )

    principal = _principal(tenant_id=tenant)
    resp = await client.post(
        f"/clips/{clip.id}/audit",
        json={"action": "clip.deleted", "payload": {}},
        headers=_dev_headers(principal),
    )
    # Pydantic Literal rejects with 422; the handler's belt-and-braces
    # check would surface as 400. Either is fine — what matters is
    # nothing got written.
    assert resp.status_code in (400, 422), resp.text

    rows = (
        await s.execute(
            select(AuditLog).where(AuditLog.target_id == clip.id)
        )
    ).scalars().all()
    assert rows == []


# ---------------------------------------------------------------------------
# Tests — GET /clips/{id}/stream (T17, local-mode playback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_endpoint_serves_file_after_local_put(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """End-to-end: write a clip via local put_object, GET /stream serves the bytes."""
    client, s = http_client_with_session

    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_root", tmp_path)

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-stream")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    clip = await _seed_clip(
        s, tenant_id=tenant, truck_id=truck.id, driver_id=None, started_at=base
    )

    # Write a known payload through the public storage API so we exercise
    # the same code path the seed uses (sans symlink).
    payload = b"\x00\x01\x02\x03local-stream-payload"
    await storage_module.put_object(tenant, clip.storage_key, payload)

    principal = _principal(tenant_id=tenant)
    resp = await client.get(
        f"/clips/{clip.id}/stream",
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("content-type") == "video/mp4"
    # Starlette's FileResponse advertises range support, which the
    # ``<video>`` element relies on for scrubbing.
    assert resp.headers.get("accept-ranges") == "bytes"
    assert resp.content == payload


@pytest.mark.asyncio
async def test_stream_endpoint_supports_range_requests(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A ``Range: bytes=0-9`` request returns 206 with the requested slice."""
    client, s = http_client_with_session

    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_root", tmp_path)

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-range")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    clip = await _seed_clip(
        s, tenant_id=tenant, truck_id=truck.id, driver_id=None, started_at=base
    )
    payload = bytes(range(64))  # 64 bytes 0x00..0x3f
    await storage_module.put_object(tenant, clip.storage_key, payload)

    principal = _principal(tenant_id=tenant)
    resp = await client.get(
        f"/clips/{clip.id}/stream",
        headers={**_dev_headers(principal), "Range": "bytes=0-9"},
    )
    assert resp.status_code == 206, resp.text
    assert resp.content == payload[:10]


@pytest.mark.asyncio
async def test_stream_endpoint_returns_404_for_cross_tenant(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Tenant B requesting tenant A's stream gets 404 even if the file exists."""
    client, s = http_client_with_session

    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_root", tmp_path)

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _seed_tenant(s, tenant_a)
    await _seed_tenant(s, tenant_b)

    truck_a = await _seed_truck(s, tenant_id=tenant_a, label="T-A-stream")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    clip_a = await _seed_clip(
        s, tenant_id=tenant_a, truck_id=truck_a.id, driver_id=None, started_at=base
    )
    await storage_module.put_object(tenant_a, clip_a.storage_key, b"a-only")

    principal_b = _principal(tenant_id=tenant_b)
    resp = await client.get(
        f"/clips/{clip_a.id}/stream",
        headers=_dev_headers(principal_b),
    )
    assert resp.status_code == 404, resp.text
    detail = resp.json().get("detail", "")
    assert "forbid" not in str(detail).lower()


@pytest.mark.asyncio
async def test_stream_endpoint_returns_404_when_file_missing(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A clip row whose on-disk file is absent returns 404 (not 500)."""
    client, s = http_client_with_session

    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_root", tmp_path)

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-missing")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    clip = await _seed_clip(
        s, tenant_id=tenant, truck_id=truck.id, driver_id=None, started_at=base
    )
    # Intentionally do NOT write any bytes — exercises the "row exists,
    # file doesn't" branch.

    principal = _principal(tenant_id=tenant)
    resp = await client.get(
        f"/clips/{clip.id}/stream",
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_stream_endpoint_blocks_path_traversal(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Synthesize a malicious storage_key with ``../`` and confirm 404.

    The tenant-prefix check on storage_key creation already prevents this
    in practice, but the router's resolved-path containment check is
    defence-in-depth: if a future ingest path ever forgot to validate,
    we'd still refuse to serve a file outside STORAGE_ROOT.
    """
    client, s = http_client_with_session

    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_root", tmp_path)

    # Plant a sensitive file *outside* STORAGE_ROOT.
    outside = tmp_path.parent / "secret.mp4"
    outside.write_bytes(b"do-not-leak")

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-trav")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    # Bypass the seeding helper's default key by passing a hand-crafted
    # traversal key. We have to start with ``{tenant}/`` to pass the
    # storage_key shape check at the model boundary (it has no validator,
    # but downstream put_object would refuse it — here we just want a row
    # that points outside the root when joined).
    bad_key = f"{tenant}/../../secret.mp4"
    clip = await _seed_clip(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        driver_id=None,
        started_at=base,
        storage_key=bad_key,
    )

    principal = _principal(tenant_id=tenant)
    resp = await client.get(
        f"/clips/{clip.id}/stream",
        headers=_dev_headers(principal),
    )
    # Either 404 (file containment check) is the only acceptable outcome.
    assert resp.status_code == 404, resp.text
    # And we definitely did not serve the planted bytes.
    assert resp.content != b"do-not-leak"


@pytest.mark.asyncio
async def test_post_clip_audit_cross_tenant_returns_404(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Tenant B posting audit for tenant A's clip must get an honest 404."""
    client, s = http_client_with_session

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _seed_tenant(s, tenant_a)
    await _seed_tenant(s, tenant_b)

    truck_a = await _seed_truck(s, tenant_id=tenant_a, label="T-A")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    clip_a = await _seed_clip(
        s, tenant_id=tenant_a, truck_id=truck_a.id, driver_id=None, started_at=base
    )

    principal_b = _principal(tenant_id=tenant_b)
    resp = await client.post(
        f"/clips/{clip_a.id}/audit",
        json={"action": "clip.play"},
        headers=_dev_headers(principal_b),
    )
    assert resp.status_code == 404, resp.text
    detail = resp.json().get("detail", "")
    assert "forbid" not in str(detail).lower()

    # Cross-tenant attempt must not leak an audit row under either tenant.
    rows = (
        await s.execute(
            select(AuditLog).where(AuditLog.target_id == clip_a.id)
        )
    ).scalars().all()
    assert rows == []
