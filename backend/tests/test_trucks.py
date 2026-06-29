"""Tests for ``GET /trucks`` and ``GET /trucks/{id}``.

Test pattern mirrors ``test_clips.py``:

* Probe the dev Postgres at import time; skip cleanly when unreachable.
* Each test gets a fresh session whose outer transaction is rolled back
  at teardown so no data leaks across tests.
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
from app.models.clip import Clip
from app.models.driver import Driver
from app.models.tenant import Tenant
from app.models.truck import Truck

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
async def http_client_with_session() -> AsyncIterator[
    tuple[httpx.AsyncClient, AsyncSession]
]:
    """HTTPX client wired to the FastAPI app with a shared, rollback-safe session."""
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
    vin: str | None = None,
    dashcam_serial: str | None = None,
) -> Truck:
    truck = Truck(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        label=label,
        vin=vin,
        dashcam_serial=dashcam_serial,
    )
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
    started_at: datetime,
    duration_s: int = 60,
    driver_id: uuid.UUID | None = None,
) -> Clip:
    ended_at = started_at + timedelta(seconds=duration_s)
    clip = Clip(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        truck_id=truck_id,
        driver_id=driver_id,
        started_at=started_at,
        ended_at=ended_at,
        duration_s=duration_s,
        storage_key=f"{tenant_id}/{started_at.date()}/{uuid.uuid4()}.mp4",
    )
    session.add(clip)
    await session.flush()
    return clip


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_trucks_tenant_isolated(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Trucks from another tenant must not appear in the caller's list."""
    client, s = http_client_with_session

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _seed_tenant(s, tenant_a)
    await _seed_tenant(s, tenant_b)

    truck_a = await _seed_truck(s, tenant_id=tenant_a, label="A-1")
    truck_b = await _seed_truck(s, tenant_id=tenant_b, label="B-1")

    principal_a = _principal(tenant_id=tenant_a)
    resp = await client.get("/trucks", headers=_dev_headers(principal_a))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    ids = {r["id"] for r in rows}
    assert str(truck_a.id) in ids
    assert str(truck_b.id) not in ids
    assert all(r["tenant_id"] == str(tenant_a) for r in rows)


