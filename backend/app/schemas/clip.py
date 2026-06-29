"""Pydantic response models for the clips API.

Two shapes are exposed:

* :class:`ClipRow` — the compact representation served from
  ``GET /clips``. Joins the clip with its truck and (optional) driver so
  the caller can render a list without an N+1.
* :class:`ClipDetail` — extends :class:`ClipRow` with sha256, dashcam
  firmware, and an optional ``playback_url``. The URL is only minted when
  the caller requests it via ``?play=true`` on ``GET /clips/{id}``.

Both models are populated directly from ORM rows; the list endpoint relies
on ``selectinload(Clip.truck, Clip.driver)`` so accessing the relationship
attributes during serialization doesn't trigger lazy loads.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.clip import Clip


class ClipRow(BaseModel):
    """Compact clip row — what ``GET /clips`` returns per item."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    truck_id: UUID
    truck_label: str
    driver_id: UUID | None
    driver_name: str | None
    started_at: datetime
    ended_at: datetime
    duration_s: int
    storage_key: str
    ingested_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _flatten_relationships(cls, data: object) -> object:
        """Pull ``truck.label`` / ``driver.name`` up onto the flat row.

        Pydantic's ``from_attributes`` can read top-level attributes off
        an ORM instance, but it can't traverse a relationship for us.
        When fed a :class:`Clip`, we synthesize a dict with the
        flattened keys so the model fields line up.
        """
        if isinstance(data, Clip):
            return {
                "id": data.id,
                "tenant_id": data.tenant_id,
                "truck_id": data.truck_id,
                "truck_label": data.truck.label,
                "driver_id": data.driver_id,
                "driver_name": data.driver.name if data.driver is not None else None,
                "started_at": data.started_at,
                "ended_at": data.ended_at,
                "duration_s": data.duration_s,
                "storage_key": data.storage_key,
                "ingested_at": data.ingested_at,
            }
        return data


class ClipDetail(ClipRow):
    """Detail view served by ``GET /clips/{id}``.

    Includes ingestion-time fields (sha256, firmware) plus an optional
    ``playback_url`` populated only on ``?play=true``.
    """

    sha256: str | None
    dashcam_firmware: str | None
    playback_url: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten_relationships(cls, data: object) -> object:
        """Same flattening as :class:`ClipRow`, plus the detail-only fields.

        ``playback_url`` isn't part of the ORM row — the router sets it
        explicitly after constructing the model — so we don't pull it
        from ``data`` here.
        """
        if isinstance(data, Clip):
            return {
                "id": data.id,
                "tenant_id": data.tenant_id,
                "truck_id": data.truck_id,
                "truck_label": data.truck.label,
                "driver_id": data.driver_id,
                "driver_name": data.driver.name if data.driver is not None else None,
                "started_at": data.started_at,
                "ended_at": data.ended_at,
                "duration_s": data.duration_s,
                "storage_key": data.storage_key,
                "ingested_at": data.ingested_at,
                "sha256": data.sha256,
                "dashcam_firmware": data.dashcam_firmware,
            }
        return data


class ClipListResponse(BaseModel):
    """Paginated list response for ``GET /clips``."""

    items: list[ClipRow]
    next_cursor: str | None = None
