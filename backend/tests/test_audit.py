"""Tests for ``app.audit`` and ``GET /audit``.

The DB-backed tests follow the same pattern as ``test_schema.py``: probe
Postgres once at import time and skip cleanly if it isn't reachable, so a
fresh checkout's CI run on a box without the dev compose still goes green.

Each test gets a fresh session whose transaction is rolled back at
teardown, so audit rows written by one test never leak into another.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import audit as audit_module
from app.audit import (
    AuditEntry,
    AuditListResponse,
    decode_cursor,
    encode_cursor,
    record,
)
from app.auth import Principal
from app.config import settings
from app.db import get_session
from app.main import app
from app.models.audit import AuditLog
from app.routers.audit import MAX_AUDIT_LIMIT

# ---------------------------------------------------------------------------
# DB reachability gate (mirrors test_schema.py)
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
async def session() -> AsyncIterator[AsyncSession]:
    """Per-test session that rolls back at teardown."""
    if not _DB_AVAILABLE:
        pytest.skip(_SKIP_REASON)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as s:
        try:
            yield s
        finally:
            await s.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def http_client_with_session() -> AsyncIterator[tuple[httpx.AsyncClient, AsyncSession]]:
    """An HTTPX client wired to the FastAPI app whose ``get_session`` dep
    yields the *same* session we hand back to the test.

    Why share the session? It lets a test write rows through ``record(...)``
    *and* read them back through ``GET /audit`` inside one transaction,
    which is then rolled back at teardown — no DB pollution.
    """
    if not _DB_AVAILABLE:
        pytest.skip(_SKIP_REASON)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
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
            await s.rollback()
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
    """Force the FastAPI dep into dev mode so X-Dev-* headers authenticate.

    This is set per-test via ``app.dependency_overrides`` so it doesn't
    leak across tests. Default app config already uses ``app_env="dev"``,
    but being explicit makes the test robust to .env changes.
    """
    from app.auth import get_settings

    dev_cfg = settings.model_copy(update={"app_env": "dev"})
    app.dependency_overrides[get_settings] = lambda: dev_cfg
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_settings, None)


# ---------------------------------------------------------------------------
# Writer tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_writes_row_with_principal_fields_and_payload(
    session: AsyncSession,
) -> None:
    """`record()` must populate tenant/actor/action/target/payload faithfully."""
    p = _principal()
    target_id = uuid.uuid4()

    await record(
        session,
        principal=p,
        action="case.create",
        target_type="case",
        target_id=target_id,
        payload={"number": "C-2026-000001", "note": "n/a"},
    )

    # Same session, post-flush — read back via a SELECT to prove the row
    # made it into the database (not just into a Python identity map).
    result = await session.execute(
        select(AuditLog).where(
            AuditLog.tenant_id == p.tenant_id,
            AuditLog.target_id == target_id,
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.tenant_id == p.tenant_id
    assert row.actor_user_id == p.user_id
    assert row.action == "case.create"
    assert row.target_type == "case"
    assert row.target_id == target_id
    assert row.payload == {"number": "C-2026-000001", "note": "n/a"}
    assert row.occurred_at is not None
    assert row.id is not None


@pytest.mark.asyncio
async def test_record_defaults_payload_to_empty_dict(session: AsyncSession) -> None:
    p = _principal()
    target_id = uuid.uuid4()

    await record(
        session,
        principal=p,
        action="clip.signed",
        target_type="clip",
        target_id=target_id,
        payload=None,
    )

    result = await session.execute(
        select(AuditLog).where(AuditLog.target_id == target_id)
    )
    row = result.scalar_one()
    assert row.payload == {}


@pytest.mark.asyncio
async def test_record_does_not_commit(session: AsyncSession) -> None:
    """Belt-and-suspenders: a transaction-level rollback must remove the row,
    proving ``record()`` did not commit on its own."""
    p = _principal()
    target_id = uuid.uuid4()

    await record(
        session,
        principal=p,
        action="case.create",
        target_type="case",
        target_id=target_id,
    )

    # Row is visible inside the txn.
    pre = await session.execute(select(AuditLog).where(AuditLog.target_id == target_id))
    assert pre.scalar_one_or_none() is not None

    await session.rollback()

    # And gone after rollback — i.e. record() didn't sneak in a commit.
    post = await session.execute(select(AuditLog).where(AuditLog.target_id == target_id))
    assert post.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# GET /audit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_audit_lists_recent_entries_for_tenant_only(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    client, s = http_client_with_session

    # Two distinct tenants. Caller is tenant A.
    p_a = _principal()
    p_b = _principal()
    assert p_a.tenant_id != p_b.tenant_id

    a_target = uuid.uuid4()
    b_target = uuid.uuid4()

    await record(s, principal=p_a, action="case.create", target_type="case", target_id=a_target)
    await record(s, principal=p_b, action="case.create", target_type="case", target_id=b_target)

    resp = await client.get("/audit", headers=_dev_headers(p_a))
    assert resp.status_code == 200, resp.text
    body = AuditListResponse.model_validate(resp.json())
    target_ids = {item.target_id for item in body.items}
    tenants = {item.tenant_id for item in body.items}
    assert a_target in target_ids
    assert b_target not in target_ids
    assert tenants == {p_a.tenant_id}


@pytest.mark.asyncio
async def test_get_audit_filters_by_target_type_and_id(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    client, s = http_client_with_session
    p = _principal()

    case_target = uuid.uuid4()
    clip_target_1 = uuid.uuid4()
    clip_target_2 = uuid.uuid4()
    other_case = uuid.uuid4()

    # 5 entries: one case-of-interest, one other case, two clips with one
    # specific clip id of interest, plus a tenant-wide row with no target_id.
    await record(s, principal=p, action="case.create", target_type="case", target_id=case_target)
    await record(s, principal=p, action="case.create", target_type="case", target_id=other_case)
    await record(s, principal=p, action="clip.signed", target_type="clip", target_id=clip_target_1)
    await record(s, principal=p, action="clip.signed", target_type="clip", target_id=clip_target_2)
    await record(s, principal=p, action="tenant.touch", target_type="tenant", target_id=None)

    # Filter by target_type only.
    resp = await client.get(
        "/audit",
        params={"target_type": "case"},
        headers=_dev_headers(p),
    )
    assert resp.status_code == 200
    body = AuditListResponse.model_validate(resp.json())
    assert {i.target_id for i in body.items} == {case_target, other_case}
    assert all(i.target_type == "case" for i in body.items)

    # Filter by both target_type AND target_id narrows to one row.
    resp = await client.get(
        "/audit",
        params={"target_type": "clip", "target_id": str(clip_target_1)},
        headers=_dev_headers(p),
    )
    assert resp.status_code == 200
    body = AuditListResponse.model_validate(resp.json())
    assert len(body.items) == 1
    assert body.items[0].target_id == clip_target_1
    assert body.items[0].target_type == "clip"


@pytest.mark.asyncio
async def test_get_audit_paginates_with_cursor(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    client, s = http_client_with_session
    p = _principal()

    # 25 entries, all tenant A. Each one tagged with the original sequence
    # index in payload so we can assert ordering.
    targets: list[uuid.UUID] = []
    for i in range(25):
        tid = uuid.uuid4()
        targets.append(tid)
        await record(
            s,
            principal=p,
            action="case.create",
            target_type="case",
            target_id=tid,
            payload={"seq": i},
        )

    # Page 1
    resp = await client.get("/audit", params={"limit": 10}, headers=_dev_headers(p))
    assert resp.status_code == 200
    page1 = AuditListResponse.model_validate(resp.json())
    assert len(page1.items) == 10
    assert page1.next_cursor is not None

    # Page 2
    resp = await client.get(
        "/audit",
        params={"limit": 10, "cursor": page1.next_cursor},
        headers=_dev_headers(p),
    )
    assert resp.status_code == 200
    page2 = AuditListResponse.model_validate(resp.json())
    assert len(page2.items) == 10
    assert page2.next_cursor is not None

    # Page 3 (last 5)
    resp = await client.get(
        "/audit",
        params={"limit": 10, "cursor": page2.next_cursor},
        headers=_dev_headers(p),
    )
    assert resp.status_code == 200
    page3 = AuditListResponse.model_validate(resp.json())
    assert len(page3.items) == 5
    assert page3.next_cursor is None

    # Newest-first ordering: payload[seq] should be monotonically
    # decreasing across the concatenated pages. (Sequence index 24 → 0.)
    all_seqs = [i.payload["seq"] for i in (*page1.items, *page2.items, *page3.items)]
    assert all_seqs == sorted(all_seqs, reverse=True)
    assert all_seqs[0] == 24
    assert all_seqs[-1] == 0

    # No row should appear twice across pages.
    seen_ids = [i.id for i in (*page1.items, *page2.items, *page3.items)]
    assert len(seen_ids) == len(set(seen_ids))


@pytest.mark.asyncio
async def test_get_audit_respects_limit_cap(
    http_client_with_session: tuple[httpx.AsyncClient, AsyncSession],
    dev_settings: None,
) -> None:
    """Requesting a giant limit must be silently capped at MAX_AUDIT_LIMIT."""
    client, s = http_client_with_session
    p = _principal()

    # 5 rows is enough — the cap is a behavioral guarantee independent of
    # actual row count. We assert ``items <= MAX``; we don't depend on
    # there being >MAX rows.
    for _ in range(5):
        await record(
            s, principal=p, action="case.create", target_type="case", target_id=uuid.uuid4()
        )

    resp = await client.get("/audit", params={"limit": 1000}, headers=_dev_headers(p))
    assert resp.status_code == 200
    body = AuditListResponse.model_validate(resp.json())
    assert len(body.items) <= MAX_AUDIT_LIMIT


# ---------------------------------------------------------------------------
# Append-only invariant — file-level grep guardrail
# ---------------------------------------------------------------------------


def test_audit_module_has_no_update_or_delete_code() -> None:
    """`app/audit.py` must not contain UPDATE or DELETE code paths.

    This is a defense-in-depth check against accidental future changes
    that introduce mutation. We strip comments and docstrings from the
    source and assert none of the forbidden mutation patterns appear in
    actual executable code.
    """
    import io
    import token
    import tokenize

    src = Path(audit_module.__file__).read_text(encoding="utf-8")

    # Strip comments and string literals so prose like "no update or delete"
    # in the module docstring doesn't trigger the guardrail.
    code_only_parts: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (token.COMMENT, token.STRING, tokenize.NL, tokenize.NEWLINE):
            continue
        code_only_parts.append(tok.string)
    code_only = " ".join(code_only_parts).lower()

    forbidden_substrings = [
        "session.delete",
        "session.execute(delete",
        "session.execute(update",
        ".delete(",  # any .delete( call
        "update(auditlog",
        "delete(auditlog",
        "from sqlalchemy import update",
        "from sqlalchemy import delete",
        ", update,",
        ", delete,",
        " update,",
        " delete,",
    ]
    for needle in forbidden_substrings:
        assert needle not in code_only, (
            f"Forbidden substring {needle!r} found in app/audit.py — audit log "
            f"must be append-only"
        )


# ---------------------------------------------------------------------------
# Cursor helper unit tests (no DB)
# ---------------------------------------------------------------------------


def test_cursor_roundtrip() -> None:
    from datetime import UTC, datetime

    ts = datetime(2026, 6, 29, 12, 34, 56, 123456, tzinfo=UTC)
    enc = encode_cursor(ts, 42)
    dec_ts, dec_id = decode_cursor(enc)
    assert dec_ts == ts
    assert dec_id == 42


def test_cursor_invalid_raises_value_error() -> None:
    with pytest.raises(ValueError):
        decode_cursor("not-a-cursor!!!")


# ---------------------------------------------------------------------------
# Pydantic model sanity
# ---------------------------------------------------------------------------


def test_audit_entry_from_attributes_works_on_orm_row() -> None:
    """``AuditEntry`` must round-trip from an ``AuditLog`` ORM instance."""
    from datetime import UTC, datetime

    row = AuditLog(
        id=7,
        tenant_id=uuid.uuid4(),
        actor_user_id=uuid.uuid4(),
        action="case.create",
        target_type="case",
        target_id=uuid.uuid4(),
        payload={"k": "v"},
        occurred_at=datetime.now(UTC),
    )
    entry = AuditEntry.model_validate(row)
    assert entry.id == 7
    assert entry.action == "case.create"
    assert entry.payload == {"k": "v"}
