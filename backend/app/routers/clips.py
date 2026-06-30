"""``GET /clips``, ``GET /clips/{id}``, ``GET /clips/{id}/stream`` — clip list, detail, playback.

Filtering on the list endpoint
------------------------------
* ``truck_id`` / ``driver_id`` — optional exact-match filters.
* ``from`` / ``to`` — ISO 8601 timestamps, inclusive of ``started_at``.
* ``text`` — case-insensitive substring match against ``trucks.label`` OR
  ``drivers.name`` (driver join is OUTER because ``driver_id`` is nullable).
* ``limit`` + opaque ``cursor`` — pagination, mirroring ``GET /audit``.

Detail endpoint
---------------
``GET /clips/{id}`` returns metadata only by default. Pass ``?play=true``
to mint a fresh playback URL and write an audit row
(``action="clip.play_url_minted"``). The audit row is part of the same
transaction as the read — if anything fails before commit, the audit
doesn't persist. The URL shape depends on ``settings.storage_backend``:
in ``local`` mode it's the relative path ``"/clips/{id}/stream"``; in
``s3`` mode it's a SigV4 presigned GET URL.

Stream endpoint
---------------
``GET /clips/{id}/stream`` serves the MP4 bytes from disk via
``FileResponse`` (with native HTTP Range support for scrubbing). Only
used in ``local`` mode; in ``s3`` mode the browser hits MinIO/S3 directly
via the signed URL and this route is unused. No audit row is written
here — ``clip.play_url_minted`` on the sibling detail call already
captures playback intent.

Tenant isolation
----------------
Every query is filtered by ``principal.tenant_id``. A clip from another
tenant returns ``404`` rather than ``403`` so the caller can't probe for
existence across tenants.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import jwt
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import storage as storage_module
from app.audit import record as audit_record
from app.auth import Principal, current_user, get_settings
from app.config import Settings, settings
from app.db import get_session
from app.models.clip import Clip
from app.models.driver import Driver
from app.models.truck import Truck
from app.schemas.clip import ClipDetail, ClipListResponse, ClipRow
from app.storage import (
    DEFAULT_SIGNED_URL_TTL_S,
    STREAM_TOKEN_PURPOSE,
    build_clip_key,
    get_playback_url,
)

_STREAM_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="invalid or missing credentials",
    headers={"WWW-Authenticate": "Bearer"},
)

router = APIRouter(tags=["clips"])

#: Closed set of audit actions the player UI may emit via
#: :func:`post_clip_audit`. Anything else is rejected with 400 so we don't
#: open the audit_log table up to arbitrary user-controlled action strings.
ALLOWED_CLIENT_AUDIT_ACTIONS: frozenset[str] = frozenset(
    {"clip.play", "clip.scrub", "clip.closed"}
)

#: Hard upper bound on page size, mirroring ``GET /audit``.
MAX_CLIPS_LIMIT: int = 200

#: Default page size when the client doesn't specify one.
DEFAULT_CLIPS_LIMIT: int = 50


# ---------------------------------------------------------------------------
# Cursor helpers — opaque base64-url JSON of ``(started_at, id)``.
# Pattern mirrors ``app.audit.encode_cursor`` / ``decode_cursor``. Kept
# inline (not extracted) since the schemas are small and the audit one
# encodes ``(occurred_at, int_id)`` whereas this one needs ``UUID`` ids.
# ---------------------------------------------------------------------------


def _encode_clip_cursor(started_at: datetime, clip_id: UUID) -> str:
    raw = json.dumps(
        {"s": started_at.isoformat(), "i": str(clip_id)}, separators=(",", ":")
    )
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_clip_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Inverse of :func:`_encode_clip_cursor`.

    Raises :class:`ValueError` on any garbage so the router can map to 400.
    """
    try:
        pad = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode((cursor + pad).encode("ascii"))
        obj = json.loads(raw.decode("utf-8"))
        return datetime.fromisoformat(obj["s"]), UUID(obj["i"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid cursor") from exc


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/clips", response_model=ClipListResponse)
async def list_clips(
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    truck_id: Annotated[UUID | None, Query()] = None,
    driver_id: Annotated[UUID | None, Query()] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    text: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = DEFAULT_CLIPS_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> ClipListResponse:
    """Return a page of clips for the caller's tenant.

    All filters AND together. The ``text`` filter joins trucks (and
    outer-joins drivers, since ``driver_id`` is nullable) so a single
    ILIKE pattern can match either the truck label or driver name.
    """
    capped_limit = min(limit, MAX_CLIPS_LIMIT)

    stmt = (
        select(Clip)
        .join(Truck, Truck.id == Clip.truck_id)
        .outerjoin(Driver, Driver.id == Clip.driver_id)
        .options(selectinload(Clip.truck), selectinload(Clip.driver))
        .where(Clip.tenant_id == principal.tenant_id)
    )

    if truck_id is not None:
        stmt = stmt.where(Clip.truck_id == truck_id)
    if driver_id is not None:
        stmt = stmt.where(Clip.driver_id == driver_id)
    if from_ is not None:
        stmt = stmt.where(Clip.started_at >= from_)
    if to is not None:
        stmt = stmt.where(Clip.started_at <= to)
    if text is not None and text != "":
        pattern = f"%{text}%"
        stmt = stmt.where(or_(Truck.label.ilike(pattern), Driver.name.ilike(pattern)))

    if cursor is not None:
        try:
            cursor_ts, cursor_id = _decode_clip_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid cursor",
            ) from exc
        # Strict "less than (started_at, id)" — newer rows already served
        # on prior pages are excluded.
        stmt = stmt.where(
            or_(
                Clip.started_at < cursor_ts,
                and_(Clip.started_at == cursor_ts, Clip.id < cursor_id),
            )
        )

    # Pull one extra row to detect whether another page exists without
    # an extra COUNT query.
    stmt = stmt.order_by(Clip.started_at.desc(), Clip.id.desc()).limit(capped_limit + 1)

    result = await session.execute(stmt)
    rows = list(result.scalars().all())

    has_more = len(rows) > capped_limit
    page = rows[:capped_limit]

    next_cursor: str | None = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_clip_cursor(last.started_at, last.id)

    return ClipListResponse(
        items=[ClipRow.model_validate(r) for r in page],
        next_cursor=next_cursor,
    )


# ---------------------------------------------------------------------------
# Upload endpoint (T20)
# ---------------------------------------------------------------------------

#: Chunk size used when streaming an uploaded file into memory for hashing
#: and into storage. 1 MiB strikes a balance between syscalls and peak
#: memory for the upload buffer.
_UPLOAD_CHUNK_BYTES: int = 1024 * 1024


@router.post("/clips/upload", response_model=ClipDetail)
async def upload_clip(
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    truck_id: Annotated[UUID, Form()],
    started_at: Annotated[datetime, Form()],
    file: Annotated[UploadFile, File(description="Clip bytes")],
    driver_id: Annotated[UUID | None, Form()] = None,
    duration_s: Annotated[int, Form(ge=0)] = 0,
) -> ClipDetail:
    """Accept a clip file from the browser upload modal.

    Multipart fields:
        * ``truck_id``: required UUID — must belong to caller's tenant.
        * ``driver_id``: optional UUID — must belong to caller's tenant if set.
        * ``started_at``: required ISO 8601 datetime (the clip's recording
          time; the storage key is derived from this).
        * ``duration_s``: integer ≥ 0 — duration in seconds; ``0`` means
          unknown (frontend can't always extract it).
        * ``file``: the MP4/MOV/MKV/M4V bytes.

    Behaviour:
        * Enforces ``settings.max_upload_bytes`` (default 1 GiB). Exceeding
          the cap — known up-front or detected mid-stream — returns
          ``413 Payload Too Large`` without leaving partial bytes on disk.
        * Hashes the body with SHA-256 while reading.
        * Writes through :func:`app.storage.put_object`, so local and s3
          backends are both supported.
        * Inserts a ``clips`` row, writes a ``clip.uploaded`` audit row,
          and commits everything atomically.

    Tenant isolation: ``truck_id`` / ``driver_id`` are validated against
    the caller's tenant. Cross-tenant references return ``404 not found``
    so the endpoint can't be used to probe other tenants' fleets.
    """
    # ---- Validate truck belongs to caller's tenant ----------------------
    truck_stmt = select(Truck).where(
        Truck.id == truck_id, Truck.tenant_id == principal.tenant_id
    )
    truck = (await session.execute(truck_stmt)).scalar_one_or_none()
    if truck is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="not found",
        )

    # ---- Validate driver belongs to caller's tenant (if provided) -------
    driver: Driver | None = None
    if driver_id is not None:
        driver_stmt = select(Driver).where(
            Driver.id == driver_id, Driver.tenant_id == principal.tenant_id
        )
        driver = (await session.execute(driver_stmt)).scalar_one_or_none()
        if driver is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="not found",
            )

    # ---- Size cap (cheap up-front check) --------------------------------
    max_bytes = settings.max_upload_bytes
    declared_size = file.size
    if declared_size is not None and declared_size > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="file too large",
        )

    # ---- Read into memory in chunks, hashing as we go -------------------
    # We accumulate into a single ``bytes`` buffer because the existing
    # ``storage.put_object`` API takes ``bytes | BinaryIO``; the local
    # backend writes it atomically, the s3 backend hands it to boto3.
    # Multi-megabyte clips fit comfortably in RAM; the cap (1 GiB) is the
    # absolute upper bound, and we abort early if exceeded.
    hasher = hashlib.sha256()
    total = 0
    chunks: list[bytes] = []
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            # Don't write partial bytes — bail out before touching storage.
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="file too large",
            )
        hasher.update(chunk)
        chunks.append(chunk)
    body_bytes = b"".join(chunks)
    sha256_hex = hasher.hexdigest()

    # ---- Materialise the clip --------------------------------------------
    clip_id = uuid4()
    storage_key = build_clip_key(principal.tenant_id, started_at, clip_id)
    content_type = file.content_type or "video/mp4"

    # Write to storage BEFORE the DB insert so that a storage failure
    # leaves us with no orphaned row. ``put_object`` re-checks the tenant
    # prefix as a defence-in-depth measure.
    await storage_module.put_object(
        principal.tenant_id,
        storage_key,
        body_bytes,
        content_type=content_type,
    )

    ended_at = started_at + timedelta(seconds=duration_s)
    clip = Clip(
        id=clip_id,
        tenant_id=principal.tenant_id,
        truck_id=truck.id,
        driver_id=driver.id if driver is not None else None,
        started_at=started_at,
        ended_at=ended_at,
        duration_s=duration_s,
        storage_key=storage_key,
        sha256=sha256_hex,
        dashcam_firmware=None,
    )
    session.add(clip)
    await session.flush()

    await audit_record(
        session,
        principal=principal,
        action="clip.uploaded",
        target_type="clip",
        target_id=clip_id,
        payload={
            "truck_id": str(truck_id),
            "driver_id": str(driver_id) if driver_id is not None else None,
            "file_size_bytes": total,
            "source": "web",
        },
    )
    await session.commit()

    # Re-load with relationships so ``ClipDetail`` can flatten truck.label
    # / driver.name. ``selectinload`` keeps it to one query.
    refresh_stmt = (
        select(Clip)
        .options(selectinload(Clip.truck), selectinload(Clip.driver))
        .where(Clip.id == clip_id, Clip.tenant_id == principal.tenant_id)
    )
    loaded = (await session.execute(refresh_stmt)).scalar_one()
    return ClipDetail.model_validate(loaded)


