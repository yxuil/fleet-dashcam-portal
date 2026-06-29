"""Pydantic response models for the trucks API.

Only one shape exists right now:

* :class:`TruckOut` — the row representation served from ``GET /trucks``.
  Maps directly off the :class:`app.models.truck.Truck` ORM row, no
  relationship traversal needed, so we lean on
  ``ConfigDict(from_attributes=True)`` and skip a ``model_validator``.
"""

from __future__ import annotations

from datetime import datetime
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
