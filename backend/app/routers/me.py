"""``GET /me`` + ``GET/PATCH /me/preferences`` — the authenticated principal and per-user state.

The ``/me/preferences`` endpoints persist arbitrary JSON state per
(tenant, user) so the frontend doesn't have to round-trip through
``localStorage`` for things like the Fleet Cam truck row order.

Upsert-on-first-write
---------------------
The dev ``X-Dev-*`` headers mint a :class:`Principal` whose ``user_id``
may not have a corresponding row in ``users`` yet. The PATCH handler
therefore upserts the user row using the Principal's email / name /
roles / tenant on first write — that gives prefs somewhere to live in
dev mode without forcing the picker to seed users at startup.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.auth import Principal, current_user
from app.db import get_session
from app.models.user import User

router = APIRouter(tags=["auth"])


# ---------------------------------------------------------------------------
# /me
# ---------------------------------------------------------------------------


@router.get("/me", response_model=Principal)
async def read_me(
    principal: Annotated[Principal, Depends(current_user)],
) -> Principal:
    """Echo back the authenticated principal."""
    return principal


# ---------------------------------------------------------------------------
# /me/preferences
# ---------------------------------------------------------------------------


class PreferencesPatch(BaseModel):
    """Partial update body for ``PATCH /me/preferences``.

    Only known fields are typed; everything else round-trips through
    ``extra="allow"`` so the frontend can stash forward-compat keys
    without a schema change.
    """

    model_config = ConfigDict(extra="allow")

    truck_order: list[UUID] | None = None


async def _load_user(session: AsyncSession, principal: Principal) -> User | None:
    """Look up the principal's user row, tenant-scoped."""
    stmt = select(User).where(
        User.id == principal.user_id,
        User.tenant_id == principal.tenant_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


@router.get("/me/preferences", response_model=dict[str, Any])
async def get_preferences(
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Return the caller's preferences dict, or ``{}`` if no row exists yet."""
    user = await _load_user(session, principal)
    if user is None:
        return {}
    return dict(user.preferences or {})


@router.patch("/me/preferences", response_model=dict[str, Any])
async def patch_preferences(
    patch: PreferencesPatch,
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Merge ``patch`` into the caller's preferences dict, upserting the row if needed.

    The merge is shallow — top-level keys in ``patch`` replace existing
    keys; unspecified keys are left alone. UUID values in ``truck_order``
    are stringified for JSON storage.
    """
    # Use Pydantic's ``model_dump`` to capture both typed and extra fields.
    patch_dict = patch.model_dump(exclude_unset=True, mode="json")

    user = await _load_user(session, principal)
    if user is None:
        # First-time write for this principal — upsert a user row from the
        # claims we have available. See module docstring.
        user = User(
            id=principal.user_id,
            tenant_id=principal.tenant_id,
            email=principal.email,
            name=principal.name,
            roles=list(principal.roles),
            preferences={},
        )
        session.add(user)
        await session.flush()

    # Shallow merge: copy existing prefs, overlay patch keys.
    merged: dict[str, Any] = dict(user.preferences or {})
    merged.update(patch_dict)
    user.preferences = merged
    # SQLAlchemy needs a hint that a JSONB field was mutated in-place.
    # Easiest path: reassign to a fresh dict (already done above).

    await audit.record(
        session,
        principal=principal,
        action="user.preferences_updated",
        target_type="user",
        target_id=user.id,
        payload={"changed_keys": sorted(patch_dict.keys())},
    )

    await session.commit()
    return merged
