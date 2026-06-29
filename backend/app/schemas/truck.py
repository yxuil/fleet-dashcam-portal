"""Pydantic response models for the trucks API.

Only one shape exists right now:

* :class:`TruckOut` — the row representation served from ``GET /trucks``.
  Maps directly off the :class:`app.models.truck.Truck` ORM row, no
  relationship traversal needed, so we lean on
  ``ConfigDict(from_attributes=True)`` and skip a ``model_validator``.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TruckOut(BaseModel):
    """One truck row served by ``GET /trucks`` and ``GET /trucks/{id}``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    label: str
    vin: str | None = None
    dashcam_serial: str | None = None
    last_seen_at: datetime | None = None


class TruckDay(BaseModel):
    """One day's worth of clips for a truck, served by ``GET /trucks/{id}/days``.

    Powers the Fleet Cam horizontal day-card strip on the dashcam page.
    ``first_clip_id`` is a representative clip used as the click-through
    target — the frontend opens that clip in the player.
    """

    model_config = ConfigDict(from_attributes=True)

    date: date
    clip_count: int
    first_clip_id: UUID
    total_duration_s: int
