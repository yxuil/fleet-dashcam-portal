"""``GET /events``, ``GET /trucks/{id}/events``, and ``POST /events/{id}/triage``.

Filtering on the list endpoints
-------------------------------
* ``truck_id`` — optional exact-match filter on the event's ``truck_id``.
* ``driver_id`` — optional filter on the event's *clip*'s ``driver_id``.
  Events don't carry a direct driver FK; driver attribution lives on the
  clip the event belongs to. T13 enables this filter so the
  ``/drivers/:id/events`` frontend route can be implemented. When the
  filter is active, the query joins ``clips`` and matches
  ``Clip.driver_id``; events whose ``clip_id`` is null are excluded.
* ``clip_id`` — optional exact-match on the event's nullable ``clip_id`` FK.
  Used by T12 to render harsh-event markers on the clip timeline.
* ``from`` / ``to`` — ISO 8601 timestamps, inclusive of ``occurred_at``.
* ``severity`` / ``type`` — repeatable query params (e.g.
  ``?severity=high&severity=critical``) interpreted as set membership.
* ``limit`` + opaque ``cursor`` — pagination, mirroring ``GET /clips`` and
  ``GET /audit``.

Triage endpoint
---------------
``POST /events/{id}/triage`` writes an audit row with
``action="event.triage"`` and the label/note in the payload, then returns
the event unchanged. Per the T7 contract, the event row is NEVER mutated
by triage, and ``label="open_case"`` does NOT create a case — that's
explicitly T8's job and the frontend will follow up with a separate
``POST /cases`` call.

Tenant isolation
----------------
Every query is filtered by ``principal.tenant_id``. Events (or trucks) from
another tenant return ``404`` rather than ``403`` so the caller can't probe
for existence across tenants.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.audit import record as audit_record
from app.auth import Principal, current_user
from app.db import get_session
from app.models.clip import Clip
from app.models.event import Event, EventSeverity, EventType
from app.models.truck import Truck
from app.schemas.event import EventListResponse, EventRow, TriageRequest

router = APIRouter(tags=["events"])

#: Hard upper bound on page size, mirroring the other list endpoints.
MAX_EVENTS_LIMIT: int = 200

#: Default page size when the client doesn't specify one.
DEFAULT_EVENTS_LIMIT: int = 50


# ---------------------------------------------------------------------------
# Cursor helpers — opaque base64-url JSON of ``(occurred_at, id)``.
# Pattern mirrors ``app.routers.clips._encode_clip_cursor``. Kept inline
# (not extracted) since each endpoint family encodes a slightly different
# tuple and the helpers are small.
# ---------------------------------------------------------------------------


def _encode_event_cursor(occurred_at: datetime, event_id: UUID) -> str:
    raw = json.dumps(
        {"o": occurred_at.isoformat(), "i": str(event_id)}, separators=(",", ":")
    )
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_event_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Inverse of :func:`_encode_event_cursor`.

    Raises :class:`ValueError` on any garbage so the router can map to 400.
    """
    try:
        pad = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode((cursor + pad).encode("ascii"))
        obj = json.loads(raw.decode("utf-8"))
        return datetime.fromisoformat(obj["o"]), UUID(obj["i"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid cursor") from exc


# ---------------------------------------------------------------------------
# Shared query builder
# ---------------------------------------------------------------------------


def _apply_event_filters(
    stmt: Select[tuple[Event]],
    *,
    truck_id: UUID | None,
    driver_id: UUID | None,
    clip_id: UUID | None,
    from_: datetime | None,
    to: datetime | None,
    severity: list[EventSeverity],
    type_: list[EventType],
) -> Select[tuple[Event]]:
    """Append the standard filter set to an events ``SELECT``.

    Multi-valued ``severity`` and ``type_`` are interpreted as set
    membership — an event matches if its column value appears in the list.
    Empty lists mean "no filter on this dimension".

    ``driver_id`` is special: events don't carry a direct driver FK, so we
    join ``clips`` on ``Event.clip_id`` and filter on ``Clip.driver_id``.
    Events without an attached clip are excluded — they can't be attributed
    to a driver. Tenant scoping on the outer query still applies, but we
    additionally require ``Clip.tenant_id`` to match so a stray cross-tenant
    clip FK (shouldn't happen, but defence-in-depth) can't leak through.
    """
    if truck_id is not None:
        stmt = stmt.where(Event.truck_id == truck_id)
    if driver_id is not None:
        # INNER join (default) on clips: any event without a clip can't
        # be attributed to a driver and is excluded by construction.
        stmt = stmt.join(Clip, Clip.id == Event.clip_id).where(
            Clip.driver_id == driver_id,
            Clip.tenant_id == Event.tenant_id,
        )
    if clip_id is not None:
        stmt = stmt.where(Event.clip_id == clip_id)
    if from_ is not None:
        stmt = stmt.where(Event.occurred_at >= from_)
    if to is not None:
        stmt = stmt.where(Event.occurred_at <= to)
    if severity:
        stmt = stmt.where(Event.severity.in_(severity))
    if type_:
        stmt = stmt.where(Event.type.in_(type_))
    return stmt


async def _query_events_page(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    truck_id: UUID | None,
    driver_id: UUID | None,
    clip_id: UUID | None,
    from_: datetime | None,
    to: datetime | None,
    severity: list[EventSeverity],
    type_: list[EventType],
    limit: int,
    cursor: str | None,
) -> EventListResponse:
    """Run the paginated events query and assemble the response.

    Shared by ``GET /events`` and ``GET /trucks/{id}/events``. The truck
    membership check (for the per-truck endpoint) is the caller's
    responsibility; this helper only enforces tenant scoping.
    """
    capped_limit = min(limit, MAX_EVENTS_LIMIT)

    stmt: Select[tuple[Event]] = (
        select(Event)
        .join(Truck, Truck.id == Event.truck_id)
        .options(selectinload(Event.truck))
        .where(Event.tenant_id == tenant_id)
    )
    stmt = _apply_event_filters(
        stmt,
        truck_id=truck_id,
        driver_id=driver_id,
        clip_id=clip_id,
        from_=from_,
        to=to,
        severity=severity,
        type_=type_,
    )

    if cursor is not None:
        try:
            cursor_ts, cursor_id = _decode_event_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid cursor",
            ) from exc
        # Strict "less than (occurred_at, id)" — newer rows already served
        # on prior pages are excluded.
        stmt = stmt.where(
            or_(
                Event.occurred_at < cursor_ts,
                and_(Event.occurred_at == cursor_ts, Event.id < cursor_id),
            )
        )

    # Pull one extra row to detect whether another page exists without an
    # extra COUNT query.
    stmt = stmt.order_by(Event.occurred_at.desc(), Event.id.desc()).limit(
        capped_limit + 1
    )

    result = await session.execute(stmt)
    rows = list(result.scalars().all())

    has_more = len(rows) > capped_limit
    page = rows[:capped_limit]

    next_cursor: str | None = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_event_cursor(last.occurred_at, last.id)

    return EventListResponse(
        items=[EventRow.model_validate(r) for r in page],
        next_cursor=next_cursor,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/events", response_model=EventListResponse)
async def list_events(
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    truck_id: Annotated[UUID | None, Query()] = None,
    driver_id: Annotated[UUID | None, Query()] = None,
    clip_id: Annotated[UUID | None, Query()] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    severity: Annotated[list[EventSeverity] | None, Query()] = None,
    type_: Annotated[list[EventType] | None, Query(alias="type")] = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = DEFAULT_EVENTS_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> EventListResponse:
    """Return a page of events for the caller's tenant.

    ``driver_id`` filters via the event's attached clip. Events don't
    carry a direct driver FK; instead the filter joins ``clips`` on
    ``Event.clip_id`` and matches ``Clip.driver_id``. Events whose
    ``clip_id`` is null are excluded — they can't be attributed to a
    driver. Used by T13's ``/drivers/:id/events`` route.

    ``clip_id`` restricts results to events whose nullable ``clip_id`` FK
    matches. Used by T12's video-player page to render harsh-event markers
    on the clip timeline. Still tenant-scoped: a clip from another tenant
    will simply yield zero rows.
    """
    return await _query_events_page(
        session,
        tenant_id=principal.tenant_id,
        truck_id=truck_id,
        driver_id=driver_id,
        clip_id=clip_id,
        from_=from_,
        to=to,
        severity=severity or [],
        type_=type_ or [],
        limit=limit,
        cursor=cursor,
    )


@router.get("/trucks/{truck_id}/events", response_model=EventListResponse)
async def list_truck_events(
    truck_id: UUID,
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    severity: Annotated[list[EventSeverity] | None, Query()] = None,
    type_: Annotated[list[EventType] | None, Query(alias="type")] = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = DEFAULT_EVENTS_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> EventListResponse:
    """Return a page of events for one truck within the caller's tenant.

    If the truck does not exist, or belongs to a different tenant, return
    a ``404 not found`` — the same response either way so callers can't
    probe for cross-tenant truck existence.
    """
    truck = await session.execute(
        select(Truck.id).where(
            Truck.id == truck_id,
            Truck.tenant_id == principal.tenant_id,
        )
    )
    if truck.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="not found",
        )

    return await _query_events_page(
        session,
        tenant_id=principal.tenant_id,
        truck_id=truck_id,
        driver_id=None,
        clip_id=None,
        from_=from_,
        to=to,
        severity=severity or [],
        type_=type_ or [],
        limit=limit,
        cursor=cursor,
    )


@router.post("/events/{event_id}/triage", response_model=EventRow)
async def triage_event(
    event_id: UUID,
    body: TriageRequest,
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EventRow:
    """Record a triage decision in the audit log and return the event.

    T7 contract:

    * The event row is **not** mutated. The triage label and note are
      written as an audit entry only; the frontend (T13) re-queries audit
      to discover what's been triaged.
    * ``label="open_case"`` does **not** create a case row — that's T8.
      Only the audit entry is written; the frontend will follow up with a
      separate ``POST /cases`` call.

    Tenant isolation: a missing event, or one belonging to another
    tenant, returns a ``404 not found`` with no hint that the row exists
    elsewhere.
    """
    stmt = (
        select(Event)
        .options(selectinload(Event.truck))
        .where(Event.id == event_id, Event.tenant_id == principal.tenant_id)
    )
    result = await session.execute(stmt)
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="not found",
        )

    # Audit FIRST so the row is part of the same transaction we then
    # commit. The flush inside ``audit_record`` ensures the row hits the
    # DB before the commit.
    await audit_record(
        session,
        principal=principal,
        action="event.triage",
        target_type="event",
        target_id=event.id,
        payload={"label": body.label, "note": body.note},
    )
    await session.commit()

    return EventRow.model_validate(event)
