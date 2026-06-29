"""Driver model."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.clip import Clip


class Driver(Base):
    __tablename__ = "drivers"
    __table_args__ = (Index("ix_drivers_tenant_id", "tenant_id"),)

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
    name: Mapped[str] = mapped_column(String, nullable=False)
    employee_ref: Mapped[str | None] = mapped_column(String, nullable=True)

    # ORM-only back-ref for clip listing endpoints.
    clips: Mapped[list[Clip]] = relationship(back_populates="driver", lazy="raise")