@pytest.mark.asyncio
async def test_list_trucks_orders_by_label(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Trucks come back alphabetically by label so the multi-select feels sane."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    # Insert out-of-order on purpose to prove the sort is on the query.
    await _seed_truck(s, tenant_id=tenant, label="Truck-C")
    await _seed_truck(s, tenant_id=tenant, label="Truck-A")
    await _seed_truck(s, tenant_id=tenant, label="Truck-B")

    principal = _principal(tenant_id=tenant)
    resp = await client.get("/trucks", headers=_dev_headers(principal))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    labels = [r["label"] for r in rows]
    assert labels == ["Truck-A", "Truck-B", "Truck-C"]


@pytest.mark.asyncio
async def test_get_truck_returns_single_row(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """``GET /trucks/{id}`` returns one truck with vin / serial fields populated."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(
        s,
        tenant_id=tenant,
        label="Truck-X",
        vin="1HGCM82633A123456",
        dashcam_serial="CAM-001",
    )

    principal = _principal(tenant_id=tenant)
    resp = await client.get(
        f"/trucks/{truck.id}", headers=_dev_headers(principal)
    )
    assert resp.status_code == 200, resp.text
    row = resp.json()
    assert row["id"] == str(truck.id)
    assert row["label"] == "Truck-X"
    assert row["vin"] == "1HGCM82633A123456"
    assert row["dashcam_serial"] == "CAM-001"


@pytest.mark.asyncio
async def test_get_truck_cross_tenant_returns_404(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """A truck belonging to another tenant returns 404, not 403, to avoid probing."""
    client, s = http_client_with_session

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _seed_tenant(s, tenant_a)
    await _seed_tenant(s, tenant_b)

    truck_b = await _seed_truck(s, tenant_id=tenant_b, label="B-1")

    principal_a = _principal(tenant_id=tenant_a)
    resp = await client.get(
        f"/trucks/{truck_b.id}", headers=_dev_headers(principal_a)
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# /trucks/{id}/days
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_days_endpoint_returns_descending_dates(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """5 clips across 3 days produce 3 rows, newest day first."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-1")

    base = datetime(2026, 6, 27, 9, 0, tzinfo=UTC)
    # Day 1 (oldest): 2 clips.
    await _seed_clip(s, tenant_id=tenant, truck_id=truck.id, started_at=base, duration_s=30)
    await _seed_clip(
        s, tenant_id=tenant, truck_id=truck.id, started_at=base + timedelta(hours=1), duration_s=45
    )
    # Day 2: 1 clip.
    await _seed_clip(
        s, tenant_id=tenant, truck_id=truck.id, started_at=base + timedelta(days=1), duration_s=120
    )
    # Day 3 (newest): 2 clips.
    await _seed_clip(
        s, tenant_id=tenant, truck_id=truck.id, started_at=base + timedelta(days=2), duration_s=60
    )
    await _seed_clip(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        started_at=base + timedelta(days=2, hours=2),
        duration_s=90,
    )

    principal = _principal(tenant_id=tenant)
    resp = await client.get(
        f"/trucks/{truck.id}/days", headers=_dev_headers(principal)
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 3
    dates = [r["date"] for r in rows]
    assert dates == sorted(dates, reverse=True)
    # Newest day has 2 clips and 150s total.
    assert rows[0]["clip_count"] == 2
    assert rows[0]["total_duration_s"] == 150
    # Middle day: 1 clip, 120s.
    assert rows[1]["clip_count"] == 1
    assert rows[1]["total_duration_s"] == 120
    # Oldest day: 2 clips, 75s.
    assert rows[2]["clip_count"] == 2
    assert rows[2]["total_duration_s"] == 75


@pytest.mark.asyncio
async def test_days_endpoint_filters_by_driver(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """``?driver_id=`` narrows the rollup to one driver's clips."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-1")
    alice = await _seed_driver(s, tenant_id=tenant, name="Alice")
    bob = await _seed_driver(s, tenant_id=tenant, name="Bob")

    base = datetime(2026, 6, 27, 9, 0, tzinfo=UTC)
    # Same day, two drivers.
    await _seed_clip(
        s, tenant_id=tenant, truck_id=truck.id, started_at=base, duration_s=30, driver_id=alice.id
    )
    await _seed_clip(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        started_at=base + timedelta(hours=1),
        duration_s=45,
        driver_id=alice.id,
    )
    await _seed_clip(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        started_at=base + timedelta(hours=2),
        duration_s=60,
        driver_id=bob.id,
    )

    principal = _principal(tenant_id=tenant)
    resp = await client.get(
        f"/trucks/{truck.id}/days?driver_id={alice.id}",
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["clip_count"] == 2
    assert rows[0]["total_duration_s"] == 75


@pytest.mark.asyncio
async def test_days_endpoint_filters_by_date_range(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """``?from=`` and ``?to=`` restrict the window of clips considered."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-1")

    base = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    for delta_days in (0, 5, 10):
        await _seed_clip(
            s,
            tenant_id=tenant,
            truck_id=truck.id,
            started_at=base + timedelta(days=delta_days),
            duration_s=60,
        )

    principal = _principal(tenant_id=tenant)
    # Window includes only the middle clip. Pass via ``params=`` so httpx
    # URL-encodes the ``+`` in the timezone offset instead of letting the
    # raw ``+`` be interpreted as a space by the server.
    resp = await client.get(
        f"/trucks/{truck.id}/days",
        headers=_dev_headers(principal),
        params={
            "from": (base + timedelta(days=3)).isoformat(),
            "to": (base + timedelta(days=7)).isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["clip_count"] == 1


@pytest.mark.asyncio
async def test_days_endpoint_cross_tenant_returns_404(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """A truck owned by another tenant returns 404 with detail ``"not found"``."""
    client, s = http_client_with_session

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _seed_tenant(s, tenant_a)
    await _seed_tenant(s, tenant_b)
    truck_b = await _seed_truck(s, tenant_id=tenant_b, label="B-1")

    principal_a = _principal(tenant_id=tenant_a)
    resp = await client.get(
        f"/trucks/{truck_b.id}/days", headers=_dev_headers(principal_a)
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "not found"


@pytest.mark.asyncio
async def test_days_endpoint_unknown_truck_returns_404(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """A nonexistent truck id 404s the same way a cross-tenant one does."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    principal = _principal(tenant_id=tenant)

    resp = await client.get(
        f"/trucks/{uuid.uuid4()}/days", headers=_dev_headers(principal)
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_days_endpoint_caps_limit(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """``?limit>365`` is rejected as a 422 by FastAPI's query validation."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-1")

    principal = _principal(tenant_id=tenant)
    resp = await client.get(
        f"/trucks/{truck.id}/days?limit=10000",
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 422
