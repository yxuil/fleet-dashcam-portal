"""Pydantic request/response models for the cases API.

Five shapes are exposed:

* :class:`CaseRow` — compact representation served from ``GET /cases`` and
  used as the base for :class:`CaseDetail`. Mapped directly from a
  :class:`app.models.case.Case` ORM row.
* :class:`AttachedClip` — one entry in :class:`CaseDetail.clips`. Joins
  the ``case_clips`` row with the underlying clip and its truck so the
  caller can render the list without an N+1.
* :class:`CaseDetail` — full detail served from ``GET /cases/{id}``.
  Extends :class:`CaseRow` with attached clips and the most recent 50
  audit entries scoped to ``target_type="case" AND target_id=<case_id>``.
* :class:`CaseCreate` — body for ``POST /cases``. Everything except the
  number, status, created_by, created_at is caller-supplied; the number
  is generated server-side per tenant per calendar year.
* :class:`CasePatch` — body for ``PATCH /cases/{id}``. All fields
  optional. ``status`` deliberately excludes ``"closed"`` — that
  transition must go through ``POST /cases/{id}/close`` so a reason is
  always captured in the audit log.
* :class:`AttachClipRequest` — body for ``POST /cases/{id}/clips``.
* :class:`CloseCaseRequest` — body for ``POST /cases/{id}/close``;
  ``reason`` is required and must be non-empty.
* :class:`CaseListResponse` — paginated list wrapper.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.audit import AuditEntry
from app.models.case import CaseStatus
from app.models.case_clip import CaseClip

# ---------------------------------------------------------------------------
# PATCH status sub-type
# ---------------------------------------------------------------------------

# ``PATCH /cases/{id}`` accepts every status EXCEPT ``"closed"``. Closing a
# case must record a reason in the audit log, which only the dedicated
# ``POST /cases/{id}/close`` endpoint enforces. Modelling the allowed set
# as a Literal lets FastAPI return 422 automatically for the bad value.
PatchableCaseStatus = Literal["open", "under_review", "approved"]


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class CaseRow(BaseModel):
    """One case row — what ``GET /cases`` returns per item."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    number: str
    external_ref: str | None
    requester_name: str | None
    requester_org: str | None
    incident_at: datetime | None
    status: CaseStatus
    assignee_user_id: UUID | None
    due_at: datetime | None
    created_by: UUID
    created_at: datetime


class AttachedClip(BaseModel):
    """One attached-clip entry inside :class:`CaseDetail`.

    Flattens ``clip.truck.label`` and ``clip.started_at`` up onto the
    response so the frontend can render the list without a second
    round-trip per row.
    """

    model_config = ConfigDict(from_attributes=True)

    clip_id: UUID
    attached_at: datetime
    attached_by: UUID
    note: str | None
    truck_label: str
    started_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: object) -> object:
        if isinstance(data, CaseClip):
            return {
                "clip_id": data.clip_id,
                "attached_at": data.attached_at,
                "attached_by": data.attached_by,
                "note": data.note,
                "truck_label": data.clip.truck.label,
                "started_at": data.clip.started_at,
            }
        return data


class CaseDetail(CaseRow):
    """Detail view served by ``GET /cases/{id}``."""

    clips: list[AttachedClip] = Field(default_factory=list)
    recent_audit: list[AuditEntry] = Field(default_factory=list)


class CaseListResponse(BaseModel):
    """Paginated list response for ``GET /cases``."""

    items: list[CaseRow]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CaseCreate(BaseModel):
    """Body for ``POST /cases``.

    All fields optional except those derived from the authenticated caller
    (``tenant_id``, ``created_by``) and the server-generated ``number``
    and ``status``.
    """

    external_ref: str | None = None
    requester_name: str | None = None
    requester_org: str | None = None
    incident_at: datetime | None = None
    due_at: datetime | None = None
    assignee_user_id: UUID | None = None


class CasePatch(BaseModel):
    """Partial update body for ``PATCH /cases/{id}``.

    ``status`` is intentionally typed as :data:`PatchableCaseStatus`,
    which omits ``"closed"`` — that transition must go through
    ``POST /cases/{id}/close`` so a reason is captured in the audit log.
    """

    # ``exclude_unset=True`` on ``model_dump`` lets the router tell the
    # difference between "field omitted" and "field set to null".
    model_config = ConfigDict(extra="forbid")

    status: PatchableCaseStatus | None = None
    assignee_user_id: UUID | None = None
    due_at: datetime | None = None
    external_ref: str | None = None
    requester_name: str | None = None
    requester_org: str | None = None
    incident_at: datetime | None = None


class AttachClipRequest(BaseModel):
    """Body for ``POST /cases/{id}/clips``."""

    clip_id: UUID
    note: str | None = None


class CloseCaseRequest(BaseModel):
    """Body for ``POST /cases/{id}/close``.

    ``reason`` is required and must be at least one non-whitespace
    character — the audit row would be useless without it.
    """

    reason: Annotated[str, Field(min_length=1)]
