"""Schema round-trip test.

Spins up an async connection to the configured DATABASE_URL, inserts one row
per table with valid foreign-key relationships, reads them back, and cleans
up. Skips automatically if the dev Postgres is not reachable.

Run prerequisites:

    docker compose -f infra/docker-compose.dev.yml up -d postgres
    uv run alembic upgrade head
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import (
    AuditLog,
    Case,
    CaseClip,
    CaseStatus,
    Clip,
    Driver,
    Event,
    EventSeverity,
    EventType,
    Tenant,
    Truck,
    User,
)


def _can_connect(url: str) -> bool:
    """Quick sync probe for DB reachability so we can skip cleanly."""

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


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Yield a session and roll back at the end to leave the DB pristine."""
    if not _DB_AVAILABLE:
        pytest.skip(
            "Dev Postgres not reachable at DATABASE_URL. "
            "Run `docker compose -f infra/docker-compose.dev.yml up -d postgres` "
            "and `uv run alembic upgrade head` first."
        )

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as s:
        try:
            yield s
        finally:
            await s.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_schema_round_trip(session: AsyncSession) -> None:
    """Insert one row per table with valid FK chain; read each back."""
    now = datetime.now(UTC)
    started = now - timedelta(minutes=5)

    tenant = Tenant(name=f"test-tenant-{uuid.uuid4()}")
    session.add(tenant)
    await session.flush()

    user = User(
        tenant_id=tenant.id,
        email=f"user-{uuid.uuid4()}@example.com",
        name="Test User",
        roles=["admin", "reviewer"],
    )
    session.add(user)
    await session.flush()

    truck = Truck(
        tenant_id=tenant.id,
        label=f"truck-{uuid.uuid4()}",
        vin="1HGCM82633A004352",
        dashcam_serial="DC-0001",
        last_seen_at=now,
    )
    session.add(truck)
    await session.flush()

    driver = Driver(
        tenant_id=tenant.id,
        name="Alice Driver",
        employee_ref="EMP-001",
    )
    session.add(driver)
    await session.flush()

    clip = Clip(
        tenant_id=tenant.id,
        truck_id=truck.id,
        driver_id=driver.id,
        started_at=started,
        ended_at=now,
        duration_s=300,
        storage_key=f"clips/{uuid.uuid4()}.mp4",
        sha256="0" * 64,
        dashcam_firmware="1.2.3",
    )
    session.add(clip)
    await session.flush()

    event = Event(
        tenant_id=tenant.id,
        truck_id=truck.id,
        clip_id=clip.id,
        occurred_at=now,
        type=EventType.harsh_brake,
        severity=EventSeverity.high,
        telemetry={"g_force": 1.8, "speed_kph": 72},
        gps_lat=37.7749,
        gps_lng=-122.4194,
    )
    session.add(event)
    await session.flush()

    case = Case(
        tenant_id=tenant.id,
        number=f"C-2026-{uuid.uuid4().hex[:6]}",
        external_ref="EXT-42",
        requester_name="Insurance Co.",
        requester_org="ACME Insurance",
        incident_at=started,
        status=CaseStatus.open,
        assignee_user_id=user.id,
        due_at=now + timedelta(days=7),
        created_by=user.id,
    )
    session.add(case)
    await session.flush()

    case_clip = CaseClip(
        case_id=case.id,
        clip_id=clip.id,
        attached_by=user.id,
        note="Primary evidence",
    )
    session.add(case_clip)
    await session.flush()

    audit = AuditLog(
        tenant_id=tenant.id,
        actor_user_id=user.id,
        action="case.create",
        target_type="case",
        target_id=case.id,
        payload={"number": case.number},
    )
    session.add(audit)
    await session.flush()

    # Read each row back through a fresh query (round-trip).
    assert (await session.get(Tenant, tenant.id)) is not None
    assert (await session.get(User, user.id)) is not None
    assert (await session.get(Truck, truck.id)) is not None
    assert (await session.get(Driver, driver.id)) is not None

    fetched_clip = await session.get(Clip, clip.id)
    assert fetched_clip is not None
    assert fetched_clip.duration_s == 300
    assert fetched_clip.storage_key == clip.storage_key

    fetched_event = await session.get(Event, event.id)
    assert fetched_event is not None
    assert fetched_event.type is EventType.harsh_brake
    assert fetched_event.severity is EventSeverity.high
    assert fetched_event.telemetry == {"g_force": 1.8, "speed_kph": 72}

    fetched_case = await session.get(Case, case.id)
    assert fetched_case is not None
    assert fetched_case.status is CaseStatus.open
    assert fetched_case.number == case.number

    fetched_link = await session.get(CaseClip, (case.id, clip.id))
    assert fetched_link is not None
    assert fetched_link.attached_by == user.id

    fetched_audit = await session.get(AuditLog, audit.id)
    assert fetched_audit is not None
    assert fetched_audit.action == "case.create"
    assert fetched_audit.target_id == case.id

    # Fixture's rollback at teardown removes all of the above — no row leaks
    # across tests.
