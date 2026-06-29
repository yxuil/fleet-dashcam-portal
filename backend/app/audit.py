"""Audit log writer and read models.

The audit log is **append-only**. This module deliberately exposes only a
write path (:func:`record`) and read models — there are no update or delete
helpers, and the test suite enforces that invariant by grep'ing this file.

Transactional semantics
-----------------------
:func:`record` does **not** call ``session.commit()``. Audit writes are
expected to be part of the same logical unit-of-work as the action they
audit: the router that mutated state opens a transaction, performs its
work, calls :func:`record`, and then commits everything atomically. If the
business operation rolls back, its audit row goes with it.

Pagination
----------
Read endpoints use an opaque base64-url cursor that encodes the last seen
``(occurred_at, id)`` tuple. Ordering is ``occurred_at DESC, id DESC``,
which is stable even when multiple rows share an ``occurred_at`` value.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.models.audit import AuditLog

# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


async def record(
    session: AsyncSession,
    *,
    principal: Principal,
    action: str,
    target_type: str,
    target_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append one audit row tied to ``principal``'s tenant and user.

    The row is staged on the session via ``session.add(...)`` and flushed
    so the ``id``/``occurred_at`` server defaults populate, but this
    function **does not commit**. The calling router controls the
    transaction boundary so the audit row lives or dies with the action
    it records.

    Args:
        session: Active async SQLAlchemy session.
        principal: Authenticated caller. Supplies ``tenant_id`` and
            ``actor_user_id``.
        action: Short verb such as ``"case.create"`` or ``"clip.signed"``.
        target_type: The kind of entity acted upon (``"case"``, ``"clip"``…).
        target_id: Optional UUID of the specific entity. Allowed to be
            ``None`` for tenant-wide actions.
        payload: Arbitrary JSON-serializable detail. ``None`` is coerced
            to ``{}`` so the column never holds SQL ``NULL``.
    """
    row = AuditLog(
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        payload=payload if payload is not None else {},
    )
    session.add(row)
    await session.flush()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class AuditEntry(BaseModel):
    """Single audit row in API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: UUID
    actor_user_id: UUID | None
    action: str
    target_type: str
    target_id: UUID | None
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class AuditListResponse(BaseModel):
    """Paginated list of audit entries."""

    items: list[AuditEntry]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Cursor helpers (opaque base64-url JSON)
# ---------------------------------------------------------------------------


def encode_cursor(occurred_at: datetime, row_id: int) -> str:
    """Encode the ``(occurred_at, id)`` pair to an opaque base64-url string.

    Padding ``=`` characters are stripped so the cursor is URL-safe; we
    re-pad on decode.
    """
    raw = json.dumps({"o": occurred_at.isoformat(), "i": row_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    """Inverse of :func:`encode_cursor`. Raises :class:`ValueError` on garbage."""
    try:
        # Re-pad to a multiple of 4 for base64.
        pad = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode((cursor + pad).encode("ascii"))
        obj = json.loads(raw.decode("utf-8"))
        return datetime.fromisoformat(obj["o"]), int(obj["i"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid cursor") from exc
