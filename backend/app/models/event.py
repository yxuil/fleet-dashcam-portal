"""Event model — telemetry-derived incidents (harsh brake, collision, …)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class EventType(StrEnum):
    harsh_brake = "harsh_brake"
    harsh_accel = "harsh_accel"
    collision = "collision"
    lane_departure = "lane_departure"
    speeding = "speeding"
    distracted_driving = "distracted_driving"


class EventSeverity(StrEnum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index(
            "ix_events_tenant_truck_occurred",
            "tenant_id",
            "truck_id",
            "occurred_at",
        ),
        Index(
            "ix_events_tenant_severity_occurred",
            "tenant_id",
            "severity",
            "occurred_at",
        ),
        Index("ix_events_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    truck_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trucks.id"),
        nullable=False,
    )
    # Cases attach clips to events implicitly via the case_clips join, so an
    # event may be unlinked at ingest time; SET NULL if the clip is removed.
    clip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clips.id", ondelete="SET NULL"),
        nullable=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    type: Mapped[EventType] = mapped_column(
        Enum(EventType, name="event_type", native_enum=True, create_type=True),
        nullable=False,
    )
    severity: Mapped[EventSeverity] = mapped_column(
        Enum(EventSeverity, name="event_severity", native_enum=True, create_type=True),
        nullable=False,
    )
    telemetry: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
    )
    gps_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    gps_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
