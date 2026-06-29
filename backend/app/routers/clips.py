"""``GET /clips`` and ``GET /clips/{id}`` — clip list + detail with signed playback.

Filtering on the list endpoint
------------------------------
* ``truck_id`` / ``driver_id`` — optional exact-match filters.
* ``from`` / ``to`` — ISO 8601 timestamps, inclusive of ``started_at``.
* ``text`` — case-insensitive substring match against ``trucks.label`` OR
  ``drivers.name`` (driver join is OUTER because ``driver_id`` is nullable).
* ``limit`` + opaque ``cursor`` — pagination, mirroring ``GET /audit``.

Detail endpoint
---------------
``GET /clips/{id}`` returns metadata only by default. Pass ``?play=true``
to mint a fresh ``DEFAULT_SIGNED_URL_TTL_S``-second signed playback URL
and write an audit row (``action="clip.play_url_minted"``). The audit row
is part of the same transaction as the read — if anything fails before
commit, the audit doesn't persist.

Tenant isolation
----------------
Every query is filtered by ``principal.tenant_id``. A clip from another
tenant returns ``404`` rather than ``403`` so the caller can't probe for
existence across tenants.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.audit import record as audit_record
from app.auth import Principal, current_user
from app.db import get_session
from app.models.clip import Clip
from app.models.driver import Driver
from app.models.truck import Truck
from app.schemas.clip import ClipDetail, ClipListResponse, ClipRow
from app.storage import DEFAULT_SIGNED_URL_TTL_S, get_signed_url

router = APIRouter(tags=["clips"])

#: Hard upper bound on page size, mirroring ``GET /audit``.
MAX_CLIPS_LIMIT: int = 200

#: Default page size when the client doesn't specify one.
DEFAULT_CLIPS_LIMIT: int = 50


# ---------------------------------------------------------------------------
# Cursor helpers — opaque base64-url JSON of ``(started_at, id)``.
# Pattern mirrors ``app.audit.encode_cursor`` / ``decode_cursor``. Kept
# inline (not extracted) since the schemas are small and the audit one
# encodes ``(occurred_at, int_id)`` whereas this one needs ``UUID`` ids.
# ---------------------------------------------------------------------------


def _encode_clip_cursor(started_at: datetime, clip_id: UUID) -> str:
    raw = json.dumps(
        {"s": started_at.isoformat(), "i": str(clip_id)}, separators=(",", ":")
    )
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_clip_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Inverse of :func:`_encode_clip_cursor`.

    Raises :class:`ValueError` on any garbage so the router can map to 400.
    """
    try:
        pad = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode((cursor + pad).encode("ascii"))
        obj = json.loads(raw.decode("utf-8"))
        return datetime.fromisoformat(obj["s"]), UUID(obj["i"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid cursor") from exc


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/clips", response_model=ClipListResponse)
async def list_clips(
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    truck_id: Annotated[UUID | None, Query()] = None,
    driver_id: Annotated[UUID | None, Query()] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    text: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = DEFAULT_CLIPS_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> ClipListResponse:
    """Return a page of clips for the caller's tenant.

    All filters AND together. The ``text`` filter joins trucks (and
    outer-joins drivers, since ``driver_id`` is nullable) so a single
    ILIKE pattern can match either the truck label or driver name.
    """
    capped_limit = min(limit, MAX_CLIPS_LIMIT)

    stmt = (
        select(Clip)
        .join(Truck, Truck.id == Clip.truck_id)
        .outerjoin(Driver, Driver.id == Clip.driver_id)
        .options(selectinload(Clip.truck), selectinload(Clip.driver))
        .where(Clip.tenant_id == principal.tenant_id)
    )

    if truck_id is not None:
        stmt = stmt.where(Clip.truck_id == truck_id)
    if driver_id is not None:
        stmt = stmt.where(Clip.driver_id == driver_id)
    if from_ is not None:
        stmt = stmt.where(Clip.started_at >= from_)
    if to is not None:
        stmt = stmt.where(Clip.started_at <= to)
    if text is not None and text != "":
        pattern = f"%{text}%"
        stmt = stmt.where(or_(Truck.label.ilike(pattern), Driver.name.ilike(pattern)))

    if cursor is not None:
        try:
            cursor_ts, cursor_id = _decode_clip_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid cursor",
            ) from exc
        # Strict "less than (started_at, id)" — newer rows already served
        # on prior pages are excluded.
        stmt = stmt.where(
            or_(
                Clip.started_at < cursor_ts,
                and_(Clip.started_at == cursor_ts, Clip.id < cursor_id),
            )
        )

    # Pull one extra row to detect whether another page exists without
    # an extra COUNT query.
    stmt = stmt.order_by(Clip.started_at.desc(), Clip.id.desc()).limit(capped_limit + 1)

    result = await session.execute(stmt)
    rows = list(result.scalars().all())

    has_more = len(rows) > capped_limit
    page = rows[:capped_limit]

    next_cursor: str | None = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_clip_cursor(last.started_at, last.id)

    return ClipListResponse(
        items=[ClipRow.model_validate(r) for r in page],
        next_cursor=next_cursor,
    )


@router.get("/clips/{clip_id}", response_model=ClipDetail)
async def get_clip(
    clip_id: UUID,
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    play: Annotated[bool, Query()] = False,
) -> ClipDetail:
    """Return clip metadata, optionally with a freshly-minted playback URL.

    Tenant isolation: a clip whose ``tenant_id`` doesn't match the caller
    produces a ``404 not found`` — the same response as a truly missing
    row — so callers can't enumerate other tenants' ids.

    ``?play=true`` writes an audit entry **before** minting the signed
    URL, and commits the transaction at the end of the handler. This
    keeps audit + read in one unit-of-work: if the URL mint somehow
    fails, the audit row rolls back with it.
    """
    stmt = (
        select(Clip)
        .options(selectinload(Clip.truck), selectinload(Clip.driver))
        .where(Clip.id == clip_id, Clip.tenant_id == principal.tenant_id)
    )
    result = await session.execute(stmt)
    clip = result.scalar_one_or_none()
    if clip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="not found",
        )

    detail = ClipDetail.model_validate(clip)

    if play:
        # Audit FIRST so the row is part of the same transaction that
        # mints the URL. The flush inside ``audit_record`` ensures the
        # row hits the DB before we do any external work.
        await audit_record(
            session,
            principal=principal,
            action="clip.play_url_minted",
            target_type="clip",
            target_id=clip.id,
            payload={"signed_url_ttl_s": DEFAULT_SIGNED_URL_TTL_S},
        )
        url = await get_signed_url(
            principal.tenant_id,
            clip.storage_key,
            expires_s=DEFAULT_SIGNED_URL_TTL_S,
        )
        await session.commit()
        # ``ClipDetail`` is a Pydantic model; ``model_copy`` keeps it immutable-ish.
        detail = detail.model_copy(update={"playback_url": url})

    return detail
