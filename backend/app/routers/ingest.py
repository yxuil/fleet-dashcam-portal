"""``POST /ingest/clips`` — forward-compat stub for upstream ingest service.

This is the only endpoint in the portal that does **not** require an
authenticated principal. It's expected to be called by an internal ingest
service (running inside the same trust boundary) that doesn't carry a
user JWT. The caller supplies ``tenant_id`` directly in the request body
and we trust it.

Security: TODO(v2)
------------------
This stub is acceptable for the MVP because:

* The endpoint is not exposed publicly — it's reachable only from inside
  the cluster / VPC where the ingest service lives.
* The actual MP4 upload happened via a presigned URL minted by the ingest
  service; we only record metadata.

Before going to production, v2 needs an HMAC-signed service-to-service
auth: the ingest service signs the request body with a shared secret, and
this handler verifies the signature before trusting ``tenant_id``. Until
then, do not route this endpoint through any public load balancer.

What this endpoint does
-----------------------
1. Validates that ``truck_id`` belongs to ``tenant_id`` (404 otherwise so
   we don't leak existence across tenants).
2. Validates that ``storage_key`` lives under the tenant's prefix (same
   check as the rest of the portal — :func:`storage._validate_tenant_prefix`).
3. Inserts a ``clips`` row.
4. Writes an audit entry (``action="clip.ingested"``, ``actor_user_id=None``)
   in the same transaction.
5. Commits and returns the new ``clip_id``.
"""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_system
from app.db import get_session
from app.models.clip import Clip
from app.models.truck import Truck
from app.schemas.ingest import IngestClipRequest, IngestClipResponse
from app.storage import _validate_tenant_prefix

router = APIRouter(tags=["ingest"])


@router.post(
    "/ingest/clips",
    response_model=IngestClipResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_clip(
    body: IngestClipRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IngestClipResponse:
    """Record metadata for an already-uploaded clip.

    No authentication required (see module docstring). Trusts the
    ``tenant_id`` in the request body and enforces tenant isolation only
    through the truck-ownership and storage-key-prefix checks.
    """
    # 1. Verify truck belongs to the asserted tenant. 404 (not 403) so the
    #    caller can't probe whether a truck id exists for a different tenant.
    truck_stmt = select(Truck).where(
        Truck.id == body.truck_id,
        Truck.tenant_id == body.tenant_id,
    )
    truck = (await session.execute(truck_stmt)).scalar_one_or_none()
    if truck is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="truck not found",
        )

    # 2. Verify the storage_key lives under the tenant's prefix. The same
    #    check that gate-keeps presigned URLs; rejecting at ingest time
    #    means cross-tenant keys never even land in our DB.
    try:
        _validate_tenant_prefix(body.tenant_id, body.storage_key)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="storage_key does not belong to tenant",
        ) from exc

    # 3. Insert the clip row.
    clip_id = uuid4()
    clip = Clip(
        id=clip_id,
        tenant_id=body.tenant_id,
        truck_id=body.truck_id,
        driver_id=body.driver_id,
        started_at=body.started_at,
        ended_at=body.ended_at,
        duration_s=body.duration_s,
        storage_key=body.storage_key,
        sha256=body.sha256,
        dashcam_firmware=body.dashcam_firmware,
    )
    session.add(clip)
    await session.flush()

    # 4. Audit (system action — no principal). Lives in the same
    #    transaction as the insert, so a downstream failure rolls both back.
    await record_system(
        session,
        tenant_id=body.tenant_id,
        action="clip.ingested",
        target_type="clip",
        target_id=clip_id,
        payload={
            "truck_id": str(body.truck_id),
            "storage_key": body.storage_key,
            "sha256": body.sha256,
        },
    )

    # 5. Commit and return.
    await session.commit()
    return IngestClipResponse(clip_id=clip_id)


__all__ = ["router", "ingest_clip"]
