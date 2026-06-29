"""Tests for ``GET /events``, ``GET /trucks/{id}/events``, and ``POST /events/{id}/triage``.

Test pattern mirrors ``test_clips.py``:

* Probe the dev Postgres at import time; skip cleanly when unreachable.
* Each test gets a fresh session whose outer transaction is rolled back
  at teardown so no data leaks across tests.
* The triage endpoint calls ``session.commit()`` to persist the audit row,
  so we open the session with ``join_transaction_mode="create_savepoint"``
  — same trick the clips tests use for the play-URL path.
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
from app.models.case import Case
from app.models.clip import Clip
from app.models.driver import Driver
from app.models.event import Event, EventSeverity, EventType
from app.models.tenant import Tenant
from app.models.truck import Truck
from app.schemas.event import EventListResponse, EventRow

# ---------------------------------------------------------------------------
# DB reachability gate (mirrors test_clips.py)
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

    ``join_transaction_mode="create_savepoint"`` lets the triage handler
    call ``session.commit()`` without breaking the per-test rollback
    contract — its commit just releases a SAVEPOINT inside our outer
    transaction.
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
    started_at: datetime,
    driver_id: uuid.UUID | None = None,
) -> Clip:
    """Minimal clip row used by the ``clip_id`` filter test.

    Events have an FK to clips, so we can't reference a synthetic UUID
    without first inserting a real row. The other shared seed helpers
    don't need a clip, so we keep this one local.
    """
    clip_id = uuid.uuid4()
    clip = Clip(
        id=clip_id,
        tenant_id=tenant_id,
        truck_id=truck_id,
        driver_id=driver_id,
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=30),
        duration_s=30,
        storage_key=f"{tenant_id}/2026/06/29/{clip_id}.mp4",
    )
    session.add(clip)
    await session.flush()
    return clip


async def _seed_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    truck_id: uuid.UUID,
    occurred_at: datetime,
    type_: EventType = EventType.harsh_brake,
    severity: EventSeverity = EventSeverity.medium,
    clip_id: uuid.UUID | None = None,
) -> Event:
    event = Event(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        truck_id=truck_id,
        clip_id=clip_id,
        occurred_at=occurred_at,
        type=type_,
        severity=severity,
        telemetry={},
        gps_lat=None,
        gps_lng=None,
    )
    session.add(event)
    await session.flush()
    return event


# ---------------------------------------------------------------------------
# Tests — GET /events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_events_tenant_isolated(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Events from another tenant must never appear in the caller's list."""
    client, s = http_client_with_session

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _seed_tenant(s, tenant_a)
    await _seed_tenant(s, tenant_b)

    truck_a = await _seed_truck(s, tenant_id=tenant_a, label="A-1")
    truck_b = await _seed_truck(s, tenant_id=tenant_b, label="B-1")

    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    ev_a = await _seed_event(s, tenant_id=tenant_a, truck_id=truck_a.id, occurred_at=base)
    ev_b = await _seed_event(s, tenant_id=tenant_b, truck_id=truck_b.id, occurred_at=base)

    principal_a = _principal(tenant_id=tenant_a)
    resp = await client.get("/events", headers=_dev_headers(principal_a))
    assert resp.status_code == 200, resp.text
    body = EventListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    assert ev_a.id in ids
    assert ev_b.id not in ids
    assert all(row.tenant_id == tenant_a for row in body.items)


