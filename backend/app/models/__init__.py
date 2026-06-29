"""SQLAlchemy ORM models.

Importing this package registers all tables on ``Base.metadata`` so that
Alembic's ``target_metadata = Base.metadata`` picks them up for autogenerate.
"""

from app.models.audit import AuditLog
from app.models.case import Case, CaseStatus
from app.models.case_clip import CaseClip
from app.models.clip import Clip
from app.models.driver import Driver
from app.models.event import Event, EventSeverity, EventType
from app.models.tenant import Tenant
from app.models.truck import Truck
from app.models.user import User

__all__ = [
    "AuditLog",
    "Case",
    "CaseClip",
    "CaseStatus",
    "Clip",
    "Driver",
    "Event",
    "EventSeverity",
    "EventType",
    "Tenant",
    "Truck",
    "User",
]
