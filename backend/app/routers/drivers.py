"""``GET /drivers`` — tenant-scoped driver listing.

Same shape and rationale as ``/trucks``: a tenant's driver roster is
small enough to return un-paginated.

Tenant isolation
----------------
Every query is filtered by ``principal.tenant_id``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, current_user
from app.db import get_session
from app.models.driver import Driver
from app.schemas.driver import DriverOut

router = APIRouter(tags=["drivers"])


@router.get("/drivers", response_model=list[DriverOut])
async def list_drivers(
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[DriverOut]:
    """Return every driver for the caller's tenant, ordered by name."""
    stmt = (
        select(Driver)
        .where(Driver.tenant_id == principal.tenant_id)
        .order_by(Driver.name.asc())
    )
    result = await session.execute(stmt)
    return [DriverOut.model_validate(d) for d in result.scalars().all()]
