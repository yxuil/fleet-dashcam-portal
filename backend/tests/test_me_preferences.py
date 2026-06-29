"""Tests for ``GET /me/preferences`` and ``PATCH /me/preferences``.

The endpoints back the per-user state used by Fleet Cam (truck row
ordering) and forward-compat keys for other client-only state. The
PATCH handler **upserts** a ``users`` row on first write so the dev
principal — which doesn't necessarily have a row in ``users`` yet —
still has somewhere to stash prefs.
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
from app.models.audit import AuditLog
from app.models.tenant import Tenant
from app.models.user import User

# ---------------------------------------------------------------------------
# DB reachability gate — same pattern as test_trucks.py / test_clips.py.
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


def _principal(
    *,
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


async def _seed_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    tenant = Tenant(id=tenant_id, name=f"Tenant {tenant_id}")
    session.add(tenant)
    await session.flush()
    return tenant


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_preferences_returns_empty_dict_when_user_missing(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """A principal with no users row sees ``{}`` — no auto-create on read."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)

    principal = _principal(tenant_id=tenant)
    resp = await client.get("/me/preferences", headers=_dev_headers(principal))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {}

    # And no row was created.
    found = (
        await s.execute(select(User).where(User.id == principal.user_id))
    ).scalar_one_or_none()
    assert found is None


@pytest.mark.asyncio
async def test_patch_preferences_upserts_user_row(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """A first PATCH for an unknown user_id inserts the users row."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)

    principal = _principal(tenant_id=tenant)
    truck_order = [str(uuid.uuid4()), str(uuid.uuid4())]
    resp = await client.patch(
        "/me/preferences",
        headers=_dev_headers(principal),
        json={"truck_order": truck_order},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["truck_order"] == truck_order

    found = (
        await s.execute(select(User).where(User.id == principal.user_id))
    ).scalar_one_or_none()
    assert found is not None
    assert found.tenant_id == principal.tenant_id
    assert found.preferences.get("truck_order") == truck_order


@pytest.mark.asyncio
async def test_patch_preferences_merges_existing_keys(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """PATCH does a shallow merge: unspecified keys survive, matching keys overwrite."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)

    principal = _principal(tenant_id=tenant)

    # First PATCH establishes baseline.
    resp = await client.patch(
        "/me/preferences",
        headers=_dev_headers(principal),
        json={"a": 1, "b": 2},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"a": 1, "b": 2}

    # Second PATCH overwrites ``a`` and adds ``c``; ``b`` survives untouched.
    resp = await client.patch(
        "/me/preferences",
        headers=_dev_headers(principal),
        json={"a": 9, "c": 3},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"a": 9, "b": 2, "c": 3}

    # GET reflects the merged state.
    resp = await client.get("/me/preferences", headers=_dev_headers(principal))
    assert resp.json() == {"a": 9, "b": 2, "c": 3}


@pytest.mark.asyncio
async def test_patch_preferences_writes_audit_row(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Each PATCH appends a ``user.preferences_updated`` audit row."""
    client, s = http_client_with_session

    tenant = uuid.uuid4()
    await _seed_tenant(s, tenant)
    principal = _principal(tenant_id=tenant)

    resp = await client.patch(
        "/me/preferences",
        headers=_dev_headers(principal),
        json={"truck_order": [str(uuid.uuid4())]},
    )
    assert resp.status_code == 200, resp.text

    rows = (
        await s.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == principal.tenant_id,
                AuditLog.action == "user.preferences_updated",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    audit_row = rows[0]
    assert audit_row.actor_user_id == principal.user_id
    assert audit_row.target_type == "user"
    assert audit_row.target_id == principal.user_id
    assert audit_row.payload == {"changed_keys": ["truck_order"]}


@pytest.mark.asyncio
async def test_get_preferences_tenant_isolated(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """A reused user_id under a different tenant_id sees its own (empty) prefs."""
    client, s = http_client_with_session

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _seed_tenant(s, tenant_a)
    await _seed_tenant(s, tenant_b)

    shared_uid = uuid.uuid4()
    principal_a = _principal(tenant_id=tenant_a, user_id=shared_uid)
    principal_b = _principal(tenant_id=tenant_b, user_id=shared_uid)

    # Write prefs under tenant A.
    resp = await client.patch(
        "/me/preferences",
        headers=_dev_headers(principal_a),
        json={"flag": "from-tenant-a"},
    )
    assert resp.status_code == 200, resp.text

    # Tenant B sees empty prefs even though the user_id matches.
    resp = await client.get(
        "/me/preferences", headers=_dev_headers(principal_b)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {}
