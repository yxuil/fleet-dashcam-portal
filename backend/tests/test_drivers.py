"""Tests for ``GET /drivers``.

Test pattern mirrors ``test_clips.py`` / ``test_trucks.py``.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator

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
from app.models.driver import Driver
from app.models.tenant import Tenant

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


async def _seed_driver(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    employee_ref: str | None = None,
) -> Driver:
    driver = Driver(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=name,
        employee_ref=employee_ref,
    )
    session.add(driver)
    await session.flush()
    return driver


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_drivers_tenant_isolated(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Drivers from another tenant must not appear in the caller's list."""
    client, s = http_client_with_session

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _seed_tenant(s, tenant_a)
    await _seed_tenant(s, tenant_b)

    driver_a = await _seed_driver(s, tenant_id=tenant_a, name="Alice")
    driver_b = await _seed_driver(s, tenant_id=tenant_b, name="Bob")

    principal_a = _principal(tenant_id=tenant_a)
    resp = await client.get("/drivers", headers=_dev_headers(principal_a))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    ids = {r["id"] for r in rows}
    assert str(driver_a.id) in ids
    assert str(driver_b.id) not in ids
    assert all(r["tenant_id"] == str(tenant_a) for r in rows)


@pytest.mark.asyncio
async def test_list_drivers_orders_by_name(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Drivers come back alphabetically by name."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    await _seed_driver(s, tenant_id=tenant, name="Charlie")
    await _seed_driver(s, tenant_id=tenant, name="Alice")
    await _seed_driver(s, tenant_id=tenant, name="Bob")

    principal = _principal(tenant_id=tenant)
    resp = await client.get("/drivers", headers=_dev_headers(principal))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    names = [r["name"] for r in rows]
    assert names == ["Alice", "Bob", "Charlie"]


@pytest.mark.asyncio
async def test_list_drivers_includes_employee_ref(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Driver rows include the optional employee_ref field."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    await _seed_driver(
        s, tenant_id=tenant, name="Alice", employee_ref="EMP-001"
    )
    await _seed_driver(s, tenant_id=tenant, name="Bob")

    principal = _principal(tenant_id=tenant)
    resp = await client.get("/drivers", headers=_dev_headers(principal))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    by_name = {r["name"]: r for r in rows}
    assert by_name["Alice"]["employee_ref"] == "EMP-001"
    assert by_name["Bob"]["employee_ref"] is None