@router.get("/clips/{clip_id}", response_model=ClipDetail)
async def get_clip(
    clip_id: UUID,
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    play: Annotated[bool, Query()] = False,
) -> ClipDetail:
    """Return clip metadata, optionally with a freshly-minted playback URL.

    Tenant isolation: a clip whose ``tenant_id`` doesn't match the caller
    produces a ``404 not found`` — the same response as a truly missing
    row — so callers can't enumerate other tenants' ids.

    ``?play=true`` writes an audit entry **before** minting the signed
    URL, and commits the transaction at the end of the handler. This
    keeps audit + read in one unit-of-work: if the URL mint somehow
    fails, the audit row rolls back with it.
    """
    stmt = (
        select(Clip)
        .options(selectinload(Clip.truck), selectinload(Clip.driver))
        .where(Clip.id == clip_id, Clip.tenant_id == principal.tenant_id)
    )
    result = await session.execute(stmt)
    clip = result.scalar_one_or_none()
    if clip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="not found",
        )

    detail = ClipDetail.model_validate(clip)

    if play:
        # Audit FIRST so the row is part of the same transaction that
        # mints the URL. The flush inside ``audit_record`` ensures the
        # row hits the DB before we do any external work.
        await audit_record(
            session,
            principal=principal,
            action="clip.play_url_minted",
            target_type="clip",
            target_id=clip.id,
            payload={"signed_url_ttl_s": DEFAULT_SIGNED_URL_TTL_S},
        )
        url = await get_playback_url(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            key=clip.storage_key,
            clip_id=clip.id,
            expires_s=DEFAULT_SIGNED_URL_TTL_S,
        )
        await session.commit()
        # ``ClipDetail`` is a Pydantic model; ``model_copy`` keeps it immutable-ish.
        detail = detail.model_copy(update={"playback_url": url})

    return detail


