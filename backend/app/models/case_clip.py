"""Case<->Clip join table."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.case import Case
    from app.models.clip import Clip


class CaseClip(Base):
    __tablename__ = "case_clips"

    # Composite primary key: a clip can appear on a case at most once.
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    clip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clips.id", ondelete="CASCADE"),
        primary_key=True,
    )
    attached_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    attached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    note: Mapped[str | None] = mapped_column(String, nullable=True)

    # ORM-only relationships — no migration impact. ``Case.clips`` uses
    # this back-ref. The forward link to ``Clip`` powers the attached-clips
    # listing on ``GET /cases/{id}`` (with selectinload chain so we can
    # flatten ``clip.truck.label`` without N+1).
    case: Mapped[Case] = relationship(back_populates="clips", lazy="raise")
    clip: Mapped[Clip] = relationship(lazy="raise")
