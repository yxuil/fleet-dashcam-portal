"""Cases API — create, list, detail, attach-clip, patch, close.

Endpoints
---------
* ``POST   /cases``                — create
* ``GET    /cases``                — list with status / assignee / q filters
* ``GET    /cases/{id}``           — detail with attached clips + recent audit
* ``POST   /cases/{id}/clips``     — attach a clip (idempotent)
* ``PATCH  /cases/{id}``           — partial update (NOT status=closed)
* ``POST   /cases/{id}/close``     — close with a reason

Number generation
-----------------
Each tenant has its own counter that resets at the start of every calendar
year. Numbers are formatted ``C-YYYY-NNNN`` where ``NNNN`` is the next
4-digit-padded sequence value within that ``(tenant, year)`` bucket.

To keep concurrent ``POST /cases`` from issuing duplicate numbers we
acquire a Postgres advisory lock keyed by ``(tenant_id, year)`` inside
the transaction:

    SELECT pg_advisory_xact_lock(hashtext('<tenant>|<year>'))

``pg_advisory_xact_lock`` is released automatically at COMMIT/ROLLBACK,
so we don't need a matching unlock. The lock is taken *before* the
``SELECT MAX(number) ...`` so two simultaneous transactions for the same
tenant-year serialise on the lock and observe each other's inserts.

If the advisory lock can't be acquired (e.g. running against a non-Postgres
database in some hypothetical test scenario), we still have the
``UNIQUE(tenant_id, number)`` constraint as a backstop — a duplicate
number would surface as an ``IntegrityError`` and we'd retry. For T8 we
keep the implementation simple and rely on the lock.

Tenant isolation
----------------
All reads and mutations are filtered by ``principal.tenant_id``. A case
or clip from another tenant returns ``404 not found`` rather than ``403``
so the caller can't probe for cross-tenant existence.

PATCH-status decision
---------------------
``CasePatch.status`` is typed ``Literal["open", "under_review", "approved"]``
— ``"closed"`` is intentionally not part of the type, so FastAPI returns
422 for the bad value before our handler runs. Closing a case must go
through ``POST /cases/{id}/close`` so the audit row carries a reason.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.audit import AuditEntry
from app.audit import record as audit_record
from app.auth import Principal, current_user
from app.db import get_session
from app.models.audit import AuditLog
from app.models.case import Case, CaseStatus
from app.models.case_clip import CaseClip
from app.models.clip import Clip
from app.schemas.case import (
    AttachClipRequest,
    AttachedClip,
    CaseCreate,
    CaseDetail,
    CaseListResponse,
    CasePatch,
    CaseRow,
    CloseCaseRequest,
)

router = APIRouter(tags=["cases"])

#: Hard upper bound on page size, mirroring the other list endpoints.
MAX_CASES_LIMIT: int = 200

#: Default page size when the client doesn't specify one.
DEFAULT_CASES_LIMIT: int = 50

#: How many audit entries to include in the detail response.
RECENT_AUDIT_LIMIT: int = 50

#: Matches a generated case number, e.g. ``C-2026-0042``.
_NUMBER_RE = re.compile(r"^C-(\d{4})-(\d{4})$")


# ---------------------------------------------------------------------------
# Cursor helpers — opaque base64-url JSON of ``(created_at, id)``.
# Mirrors ``app.routers.clips`` / ``app.routers.events``.
# ---------------------------------------------------------------------------


def _encode_case_cursor(created_at: datetime, case_id: UUID) -> str:
    raw = json.dumps({"c": created_at.isoformat(), "i": str(case_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_case_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        pad = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode((cursor + pad).encode("ascii"))
        obj = json.loads(raw.decode("utf-8"))
        return datetime.fromisoformat(obj["c"]), UUID(obj["i"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid cursor") from exc


# ---------------------------------------------------------------------------
# Number generation
# ---------------------------------------------------------------------------


async def _next_case_number(session: AsyncSession, *, tenant_id: UUID, year: int) -> str:
    """Allocate the next ``C-YYYY-NNNN`` number for ``(tenant_id, year)``.

    Holds a Postgres transaction-scoped advisory lock for the duration of
    the SELECT-then-insert window so concurrent ``POST /cases`` calls
    against the same tenant in the same year serialise here.
    """
    # ``hashtext`` is a Postgres-only function returning a 32-bit signed
    # int — exactly what pg_advisory_xact_lock(int) wants.
    lock_key = f"{tenant_id}|{year}"
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))").bindparams(k=lock_key))

    prefix = f"C-{year:04d}-"
    # Find the largest existing NNNN under this prefix for this tenant.
    # We sort on the full ``number`` string — safe because all candidates
    # share the same fixed-width prefix and a fixed 4-digit suffix.
    stmt = (
        select(Case.number)
        .where(
            Case.tenant_id == tenant_id,
            Case.number.like(f"{prefix}%"),
        )
        .order_by(Case.number.desc())
        .limit(1)
    )
    latest = (await session.execute(stmt)).scalar_one_or_none()

    next_seq = 1
    if latest is not None:
        match = _NUMBER_RE.match(latest)
        if match is not None:
            next_seq = int(match.group(2)) + 1
    return f"{prefix}{next_seq:04d}"


# ---------------------------------------------------------------------------
# Lookup helper — tenant-scoped 404
# ---------------------------------------------------------------------------


async def _load_case_or_404(session: AsyncSession, *, case_id: UUID, tenant_id: UUID) -> Case:
    """Return the case row scoped to ``tenant_id`` or raise ``404``."""
    stmt = select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id)
    case = (await session.execute(stmt)).scalar_one_or_none()
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="not found",
        )
    return case


# ---------------------------------------------------------------------------
# Routes — create
# ---------------------------------------------------------------------------


@router.post("/cases", response_model=CaseDetail, status_code=status.HTTP_201_CREATED)
async def create_case(
    body: CaseCreate,
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseDetail:
    """Create a new case for the caller's tenant.

    The number is generated server-side as ``C-YYYY-NNNN`` where ``YYYY``
    is the current UTC year and ``NNNN`` is the next per-tenant sequence
    that resets each year. An advisory lock keyed by ``(tenant, year)``
    prevents two concurrent creates from picking the same number.
    """
    year = datetime.now(UTC).year
    number = await _next_case_number(session, tenant_id=principal.tenant_id, year=year)

    case = Case(
        tenant_id=principal.tenant_id,
        number=number,
        external_ref=body.external_ref,
        requester_name=body.requester_name,
        requester_org=body.requester_org,
        incident_at=body.incident_at,
        status=CaseStatus.open,
        assignee_user_id=body.assignee_user_id,
        due_at=body.due_at,
        created_by=principal.user_id,
    )
    session.add(case)
    await session.flush()

    await audit_record(
        session,
        principal=principal,
        action="case.created",
        target_type="case",
        target_id=case.id,
        payload={"number": case.number},
    )
    await session.commit()
    # Re-fetch with empty clips list; this is a fresh case so no audit
    # rows other than the create itself exist yet.
    return await _build_detail(session, case=case, tenant_id=principal.tenant_id)


# ---------------------------------------------------------------------------
# Routes — list
# ---------------------------------------------------------------------------


@router.get("/cases", response_model=CaseListResponse)
async def list_cases(
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    status_: Annotated[list[CaseStatus] | None, Query(alias="status")] = None,
    assignee_user_id: Annotated[UUID | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = DEFAULT_CASES_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> CaseListResponse:
    """Return a page of cases for the caller's tenant.

    ``status`` is repeatable (``?status=open&status=under_review``).
    ``q`` is a case-insensitive substring match across
    ``number / external_ref / requester_name / requester_org``.
    """
    capped_limit = min(limit, MAX_CASES_LIMIT)

    stmt = select(Case).where(Case.tenant_id == principal.tenant_id)

    if status_:
        stmt = stmt.where(Case.status.in_(status_))
    if assignee_user_id is not None:
        stmt = stmt.where(Case.assignee_user_id == assignee_user_id)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                Case.number.ilike(pattern),
                Case.external_ref.ilike(pattern),
                Case.requester_name.ilike(pattern),
                Case.requester_org.ilike(pattern),
            )
        )

    if cursor is not None:
        try:
            cursor_ts, cursor_id = _decode_case_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid cursor",
            ) from exc
        stmt = stmt.where(
            or_(
                Case.created_at < cursor_ts,
                and_(Case.created_at == cursor_ts, Case.id < cursor_id),
            )
        )

    stmt = stmt.order_by(Case.created_at.desc(), Case.id.desc()).limit(capped_limit + 1)

    result = await session.execute(stmt)
    rows = list(result.scalars().all())

    has_more = len(rows) > capped_limit
    page = rows[:capped_limit]

    next_cursor: str | None = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_case_cursor(last.created_at, last.id)

    return CaseListResponse(
        items=[CaseRow.model_validate(r) for r in page],
        next_cursor=next_cursor,
    )


# ---------------------------------------------------------------------------
# Routes — detail
# ---------------------------------------------------------------------------


async def _build_detail(session: AsyncSession, *, case: Case, tenant_id: UUID) -> CaseDetail:
    """Assemble a :class:`CaseDetail` for ``case``.

    Runs two extra queries: one ``selectinload``-style fetch of the
    attached clips (with truck), and one query for the last
    :data:`RECENT_AUDIT_LIMIT` audit rows targeting this case.
    """
    # Attached clips, ordered oldest-first so the UI sees attachments in
    # the order they happened.
    clip_stmt = (
        select(CaseClip)
        .options(selectinload(CaseClip.clip).selectinload(Clip.truck))
        .where(CaseClip.case_id == case.id)
        .order_by(CaseClip.attached_at.asc())
    )
    case_clips = list((await session.execute(clip_stmt)).scalars().all())

    audit_stmt = (
        select(AuditLog)
        .where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.target_type == "case",
            AuditLog.target_id == case.id,
        )
        .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
        .limit(RECENT_AUDIT_LIMIT)
    )
    audit_rows = list((await session.execute(audit_stmt)).scalars().all())

    return CaseDetail(
        id=case.id,
        tenant_id=case.tenant_id,
        number=case.number,
        external_ref=case.external_ref,
        requester_name=case.requester_name,
        requester_org=case.requester_org,
        incident_at=case.incident_at,
        status=case.status,
        assignee_user_id=case.assignee_user_id,
        due_at=case.due_at,
        created_by=case.created_by,
        created_at=case.created_at,
        clips=[AttachedClip.model_validate(cc) for cc in case_clips],
        recent_audit=[AuditEntry.model_validate(a) for a in audit_rows],
    )


@router.get("/cases/{case_id}", response_model=CaseDetail)
async def get_case(
    case_id: UUID,
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseDetail:
    """Return case detail, attached clips, and the most recent audit entries."""
    case = await _load_case_or_404(session, case_id=case_id, tenant_id=principal.tenant_id)
    return await _build_detail(session, case=case, tenant_id=principal.tenant_id)


# ---------------------------------------------------------------------------
# Routes — attach clip
# ---------------------------------------------------------------------------


@router.post(
    "/cases/{case_id}/clips",
    response_model=CaseDetail,
)
async def attach_clip(
    case_id: UUID,
    body: AttachClipRequest,
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseDetail:
    """Attach a clip to a case. Idempotent on ``(case, clip)``.

    Both the case and the clip must belong to the caller's tenant; a
    cross-tenant attempt on either returns ``404 not found``. Re-attaching
    the same clip is a no-op — the existing row is left as-is and we
    return the case detail without raising.
    """
    case = await _load_case_or_404(session, case_id=case_id, tenant_id=principal.tenant_id)

    clip_stmt = select(Clip).where(Clip.id == body.clip_id, Clip.tenant_id == principal.tenant_id)
    clip = (await session.execute(clip_stmt)).scalar_one_or_none()
    if clip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="not found",
        )

    existing_stmt = select(CaseClip).where(CaseClip.case_id == case.id, CaseClip.clip_id == clip.id)
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()

    if existing is None:
        session.add(
            CaseClip(
                case_id=case.id,
                clip_id=clip.id,
                attached_by=principal.user_id,
                note=body.note,
            )
        )
        await session.flush()
        await audit_record(
            session,
            principal=principal,
            action="case.clip_attached",
            target_type="case",
            target_id=case.id,
            payload={"clip_id": str(clip.id), "note": body.note},
        )
        await session.commit()

    return await _build_detail(session, case=case, tenant_id=principal.tenant_id)


# ---------------------------------------------------------------------------
# Routes — patch
# ---------------------------------------------------------------------------


@router.patch("/cases/{case_id}", response_model=CaseDetail)
async def patch_case(
    case_id: UUID,
    body: CasePatch,
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseDetail:
    """Partial update of supplied fields, with one audit row listing all changes.

    ``status="closed"`` is rejected at the schema layer (the ``status``
    field is typed without ``"closed"``). Use ``POST /cases/{id}/close``.
    """
    case = await _load_case_or_404(session, case_id=case_id, tenant_id=principal.tenant_id)

    changes = body.model_dump(exclude_unset=True)
    if not changes:
        # Nothing to do, no audit row written.
        return await _build_detail(session, case=case, tenant_id=principal.tenant_id)

    audit_payload: dict[str, Any] = {}
    for field, new_value in changes.items():
        setattr(case, field, new_value)
        # JSON-friendly representation for the audit row.
        if isinstance(new_value, datetime):
            audit_payload[field] = new_value.isoformat()
        elif isinstance(new_value, UUID):
            audit_payload[field] = str(new_value)
        else:
            audit_payload[field] = new_value
    await session.flush()

    await audit_record(
        session,
        principal=principal,
        action="case.updated",
        target_type="case",
        target_id=case.id,
        payload={"changes": audit_payload},
    )
    await session.commit()

    return await _build_detail(session, case=case, tenant_id=principal.tenant_id)


# ---------------------------------------------------------------------------
# Routes — close
# ---------------------------------------------------------------------------


@router.post("/cases/{case_id}/close", response_model=CaseDetail)
async def close_case(
    case_id: UUID,
    body: CloseCaseRequest,
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseDetail:
    """Close a case with a required reason.

    Idempotency is intentionally NOT applied here — a double-close
    returns ``409 conflict``. The reason must be non-empty (enforced by
    the schema).
    """
    case = await _load_case_or_404(session, case_id=case_id, tenant_id=principal.tenant_id)
    if case.status == CaseStatus.closed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="case already closed",
        )

    case.status = CaseStatus.closed
    await session.flush()

    await audit_record(
        session,
        principal=principal,
        action="case.closed",
        target_type="case",
        target_id=case.id,
        payload={"reason": body.reason},
    )
    await session.commit()

    return await _build_detail(session, case=case, tenant_id=principal.tenant_id)
