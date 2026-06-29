"""``GET /trucks`` and ``GET /trucks/{id}`` — tenant-scoped truck listings.

The list endpoint is intentionally tiny: no filters, no pagination. A
fleet rarely has more than a few hundred trucks, and the Search page
needs them all in-memory for its multi-select. If a tenant ever grows
past the point where this matters, we'll add `limit/cursor` then.

Tenant isolation
----------------
Every query is filtered by ``principal.tenant_id``. A truck from another
tenant returns ``404`` rather than ``403`` so the caller can't probe for
existence across tenants — same convention used by ``/clips/{id}``.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, current_user
from app.db import get_session
from app.models.truck import Truck
from app.schemas.truck import TruckOut

router = APIRouter(tags=["trucks"])


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
