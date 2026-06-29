"""``GET /trucks`` and ``GET /trucks/{id}`` — tenant-scoped truck listings.

The list endpoint is intentionally tiny: no filters, no pagination. A
fleet rarely has more than a few hundred trucks, and the Search page
needs them all in-memory for its multi-select. If a tenant ever grows
past the point where this matters, we'll add `limit/cursor` then.

``GET /trucks/{id}/days`` returns a per-day rollup of clips for one
truck, used by the Fleet Cam page to render the horizontal day-card
strip. The returned ``first_clip_id`` is a representative clip the UI
opens on click.

Tenant isolation
----------------
Every query is filtered by ``principal.tenant_id``. A truck from another
tenant returns ``404`` rather than ``403`` so the caller can't probe for
existence across tenants — same convention used by ``/clips/{id}``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, current_user
from app.db import get_session
from app.models.clip import Clip
from app.models.truck import Truck
from app.schemas.truck import TruckDay, TruckOut

router = APIRouter(tags=["trucks"])

#: Hard upper bound on the days endpoint's ``limit`` parameter to keep
#: the per-row payload small and the SQL cheap.
MAX_DAYS_LIMIT: int = 365

#: Default day-row count when the client doesn't specify one. Covers
#: roughly the last month of activity, which is what the Fleet Cam row
#: scroller shows out of the box.
DEFAULT_DAYS_LIMIT: int = 30


@router.get("/trucks", response_model=list[TruckOut])
async def list_trucks(
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[TruckOut]:
    """Return every truck for the caller's tenant, ordered by label."""
    stmt = (
        select(Truck)
        .where(Truck.tenant_id == principal.tenant_id)
        .order_by(Truck.label.asc())
    )
    result = await session.execute(stmt)
    return [TruckOut.model_validate(t) for t in result.scalars().all()]


@router.get("/trucks/{truck_id}", response_model=TruckOut)
async def get_truck(
    truck_id: UUID,
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TruckOut:
    """Return a single truck by id, scoped to the caller's tenant."""
    stmt = select(Truck).where(
        Truck.id == truck_id,
        Truck.tenant_id == principal.tenant_id,
    )
    result = await session.execute(stmt)
    truck = result.scalar_one_or_none()
    if truck is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="not found",
        )
    return TruckOut.model_validate(truck)


@router.get("/trucks/{truck_id}/days", response_model=list[TruckDay])
async def list_truck_days(
    truck_id: UUID,
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    driver_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_DAYS_LIMIT)] = DEFAULT_DAYS_LIMIT,
) -> list[TruckDay]:
    """Return per-day rollups of clips for ``truck_id``, newest day first.

    ``first_clip_id`` is the smallest UUID for the day — stable and cheap
    to compute. For MVP this is "a clip from that day" rather than
    "literally the first clip by ``started_at``"; the UI only needs it as
    a click-through target.
    """
    # Tenant ownership check — honest 404 on cross-tenant access.
    truck_stmt = select(Truck.id).where(
        Truck.id == truck_id,
        Truck.tenant_id == principal.tenant_id,
    )
    if (await session.execute(truck_stmt)).scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="not found",
        )

    day_col = func.date_trunc("day", Clip.started_at).label("day")
    # ``MIN(uuid)`` isn't defined in Postgres. Picking the clip with the
    # earliest ``started_at`` for the day gives a stable, intuitive
    # representative — and ``(array_agg ORDER BY started_at)[1]`` lets us
    # do that inside the GROUP BY without a self-join.
    first_clip_expr = func.array_agg(
        Clip.id, order_by=Clip.started_at.asc()
    )[1].label("first_clip_id")
    stmt = (
        select(
            day_col,
            func.count().label("clip_count"),
            first_clip_expr,
            func.coalesce(func.sum(Clip.duration_s), 0).label("total_duration_s"),
        )
        .where(
            Clip.tenant_id == principal.tenant_id,
            Clip.truck_id == truck_id,
        )
        .group_by(day_col)
        .order_by(desc(day_col))
        .limit(limit)
    )
    if from_ is not None:
        stmt = stmt.where(Clip.started_at >= from_)
    if to is not None:
        stmt = stmt.where(Clip.started_at <= to)
    if driver_id is not None:
        stmt = stmt.where(Clip.driver_id == driver_id)

    rows = (await session.execute(stmt)).all()
    return [
        TruckDay(
            date=row.day.date(),
            clip_count=int(row.clip_count),
            first_clip_id=row.first_clip_id,
            total_duration_s=int(row.total_duration_s),
        )
        for row in rows
    ]
