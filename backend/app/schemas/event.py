"""Pydantic response/request models for the events API.

Three shapes are exposed:

* :class:`EventRow` — the row representation served from ``GET /events``
  and ``POST /events/{id}/triage``. Joins the event with its truck so the
  caller can render a list without an N+1. Events don't directly link to a
  driver in the schema (driver attribution lives on the clip), so the
  driver-related fields are kept null for T7.
* :class:`EventListResponse` — paginated list response wrapper.
* :class:`TriageRequest` — body schema for ``POST /events/{id}/triage``.

Both row/response models are populated directly from ORM rows; the list
endpoint relies on ``selectinload(Event.truck)`` so accessing the
relationship attribute during serialization doesn't trigger lazy loads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.event import Event, EventSeverity, EventType


class EventRow(BaseModel):
    """One event row — what ``GET /events`` returns per item.

    ``type`` and ``severity`` serialize as their string values (e.g.
    ``"harsh_brake"``, ``"critical"``) because the underlying enums are
    ``StrEnum`` subclasses.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    truck_id: UUID
    truck_label: str
    # Events don't have a direct driver FK in T7; kept null so the response
    # shape is stable for the frontend.
    driver_id: UUID | None = None
    driver_name: str | None = None
    clip_id: UUID | None
    occurred_at: datetime
    type: EventType
    severity: EventSeverity
    telemetry: dict[str, Any] = Field(default_factory=dict)
    gps_lat: float | None
    gps_lng: float | None

    @model_validator(mode="before")
    @classmethod
    def _flatten_relationships(cls, data: object) -> object:
        """Pull ``truck.label`` up onto the flat row.

        Pydantic's ``from_attributes`` can read top-level attributes off
        an ORM instance, but it can't traverse a relationship for us.
        When fed an :class:`Event`, we synthesize a dict with the
        flattened keys so the model fields line up.
        """
        if isinstance(data, Event):
            return {
                "id": data.id,
                "tenant_id": data.tenant_id,
                "truck_id": data.truck_id,
                "truck_label": data.truck.label,
                "driver_id": None,
                "driver_name": None,
                "clip_id": data.clip_id,
                "occurred_at": data.occurred_at,
                "type": data.type,
                "severity": data.severity,
                "telemetry": data.telemetry,
                "gps_lat": data.gps_lat,
                "gps_lng": data.gps_lng,
            }
        return data


class EventListResponse(BaseModel):
    """Paginated list response for ``GET /events``."""

    items: list[EventRow]
    next_cursor: str | None = None


class TriageRequest(BaseModel):
    """Body schema for ``POST /events/{id}/triage``.

    ``label`` is a closed set — the frontend picks one of three values.
    ``note`` is optional free-form text persisted into the audit payload.
    The triage action is audit-only in T7: it never mutates the event row,
    and ``open_case`` does NOT create a case (that's a follow-up call to
    ``POST /cases`` in T8/T13).
    """

    label: Literal["false_positive", "coaching_note", "open_case"]
    note: str | None = None
