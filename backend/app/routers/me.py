"""``GET /me`` — returns the authenticated principal."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth import Principal, current_user

router = APIRouter(tags=["auth"])


@router.get("/me", response_model=Principal)
async def read_me(
    principal: Annotated[Principal, Depends(current_user)],
) -> Principal:
    """Echo back the authenticated principal."""
    return principal
