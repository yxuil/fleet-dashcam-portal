"""Tests for ``app.seed`` — the dev-data CLI.

These tests exercise the real database since the seed's whole job is to
land rows in Postgres. We use ``--no-upload-samples`` everywhere so the
suite doesn't depend on MinIO.

Each test resets via ``run_seed(reset=True, ...)`` rather than rolling
back a transaction, because the seed itself opens its own session and
commits. That means tests in this file *write* to the dev DB — the
final ``--reset`` at the end of each test leaves a known clean-ish state.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import seed as seed_module
from app.config import settings
from app.models.clip import Clip
from app.models.driver import Driver
from app.models.event import Event
from app.models.tenant import Tenant
from app.models.truck import Truck
from app.models.user import User
from app.seed import (
    TENANT_SPECS,
    TOTAL_CLIPS,
    TOTAL_EVENTS,
    run_seed,
)

# ---------------------------------------------------------------------------
# DB reachability gate — mirrors the other DB-backed test modules.
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


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Read-only session for asserting on seeded rows.

    A dedicated engine is created per test so the session is bound to the
    pytest-asyncio test loop (the shared ``app.db`` engine binds to
    whichever loop touched it first, which causes "different loop" errors
    here). Unlike other tests we don't roll back at teardown — the seed
    has already committed, so rollback wouldn't undo anything. We rely on
    the per-test ``run_seed(reset=True, ...)`` for cleanliness.
    """
    if not _DB_AVAILABLE:
        pytest.skip(_SKIP_REASON)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as s:
            yield s
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_creates_expected_counts(
    session: AsyncSession,
) -> None:
    """One seed run produces the documented number of rows per table.

    Counts (1-tenant demo seed):
      tenants = 1   (Acme)
      users   = 2   (admin + viewer)
      trucks  = 2
      drivers = 2
      clips   = TOTAL_CLIPS
      events  = TOTAL_EVENTS

    Also asserts tenant-isolation invariants: every row's tenant_id is
    the seeded tenant, and trucks owning the clip/event match the
    clip/event's tenant_id.
    """
    summary = await run_seed(reset=True, upload_samples=False)

    expected_tenants = len(TENANT_SPECS)
    expected_users = expected_tenants * 2
    expected_trucks = sum(len(s.truck_labels) for s in TENANT_SPECS)
    expected_drivers = sum(len(s.driver_names) for s in TENANT_SPECS)

    assert summary.tenants == expected_tenants
    assert summary.users == expected_users
    assert summary.trucks == expected_trucks
    assert summary.drivers == expected_drivers
    assert summary.clips == TOTAL_CLIPS
    assert summary.events == TOTAL_EVENTS

    # Verify via the DB, not just the in-memory summary.
    assert (
        await session.execute(select(func.count()).select_from(Tenant))
    ).scalar_one() == expected_tenants
    assert (
        await session.execute(select(func.count()).select_from(User))
    ).scalar_one() == expected_users
    assert (
        await session.execute(select(func.count()).select_from(Truck))
    ).scalar_one() == expected_trucks
    assert (
        await session.execute(select(func.count()).select_from(Driver))
    ).scalar_one() == expected_drivers
    assert (
        await session.execute(select(func.count()).select_from(Clip))
    ).scalar_one() == TOTAL_CLIPS
    assert (
        await session.execute(select(func.count()).select_from(Event))
    ).scalar_one() == TOTAL_EVENTS

    seeded_tenant_ids = {spec.tenant_id for spec in TENANT_SPECS}

    # Tenant isolation: every clip's tenant_id is a seeded one AND its
    # truck is owned by the same tenant.
    rows = (
        await session.execute(
            select(Clip.tenant_id, Truck.tenant_id)
            .join(Truck, Truck.id == Clip.truck_id)
        )
    ).all()
    for clip_tid, truck_tid in rows:
        assert clip_tid in seeded_tenant_ids
        assert clip_tid == truck_tid

    # Same invariant for events.
    rows = (
        await session.execute(
            select(Event.tenant_id, Truck.tenant_id)
            .join(Truck, Truck.id == Event.truck_id)
        )
    ).all()
    for ev_tid, truck_tid in rows:
        assert ev_tid in seeded_tenant_ids
        assert ev_tid == truck_tid


@pytest.mark.asyncio
async def test_seed_creates_symlinks_in_local_mode_when_samples_present(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """In local mode with samples present, seed creates one symlink per clip.

    We point the seed at a fake samples dir + tmp STORAGE_ROOT so the test
    is hermetic — no real ~840 MB MP4s are read, no production storage
    tree is touched.
    """
    # Fake samples dir with three tiny placeholder MP4s.
    fake_samples = tmp_path / "samples"
    fake_samples.mkdir()
    sample_paths = []
    for i in range(3):
        p = fake_samples / f"clip-{i}.mp4"
        p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes([i]))  # tiny stub
        sample_paths.append(p)

    storage_root = tmp_path / "var" / "clips"

    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_root", storage_root)
    # Redirect the seed's samples lookup to our hermetic dir.
    monkeypatch.setattr(seed_module, "_samples_dir", lambda: fake_samples)

    summary = await run_seed(reset=True, upload_samples=True)

    # Every clip should have a symlink at STORAGE_ROOT/<storage_key>.
    assert summary.clips == TOTAL_CLIPS
    assert summary.links == TOTAL_CLIPS
    assert summary.uploaded_samples == 0  # local mode does not upload

    # Spot-check disk: every seeded clip's symlink resolves to one of the
    # sample MP4s.
    clips = (await session.execute(select(Clip))).scalars().all()
    sample_set = {p.resolve() for p in sample_paths}
    for clip in clips:
        link_path = storage_root / clip.storage_key
        assert link_path.is_symlink(), f"missing symlink for {clip.storage_key}"
        assert link_path.resolve() in sample_set


@pytest.mark.asyncio
async def test_seed_is_idempotent_with_reset(session: AsyncSession) -> None:
    """Running seed twice with --reset leaves the same totals (and same ids).

    Tenant ids are derived via ``uuid5`` so they're stable across runs;
    user/truck/driver ids likewise. Clip and event ids use ``uuid4`` so
    they differ, but counts and tenant_ids must match exactly.
    """
    first = await run_seed(reset=True, upload_samples=False)
    second = await run_seed(reset=True, upload_samples=False)

    assert first.tenants == second.tenants
    assert first.users == second.users
    assert first.trucks == second.trucks
    assert first.drivers == second.drivers
    assert first.clips == second.clips
    assert first.events == second.events

    # The tenant rows themselves should have the documented stable ids.
    db_tenant_ids = {
        row[0]
        for row in (await session.execute(select(Tenant.id))).all()
    }
    expected = {spec.tenant_id for spec in TENANT_SPECS}
    assert db_tenant_ids == expected