# ---------------------------------------------------------------------------
# Local-mode playback streaming
# ---------------------------------------------------------------------------


def _verify_stream_token(token: str, clip_id: UUID, cfg: Settings) -> Principal:
    """Decode a ``?t=`` stream token and bind it to ``clip_id``.

    Validates: HS256 signature against ``cfg.jwt_secret``, expiry,
    ``purpose == "clip-stream"`` (defence-in-depth so a stream token can't
    impersonate a session JWT), and the ``clip_id`` claim against the URL
    path. The ``tenant_id`` claim is propagated into the returned
    :class:`Principal`; the SQL ``WHERE`` on ``tenant_id`` then enforces
    that the loaded clip actually belongs to the token's tenant. Any
    failure raises the same neutral 401 used elsewhere.
    """
    try:
        claims = jwt.decode(token, cfg.jwt_secret, algorithms=[cfg.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise _STREAM_CREDENTIALS_ERROR from exc
    except jwt.InvalidTokenError as exc:
        raise _STREAM_CREDENTIALS_ERROR from exc

    if claims.get("purpose") != STREAM_TOKEN_PURPOSE:
        raise _STREAM_CREDENTIALS_ERROR

    try:
        claim_clip = UUID(claims["clip_id"])
        claim_tenant = UUID(claims["tenant_id"])
        claim_user = UUID(claims["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _STREAM_CREDENTIALS_ERROR from exc

    if claim_clip != clip_id:
        raise _STREAM_CREDENTIALS_ERROR

    # Build a minimal Principal. ``roles``, ``email``, ``name`` aren't
    # consulted by the stream endpoint; the load-by-tenant SQL is the
    # actual authz check.
    return Principal(
        user_id=claim_user,
        tenant_id=claim_tenant,
        roles=[],
        email="",
        name="",
    )


@router.get("/clips/{clip_id}/stream")
async def stream_clip(
    clip_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    cfg: Annotated[Settings, Depends(get_settings)],
) -> FileResponse:
    """Stream a clip's MP4 bytes from the local filesystem.

    Used in ``storage_backend="local"`` mode in place of a signed S3 URL.
    Tenant scoping happens twice: once via the SQL ``WHERE`` (so a clip
    belonging to another tenant returns 404), and again as a defence-in-depth
    path-traversal check after resolving the on-disk path.

    Two credential paths are accepted:

    * **``?t=<jwt>``** — short-lived HS256 token minted by
      :func:`app.storage._mint_stream_token`. This is what cross-origin
      ``<video>`` elements use, because the browser won't attach the
      ``Authorization`` / ``X-Dev-*`` headers on a media fetch.
    * **No ``?t=``** — falls back to :func:`app.auth.current_user`
      (Bearer JWT or dev headers). Curl + dev headers keeps working for
      ops/debug.

    No audit row is written here — the browser may issue many HTTP Range
    requests per playback, and ``clip.play_url_minted`` (written by the
    sibling ``GET /clips/{id}?play=true``) already records the playback
    intent. The player-side ``POST /clips/{id}/audit`` captures ``clip.play``
    and ``clip.scrub`` events with finer granularity.

    Returns:
        A :class:`FastAPI.FileResponse` with ``media_type="video/mp4"``.
        Starlette adds ``Accept-Ranges: bytes`` and handles ``Range``
        requests natively, which is what the ``<video>`` element needs
        to scrub.
    """
    token = request.query_params.get("t")
    if token:
        principal = _verify_stream_token(token, clip_id, cfg)
    else:
        # Manual delegation to the dependency: we want either credential
        # to work, so we can't list ``current_user`` as a hard ``Depends``
        # without breaking the token-only path. Reconstruct the Bearer
        # credentials from the raw header so curl + JWT keeps working.
        auth_header = request.headers.get("Authorization") or ""
        credentials: HTTPAuthorizationCredentials | None = None
        if auth_header.lower().startswith("bearer "):
            credentials = HTTPAuthorizationCredentials(
                scheme="Bearer", credentials=auth_header[7:].strip()
            )
        principal = current_user(
            request=request, credentials=credentials, cfg=cfg
        )

    stmt = select(Clip).where(
        Clip.id == clip_id, Clip.tenant_id == principal.tenant_id
    )
    result = await session.execute(stmt)
    clip = result.scalar_one_or_none()
    if clip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="not found",
        )

    # Resolve the on-disk path. We resolve *without* following symlinks
    # because the seed creates each clip target as a symlink into the
    # repo's ``samples/`` directory — following links here would push the
    # final path outside ``STORAGE_ROOT`` and trip the containment check
    # below for legitimate clips. ``os.path.normpath`` collapses ``..``
    # segments so a malformed key like ``{tenant}/../../etc`` still gets
    # caught by the containment assertion.
    root = Path(settings.storage_root).resolve()
    # Use ``os.path.normpath`` to flatten ``..`` traversal without resolving
    # symlinks. Then re-wrap in ``Path`` for the containment + existence checks.
    raw = root / clip.storage_key
    candidate = Path(os.path.normpath(raw))

    # Defence-in-depth: the tenant-prefix check on ``storage_key`` already
    # prevents this in practice, but if a malformed key ever slipped in
    # (e.g. via a future ingest path that forgot to validate), this
    # ensures we never serve a file outside the storage root.
    if not _is_relative_to(candidate, root):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="not found",
        )

    # ``exists()`` follows symlinks, which is what we want — a clip whose
    # symlink target was removed is effectively missing.
    if not candidate.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="not found",
        )

    return FileResponse(
        path=str(candidate),
        media_type="video/mp4",
        filename=f"{clip.id}.mp4",
    )


def _is_relative_to(child: Path, parent: Path) -> bool:
    """Backport of ``Path.is_relative_to`` (added in 3.9) for clarity.

    ``Path.is_relative_to`` exists on 3.9+, but we wrap it so the call
    site reads as a security check rather than a path manipulation.
    """
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# Player-emitted audit endpoint
# ---------------------------------------------------------------------------


class ClipAuditRequest(BaseModel):
    """Body schema for ``POST /clips/{id}/audit``.

    The frontend can't write audit rows directly — it has no database
    handle, and we wouldn't want it to: tenant scoping, the
    actor_user_id, and the row shape all need to be authoritative. This
    endpoint is the controlled wrapper that lets the video player emit a
    tightly-scoped set of player-lifecycle audit events.

    ``action`` is restricted to :data:`ALLOWED_CLIENT_AUDIT_ACTIONS`.
    Anything else is rejected with 400 by :func:`post_clip_audit` so we
    don't open the audit_log table up to arbitrary user-controlled action
    strings (e.g. the frontend can't fabricate a ``clip.deleted`` row).
    """

    action: Literal["clip.play", "clip.scrub", "clip.closed"]
    payload: dict[str, Any] | None = Field(default=None)


@router.post(
    "/clips/{clip_id}/audit",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def post_clip_audit(
    clip_id: UUID,
    body: ClipAuditRequest,
    principal: Annotated[Principal, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Append one player-lifecycle audit row.

    Used by the video-player page (T12) to record:

    * ``clip.play`` — fired once when playback actually starts (we already
      log ``clip.play_url_minted`` on the GET that mints the signed URL;
      this captures the user actually hitting play).
    * ``clip.scrub`` — fired on seek, debounced client-side to once per
      750 ms so we don't flood the table.
    * ``clip.closed`` — fired on unmount with the accumulated view
      duration in the payload.

    Tenant isolation: a clip whose ``tenant_id`` doesn't match the
    caller's produces a ``404 not found`` — the same response as a truly
    missing row — so callers can't enumerate other tenants' ids by
    probing the audit endpoint either.
    """
    if body.action not in ALLOWED_CLIENT_AUDIT_ACTIONS:
        # Pydantic's ``Literal`` already enforces this for well-formed
        # requests, but defence-in-depth: if the schema is ever relaxed
        # the runtime check still holds the contract.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="unsupported action",
        )

    stmt = select(Clip.id).where(
        Clip.id == clip_id, Clip.tenant_id == principal.tenant_id
    )
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="not found",
        )

    await audit_record(
        session,
        principal=principal,
        action=body.action,
        target_type="clip",
        target_id=clip_id,
        payload=body.payload if body.payload is not None else {},
    )
    await session.commit()
