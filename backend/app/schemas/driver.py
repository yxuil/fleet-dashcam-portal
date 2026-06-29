"""Pydantic response models for the drivers API.

Only one shape exists right now:

* :class:`DriverOut` — the row representation served from ``GET /drivers``.
  Maps directly off the :class:`app.models.driver.Driver` ORM row.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DriverOut(BaseModel):
    """One driver row served by ``GET /drivers``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    employee_ref: str | None = None
