"""``GET /audit`` — list recent audit entries scoped to the caller's tenant.

Filtering
---------
* ``target_type`` / ``target_id`` / ``action`` — optional exact-match filters.
* ``limit`` — default 50, capped at 200.
* ``cursor`` — opaque continuation token from a previous page's
  ``next_cursor``. Encodes ``(occurred_at, id)`` so pagination is stable
  even across rows sharing a timestamp.

Tenant scoping is **not** user-controllable. The ``tenant_id`` filter is
always pulled from the authenticated principal; there is no
``?tenant_id=`` query parameter.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import (
    AuditEntry,
    AuditListResponse,
    decode_cursor,
    encode_cursor,
)
from app.auth import Principal, current_user
from app.db import get_session
from app.models.audit import AuditLog

router = APIRouter(tags=["audit"])

#: Hard upper bound on page size. Requests above this are silently capped.
MAX_AUDIT_LIMIT: int = 200

#: Default page size when the client doesn't specify one.
DEFAULT_AUDIT_LIMIT: int = 50


@router.get("/audit", response_model=AuditListResponse)
async def list_audit(
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    target_type: Annotated[str | None, Query()] = None,
    target_id: Annotated[UUID | None, Query()] = None,
    action: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = DEFAULT_AUDIT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> AuditListResponse:
    """Return a page of audit entries for the caller's tenant."""
    capped_limit = min(limit, MAX_AUDIT_LIMIT)

    stmt = select(AuditLog).where(AuditLog.tenant_id == principal.tenant_id)

    if target_type is not None:
        stmt = stmt.where(AuditLog.target_type == target_type)
    if target_id is not None:
        stmt = stmt.where(AuditLog.target_id == target_id)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)

    if cursor is not None:
        try:
            cursor_ts, cursor_id = decode_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid cursor",
            ) from exc
        # Strict "less than (occurred_at, id)" — newer rows already
        # served on prior pages are excluded.
        stmt = stmt.where(
            or_(
                AuditLog.occurred_at < cursor_ts,
                and_(AuditLog.occurred_at == cursor_ts, AuditLog.id < cursor_id),
            )
        )

    # Pull one extra row to detect whether another page exists without
    # an extra COUNT query.
    stmt = stmt.order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc()).limit(
        capped_limit + 1
    )

    result = await session.execute(stmt)
    rows = list(result.scalars().all())

    has_more = len(rows) > capped_limit
    page = rows[:capped_limit]

    next_cursor: str | None = None
    if has_more and page:
        last = page[-1]
        next_cursor = encode_cursor(last.occurred_at, last.id)

    return AuditListResponse(
        items=[AuditEntry.model_validate(r) for r in page],
        next_cursor=next_cursor,
    )
