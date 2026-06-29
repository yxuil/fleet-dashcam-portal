"""Pydantic request/response models for the clip ingest stub.

The shapes here mirror the contract the upstream ingest service will use
once we wire up real camera-to-portal upload notifications. For the MVP
(T9) the endpoint is a forward-compatible stub: callers POST metadata
plus a ``storage_key`` they've already uploaded to MinIO, and we record
a ``clips`` row + an audit entry.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class IngestClipRequest(BaseModel):
    """Body schema for ``POST /ingest/clips``.

    The caller is expected to have already PUT the MP4 to
    ``s3://{bucket}/{storage_key}``. ``tenant_id`` is trusted here — this
    stub has no authentication; see the router docstring for the v2 plan.
    """

    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    truck_id: UUID
    driver_id: UUID | None = None
    started_at: datetime
    ended_at: datetime
    duration_s: int
    storage_key: str
    sha256: str | None = None
    dashcam_firmware: str | None = None


class IngestClipResponse(BaseModel):
    """Response from ``POST /ingest/clips`` — just the new clip's UUID."""

    clip_id: UUID