@pytest.mark.asyncio
async def test_list_events_filter_by_truck(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck1 = await _seed_truck(s, tenant_id=tenant, label="T-1")
    truck2 = await _seed_truck(s, tenant_id=tenant, label="T-2")

    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    e1 = await _seed_event(s, tenant_id=tenant, truck_id=truck1.id, occurred_at=base)
    e2 = await _seed_event(
        s,
        tenant_id=tenant,
        truck_id=truck2.id,
        occurred_at=base + timedelta(minutes=1),
    )

    principal = _principal(tenant_id=tenant)
    resp = await client.get(
        "/events", params={"truck_id": str(truck1.id)}, headers=_dev_headers(principal)
    )
    assert resp.status_code == 200, resp.text
    body = EventListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    assert e1.id in ids
    assert e2.id not in ids


@pytest.mark.asyncio
async def test_list_events_filter_by_severity_multi(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Repeated ``severity`` params must be interpreted as set membership."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-1")

    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    e_critical = await _seed_event(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        occurred_at=base,
        severity=EventSeverity.critical,
    )
    e_high = await _seed_event(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        occurred_at=base + timedelta(minutes=1),
        severity=EventSeverity.high,
    )
    e_medium = await _seed_event(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        occurred_at=base + timedelta(minutes=2),
        severity=EventSeverity.medium,
    )
    e_low = await _seed_event(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        occurred_at=base + timedelta(minutes=3),
        severity=EventSeverity.low,
    )

    principal = _principal(tenant_id=tenant)
    resp = await client.get(
        "/events",
        params=[("severity", "high"), ("severity", "critical")],
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    body = EventListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    assert ids == {e_critical.id, e_high.id}
    assert e_medium.id not in ids
    assert e_low.id not in ids


@pytest.mark.asyncio
async def test_list_events_filter_by_type_multi(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Repeated ``type`` params must be interpreted as set membership."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-1")

    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    e_brake = await _seed_event(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        occurred_at=base,
        type_=EventType.harsh_brake,
    )
    e_collision = await _seed_event(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        occurred_at=base + timedelta(minutes=1),
        type_=EventType.collision,
    )
    e_speed = await _seed_event(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        occurred_at=base + timedelta(minutes=2),
        type_=EventType.speeding,
    )

    principal = _principal(tenant_id=tenant)
    resp = await client.get(
        "/events",
        params=[("type", "harsh_brake"), ("type", "collision")],
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    body = EventListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    assert ids == {e_brake.id, e_collision.id}
    assert e_speed.id not in ids


@pytest.mark.asyncio
async def test_list_events_filter_by_date_range(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """``from`` and ``to`` are inclusive bounds on ``occurred_at``."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-1")

    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    events = []
    for i in range(5):
        e = await _seed_event(
            s,
            tenant_id=tenant,
            truck_id=truck.id,
            occurred_at=base + timedelta(minutes=i),
        )
        events.append(e)

    principal = _principal(tenant_id=tenant)
    from_ts = (base + timedelta(minutes=1)).isoformat()
    to_ts = (base + timedelta(minutes=3)).isoformat()
    resp = await client.get(
        "/events",
        params={"from": from_ts, "to": to_ts},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    body = EventListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    assert events[0].id not in ids
    assert events[1].id in ids
    assert events[2].id in ids
    assert events[3].id in ids
    assert events[4].id not in ids


@pytest.mark.asyncio
async def test_list_events_paginates(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """``limit=2`` plus the cursor must page newest-first with no overlap."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-pg")

    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    seeded: list[Event] = []
    for i in range(5):
        e = await _seed_event(
            s,
            tenant_id=tenant,
            truck_id=truck.id,
            occurred_at=base + timedelta(minutes=i),
        )
        seeded.append(e)

    principal = _principal(tenant_id=tenant)

    # Page 1
    resp = await client.get(
        "/events", params={"limit": 2}, headers=_dev_headers(principal)
    )
    assert resp.status_code == 200, resp.text
    page1 = EventListResponse.model_validate(resp.json())
    assert len(page1.items) == 2
    assert page1.next_cursor is not None

    # Page 2
    resp = await client.get(
        "/events",
        params={"limit": 2, "cursor": page1.next_cursor},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    page2 = EventListResponse.model_validate(resp.json())
    assert len(page2.items) == 2
    assert page2.next_cursor is not None

    # Page 3 (last)
    resp = await client.get(
        "/events",
        params={"limit": 2, "cursor": page2.next_cursor},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    page3 = EventListResponse.model_validate(resp.json())
    assert len(page3.items) == 1
    assert page3.next_cursor is None

    seen_ids = [r.id for r in (*page1.items, *page2.items, *page3.items)]
    assert len(seen_ids) == len(set(seen_ids))  # no duplicates across pages

    # Newest-first ordering — occurred_at descending.
    occurred = [r.occurred_at for r in (*page1.items, *page2.items, *page3.items)]
    assert occurred == sorted(occurred, reverse=True)


# ---------------------------------------------------------------------------
# Tests — GET /trucks/{id}/events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trucks_events_returns_404_for_other_tenant_truck(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Tenant B asking for tenant A's truck-events must get an honest 404."""
    client, s = http_client_with_session

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _seed_tenant(s, tenant_a)
    await _seed_tenant(s, tenant_b)

    truck_a = await _seed_truck(s, tenant_id=tenant_a, label="A-1")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    await _seed_event(s, tenant_id=tenant_a, truck_id=truck_a.id, occurred_at=base)

    principal_b = _principal(tenant_id=tenant_b)
    resp = await client.get(
        f"/trucks/{truck_a.id}/events", headers=_dev_headers(principal_b)
    )
    assert resp.status_code == 404, resp.text
    # No mention of "forbidden" — we don't want to leak cross-tenant existence.
    detail = resp.json().get("detail", "")
    assert "forbid" not in str(detail).lower()


@pytest.mark.asyncio
async def test_list_events_filter_by_clip_id(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """``?clip_id=`` must restrict results to events linked to that clip.

    Added in T12 so the video-player page can render harsh-event timeline
    markers without an unrelated-event N+1.
    """
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-cf")

    # We seed four events: two pointing at the same clip, one at a
    # different clip, one unlinked. The unlinked / other-clip ones share
    # all other fields so we can be confident the filter is keying on
    # ``clip_id`` specifically.
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    target_clip = await _seed_clip(
        s, tenant_id=tenant, truck_id=truck.id, started_at=base
    )
    other_clip = await _seed_clip(
        s, tenant_id=tenant, truck_id=truck.id, started_at=base
    )

    e_match_1 = await _seed_event(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        occurred_at=base,
        clip_id=target_clip.id,
    )
    e_match_2 = await _seed_event(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        occurred_at=base + timedelta(minutes=1),
        clip_id=target_clip.id,
    )
    e_other = await _seed_event(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        occurred_at=base + timedelta(minutes=2),
        clip_id=other_clip.id,
    )
    e_unlinked = await _seed_event(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        occurred_at=base + timedelta(minutes=3),
        clip_id=None,
    )

    principal = _principal(tenant_id=tenant)
    resp = await client.get(
        "/events",
        params={"clip_id": str(target_clip.id)},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    body = EventListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    assert ids == {e_match_1.id, e_match_2.id}
    assert e_other.id not in ids
    assert e_unlinked.id not in ids


@pytest.mark.asyncio
async def test_list_events_filter_by_driver_id(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """``?driver_id=`` must filter via the event's attached clip.

    Events have no direct driver FK; driver attribution lives on the
    clip. T13 plumbs this filter via an inner-join on ``clips.driver_id``
    so the ``/drivers/:id/events`` page can scope events to one driver.

    Events whose ``clip_id`` is null are excluded by construction —
    they can't be attributed to a driver. Cross-tenant clips are
    excluded by the outer tenant scope plus the explicit tenant match
    on the clip join.
    """
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-drv")
    driver_a = await _seed_driver(s, tenant_id=tenant, name="Alice")
    driver_b = await _seed_driver(s, tenant_id=tenant, name="Bob")

    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    clip_alice = await _seed_clip(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        started_at=base,
        driver_id=driver_a.id,
    )
    clip_bob = await _seed_clip(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        started_at=base,
        driver_id=driver_b.id,
    )
    clip_unassigned = await _seed_clip(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        started_at=base,
        driver_id=None,
    )

    e_alice_1 = await _seed_event(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        occurred_at=base,
        clip_id=clip_alice.id,
    )
    e_alice_2 = await _seed_event(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        occurred_at=base + timedelta(minutes=1),
        clip_id=clip_alice.id,
    )
    e_bob = await _seed_event(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        occurred_at=base + timedelta(minutes=2),
        clip_id=clip_bob.id,
    )
    e_unassigned = await _seed_event(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        occurred_at=base + timedelta(minutes=3),
        clip_id=clip_unassigned.id,
    )
    e_no_clip = await _seed_event(
        s,
        tenant_id=tenant,
        truck_id=truck.id,
        occurred_at=base + timedelta(minutes=4),
        clip_id=None,
    )

    principal = _principal(tenant_id=tenant)
    resp = await client.get(
        "/events",
        params={"driver_id": str(driver_a.id)},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    body = EventListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    assert ids == {e_alice_1.id, e_alice_2.id}
    assert e_bob.id not in ids
    assert e_unassigned.id not in ids
    assert e_no_clip.id not in ids


@pytest.mark.asyncio
async def test_list_events_filter_by_driver_id_tenant_scoped(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """A driver id from another tenant must yield zero events.

    Defence-in-depth: even if a caller could enumerate driver IDs from
    another tenant, the events query is tenant-scoped on both the
    ``events`` and the joined ``clips`` rows, so the result set is empty.
    """
    client, s = http_client_with_session

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _seed_tenant(s, tenant_a)
    await _seed_tenant(s, tenant_b)

    truck_a = await _seed_truck(s, tenant_id=tenant_a, label="A-1")
    driver_a = await _seed_driver(s, tenant_id=tenant_a, name="Alice")

    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    clip_a = await _seed_clip(
        s,
        tenant_id=tenant_a,
        truck_id=truck_a.id,
        started_at=base,
        driver_id=driver_a.id,
    )
    await _seed_event(
        s,
        tenant_id=tenant_a,
        truck_id=truck_a.id,
        occurred_at=base,
        clip_id=clip_a.id,
    )

    principal_b = _principal(tenant_id=tenant_b)
    resp = await client.get(
        "/events",
        params={"driver_id": str(driver_a.id)},
        headers=_dev_headers(principal_b),
    )
    assert resp.status_code == 200, resp.text
    body = EventListResponse.model_validate(resp.json())
    assert body.items == []


@pytest.mark.asyncio
async def test_trucks_events_filters_to_that_truck(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """The convenience endpoint must narrow results to the URL-path truck."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck1 = await _seed_truck(s, tenant_id=tenant, label="T-1")
    truck2 = await _seed_truck(s, tenant_id=tenant, label="T-2")

    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    e1 = await _seed_event(s, tenant_id=tenant, truck_id=truck1.id, occurred_at=base)
    e2 = await _seed_event(
        s,
        tenant_id=tenant,
        truck_id=truck2.id,
        occurred_at=base + timedelta(minutes=1),
    )

    principal = _principal(tenant_id=tenant)
    resp = await client.get(
        f"/trucks/{truck1.id}/events", headers=_dev_headers(principal)
    )
    assert resp.status_code == 200, resp.text
    body = EventListResponse.model_validate(resp.json())
    ids = {row.id for row in body.items}
    assert e1.id in ids
    assert e2.id not in ids


# ---------------------------------------------------------------------------
# Tests — POST /events/{id}/triage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_triage_writes_audit_entry_with_label_and_note(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """``coaching_note`` triage must append the expected audit row."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-tri")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    event = await _seed_event(
        s, tenant_id=tenant, truck_id=truck.id, occurred_at=base
    )

    principal = _principal(tenant_id=tenant)
    resp = await client.post(
        f"/events/{event.id}/triage",
        json={"label": "coaching_note", "note": "reviewed"},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text
    returned = EventRow.model_validate(resp.json())
    assert returned.id == event.id
    # The event row is NOT mutated by triage — type/severity unchanged.
    assert returned.type == event.type
    assert returned.severity == event.severity

    audit_rows = (
        await s.execute(
            select(AuditLog).where(
                AuditLog.target_id == event.id,
                AuditLog.action == "event.triage",
            )
        )
    ).scalars().all()
    assert len(audit_rows) == 1
    audit = audit_rows[0]
    assert audit.tenant_id == tenant
    assert audit.actor_user_id == principal.user_id
    assert audit.target_type == "event"
    assert audit.payload == {"label": "coaching_note", "note": "reviewed"}


@pytest.mark.asyncio
async def test_triage_open_case_writes_audit_does_not_create_case(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """``label=open_case`` records audit only — no case row is created in T7."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    truck = await _seed_truck(s, tenant_id=tenant, label="T-oc")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    event = await _seed_event(
        s, tenant_id=tenant, truck_id=truck.id, occurred_at=base
    )

    cases_before = (
        await s.execute(select(Case).where(Case.tenant_id == tenant))
    ).scalars().all()
    assert cases_before == []

    principal = _principal(tenant_id=tenant)
    resp = await client.post(
        f"/events/{event.id}/triage",
        json={"label": "open_case"},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 200, resp.text

    # Audit row written.
    audit_rows = (
        await s.execute(
            select(AuditLog).where(
                AuditLog.target_id == event.id,
                AuditLog.action == "event.triage",
            )
        )
    ).scalars().all()
    assert len(audit_rows) == 1
    assert audit_rows[0].payload == {"label": "open_case", "note": None}

    # ...but NO case row.
    cases_after = (
        await s.execute(select(Case).where(Case.tenant_id == tenant))
    ).scalars().all()
    assert cases_after == []


@pytest.mark.asyncio
async def test_triage_unknown_event_returns_404(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)

    principal = _principal(tenant_id=tenant)
    bogus = uuid.uuid4()
    resp = await client.post(
        f"/events/{bogus}/triage",
        json={"label": "false_positive"},
        headers=_dev_headers(principal),
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_triage_cross_tenant_returns_404(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Triage on another tenant's event must return 404 — never 403 — and audit nothing."""
    client, s = http_client_with_session

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _seed_tenant(s, tenant_a)
    await _seed_tenant(s, tenant_b)

    truck_a = await _seed_truck(s, tenant_id=tenant_a, label="A-1")
    base = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    event_a = await _seed_event(
        s, tenant_id=tenant_a, truck_id=truck_a.id, occurred_at=base
    )

    principal_b = _principal(tenant_id=tenant_b)
    resp = await client.post(
        f"/events/{event_a.id}/triage",
        json={"label": "false_positive"},
        headers=_dev_headers(principal_b),
    )
    assert resp.status_code == 404, resp.text
    detail = resp.json().get("detail", "")
    assert "forbid" not in str(detail).lower()

    # Nothing should have been audited for the cross-tenant attempt.
    audit_rows = (
        await s.execute(
            select(AuditLog).where(AuditLog.target_id == event_a.id)
        )
    ).scalars().all()
    assert audit_rows == []
