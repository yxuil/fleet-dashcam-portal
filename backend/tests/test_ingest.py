"""Tests for ``POST /ingest/clips`` — the forward-compat ingest stub.

Pattern mirrors ``test_clips.py``: probe Postgres at import time and skip
the suite cleanly when unreachable. We use the savepoint-based session
fixture because the ingest handler calls ``session.commit()`` to persist
its clip + audit row; with ``join_transaction_mode="create_savepoint"``
that commit just releases a SAVEPOINT inside an outer transaction we
roll back at teardown, so no rows leak across tests.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
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

from app.config import settings
from app.db import get_session
from app.main import app
from app.models.audit import AuditLog
from app.models.clip import Clip
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
    """HTTPX client wired to the FastAPI app + a rollback-safe shared session.

    The ingest handler commits, so we wrap an outer connection-level
    transaction and ask the session to use SAVEPOINTs; the outer txn is
    rolled back at teardown.
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


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


async def _seed_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    tenant = Tenant(id=tenant_id, name=f"Tenant {tenant_id}")
    session.add(tenant)
    await session.flush()
    return tenant


async def _seed_truck(
    session: AsyncSession, *, tenant_id: uuid.UUID, label: str = "T-1"
) -> Truck:
    truck = Truck(id=uuid.uuid4(), tenant_id=tenant_id, label=label)
    session.add(truck)
    await session.flush()
    return truck


def _body(
    *,
    tenant_id: uuid.UUID,
    truck_id: uuid.UUID,
    storage_key: str | None = None,
    driver_id: uuid.UUID | None = None,
) -> dict[str, object]:
    started = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    ended = started + timedelta(seconds=45)
    return {
        "tenant_id": str(tenant_id),
        "truck_id": str(truck_id),
        "driver_id": str(driver_id) if driver_id else None,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_s": 45,
        "storage_key": storage_key or f"{tenant_id}/2026/06/29/{uuid.uuid4()}.mp4",
        "sha256": "deadbeef" * 8,
        "dashcam_firmware": "1.5.0",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_creates_clip_and_audit_row(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
) -> None:
    """Happy path: clip row + system audit row, ``clip_id`` returned."""
    client, s = http_client_with_session

    tenant_id = uuid.uuid4()
    await _seed_tenant(s, tenant_id)
    truck = await _seed_truck(s, tenant_id=tenant_id)

    body = _body(tenant_id=tenant_id, truck_id=truck.id)
    resp = await client.post("/ingest/clips", json=body)
    assert resp.status_code == 201, resp.text

    clip_id = uuid.UUID(resp.json()["clip_id"])

    # Clip row exists with the values we sent.
    clip = (
        await s.execute(select(Clip).where(Clip.id == clip_id))
    ).scalar_one()
    assert clip.tenant_id == tenant_id
    assert clip.truck_id == truck.id
    assert clip.duration_s == 45
    assert clip.storage_key == body["storage_key"]
    assert clip.sha256 == body["sha256"]
    assert clip.dashcam_firmware == "1.5.0"

    # Audit row: actor is None (system), action is clip.ingested, payload
    # carries truck_id / storage_key / sha256.
    audit = (
        await s.execute(
            select(AuditLog).where(
                AuditLog.target_type == "clip",
                AuditLog.target_id == clip_id,
            )
        )
    ).scalar_one()
    assert audit.actor_user_id is None
    assert audit.action == "clip.ingested"
    assert audit.tenant_id == tenant_id
    assert audit.payload["truck_id"] == str(truck.id)
    assert audit.payload["storage_key"] == body["storage_key"]
    assert audit.payload["sha256"] == body["sha256"]


@pytest.mark.asyncio
async def test_ingest_rejects_storage_key_outside_tenant_prefix(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
) -> None:
    """A storage key under a different tenant's prefix is a 400."""
    client, s = http_client_with_session

    tenant_id = uuid.uuid4()
    other_tenant = uuid.uuid4()
    await _seed_tenant(s, tenant_id)
    truck = await _seed_truck(s, tenant_id=tenant_id)

    bad_key = f"{other_tenant}/2026/06/29/{uuid.uuid4()}.mp4"
    body = _body(tenant_id=tenant_id, truck_id=truck.id, storage_key=bad_key)
    resp = await client.post("/ingest/clips", json=body)
    assert resp.status_code == 400, resp.text
    assert "tenant" in resp.json()["detail"].lower()

    # No clip row was inserted.
    n = (
        await s.execute(
            select(Clip).where(Clip.tenant_id == tenant_id, Clip.storage_key == bad_key)
        )
    ).scalars().all()
    assert n == []


@pytest.mark.asyncio
async def test_ingest_rejects_unknown_truck(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
) -> None:
    """Unknown truck id returns 404 (not 403, to avoid existence probes)."""
    client, s = http_client_with_session

    tenant_id = uuid.uuid4()
    await _seed_tenant(s, tenant_id)

    body = _body(tenant_id=tenant_id, truck_id=uuid.uuid4())
    resp = await client.post("/ingest/clips", json=body)
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "truck not found"


@pytest.mark.asyncio
async def test_ingest_rejects_cross_tenant_truck(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
) -> None:
    """A truck belonging to tenant B must not be ingestable as tenant A."""
    client, s = http_client_with_session

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _seed_tenant(s, tenant_a)
    await _seed_tenant(s, tenant_b)
    truck_b = await _seed_truck(s, tenant_id=tenant_b, label="B-truck")

    # storage_key under tenant_a's prefix would otherwise pass the prefix
    # check, so we're really testing the truck-belongs-to-tenant gate.
    body = _body(
        tenant_id=tenant_a,
        truck_id=truck_b.id,
        storage_key=f"{tenant_a}/2026/06/29/{uuid.uuid4()}.mp4",
    )
    resp = await client.post("/ingest/clips", json=body)
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "truck not found"
