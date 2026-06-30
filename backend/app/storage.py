"""Storage adapter — local filesystem or S3/MinIO, gated on settings.

This module is the single security boundary between the portal and clip
storage. Every public function takes a ``tenant_id`` and verifies that the
storage key it is about to touch lives under that tenant's prefix.
Cross-tenant operations raise :class:`ValueError` and never reach the wire
(or the disk).

Two backends are supported, chosen via ``settings.storage_backend``:

* ``"local"`` (default): clips live under ``settings.storage_root`` and
  playback is served by the in-process ``GET /clips/{id}/stream`` route.
* ``"s3"``: clips live in MinIO/S3 and playback is a SigV4 presigned URL.

Public surface:
    * :func:`put_object` — write clip bytes (disk or S3).
    * :func:`get_playback_url` — return a URL the player can hit; either
      ``"/clips/{id}/stream"`` (local) or a presigned S3 URL.
    * :func:`get_signed_url` — presigned S3 GET URL with bounded TTL.
      Only safe to call when ``storage_backend == "s3"``.
    * :func:`ensure_bucket` — create the bucket or storage_root directory.
    * :func:`build_clip_key` — canonical key layout helper.

Boto3 is synchronous; we wrap blocking calls in
:func:`anyio.to_thread.run_sync` so async FastAPI handlers don't stall.
"""

from __future__ import annotations

import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO
from uuid import UUID

import anyio.to_thread
import boto3
import jwt
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import settings

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client
else:
    S3Client = object  # type: ignore[assignment,misc]


#: Maximum allowed TTL for a presigned URL (6 hours). Requests above this
#: are rejected with :class:`ValueError`; this caps the blast radius of a
#: leaked URL.
MAX_SIGNED_URL_TTL_S: int = 6 * 3600

#: Default presigned URL TTL (1 hour). Suitable for short-lived player
#: sessions; reviewers can re-fetch if expired.
DEFAULT_SIGNED_URL_TTL_S: int = 3600


# ---------------------------------------------------------------------------
# Client factory (s3 mode only)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_s3_client() -> S3Client:
    """Return a memoized boto3 S3 client wired to MinIO via settings.

    MinIO requires SigV4 for presigned URLs, so we force
    ``signature_version="s3v4"``. The client is cached at module scope; if
    you need to swap settings in tests, call ``get_s3_client.cache_clear()``.
    """
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4"),
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def build_clip_key(tenant_id: UUID, started_at: datetime, clip_id: UUID) -> str:
    """Return the canonical storage key for a clip.

    Layout: ``{tenant_id}/{yyyy}/{mm}/{dd}/{clip_id}.mp4``. Date components
    come from ``started_at`` (the clip's start timestamp), in whatever
    timezone the caller chose — typically UTC. The same layout is used
    for both S3 keys and on-disk paths under ``storage_root``.
    """
    return (
        f"{tenant_id}/"
        f"{started_at.year:04d}/{started_at.month:02d}/{started_at.day:02d}/"
        f"{clip_id}.mp4"
    )


def _validate_tenant_prefix(tenant_id: UUID, key: str) -> None:
    """Raise ``ValueError`` if ``key`` is not under the tenant's prefix.

    This is the only thing standing between an authenticated caller from
    tenant A and a presigned URL for tenant B's clip. Keep it strict: the
    key must start with ``"{tenant_id}/"``. Bare ``tenant_id`` (no slash)
    or any other prefix is rejected.
    """
    expected_prefix = f"{tenant_id}/"
    if not key.startswith(expected_prefix):
        raise ValueError("storage key does not belong to caller's tenant")


def _storage_root() -> Path:
    """Return ``settings.storage_root`` as a ``Path`` (accept ``str`` for env)."""
    root = settings.storage_root
    if not isinstance(root, Path):
        root = Path(root)
    return root


# ---------------------------------------------------------------------------
# Bucket / root lifecycle
# ---------------------------------------------------------------------------


def _ensure_bucket_sync() -> None:
    """Create the configured S3 bucket if it doesn't already exist."""
    client = get_s3_client()
    bucket = settings.s3_bucket
    try:
        client.head_bucket(Bucket=bucket)
        return
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        # 404 / NoSuchBucket → create; anything else → propagate.
        if code not in {"404", "NoSuchBucket", "NoSuchKey"}:
            raise
    client.create_bucket(Bucket=bucket)


def _ensure_local_root_sync() -> None:
    """Create the local storage root if it doesn't already exist."""
    _storage_root().mkdir(parents=True, exist_ok=True)


async def ensure_bucket() -> None:
    """Ensure the storage backend is ready to accept writes.

    In ``local`` mode this creates ``storage_root``; in ``s3`` mode it
    creates the configured bucket. Safe to call repeatedly.
    """
    if settings.storage_backend == "local":
        await anyio.to_thread.run_sync(_ensure_local_root_sync)
        return
    await anyio.to_thread.run_sync(_ensure_bucket_sync)


# ---------------------------------------------------------------------------
# Object operations
# ---------------------------------------------------------------------------


Body = bytes | BinaryIO


def _put_object_sync(key: str, body: Body, content_type: str) -> None:
    client = get_s3_client()
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
    )


def _put_object_local_sync(key: str, body: Body) -> None:
    """Write ``body`` to ``storage_root/key``, creating parent dirs.

    ``content_type`` is ignored on disk — the stream endpoint sets it
    statically to ``video/mp4`` since clips are the only payload here.
    """
    target = _storage_root() / key
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(body, bytes):
        target.write_bytes(body)
    else:
        # File-like: read in chunks to avoid loading huge files into memory.
        with target.open("wb") as f:
            while True:
                chunk = body.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)


async def put_object(
    tenant_id: UUID,
    key: str,
    body: Body,
    content_type: str = "video/mp4",
) -> None:
    """Write ``body`` under ``key``, scoped to ``tenant_id``.

    Args:
        tenant_id: Caller's tenant; ``key`` must live under this prefix.
        key: Full storage key. Use :func:`build_clip_key` to construct.
        body: Bytes or a binary file-like object.
        content_type: MIME type stored as object metadata (s3 mode only;
            local mode infers from the file extension at read time).

    Raises:
        ValueError: If ``key`` is not under ``{tenant_id}/``.
        botocore.exceptions.ClientError: S3-side failures (s3 mode).
        OSError: Disk failures (local mode).
    """
    _validate_tenant_prefix(tenant_id, key)
    if settings.storage_backend == "local":
        await anyio.to_thread.run_sync(_put_object_local_sync, key, body)
        return
    await anyio.to_thread.run_sync(_put_object_sync, key, body, content_type)


def _get_signed_url_sync(key: str, expires_s: int) -> str:
    client = get_s3_client()
    url: str = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=expires_s,
    )
    return url


async def get_signed_url(
    tenant_id: UUID,
    key: str,
    expires_s: int = DEFAULT_SIGNED_URL_TTL_S,
) -> str:
    """Return a presigned S3 GET URL for ``key``.

    Only meaningful when ``settings.storage_backend == "s3"``. The router
    no longer calls this directly; it goes through :func:`get_playback_url`
    which delegates here in s3 mode.

    Args:
        tenant_id: Caller's tenant; ``key`` must live under this prefix.
        key: Full object key.
        expires_s: TTL in seconds, in ``(0, MAX_SIGNED_URL_TTL_S]``.

    Raises:
        ValueError: If the key is not under the caller's tenant prefix, or
            if ``expires_s`` is out of range.
    """
    if expires_s <= 0:
        raise ValueError("expires_s must be positive")
    if expires_s > MAX_SIGNED_URL_TTL_S:
        raise ValueError(
            f"expires_s={expires_s} exceeds MAX_SIGNED_URL_TTL_S={MAX_SIGNED_URL_TTL_S}"
        )
    _validate_tenant_prefix(tenant_id, key)
    return await anyio.to_thread.run_sync(_get_signed_url_sync, key, expires_s)


#: Literal ``purpose`` claim placed on local-mode stream tokens. The
#: ``current_user`` JWT path rejects any token carrying this purpose so a
#: stream token can never accidentally satisfy session auth.
STREAM_TOKEN_PURPOSE: str = "clip-stream"


def _mint_stream_token(
    *,
    user_id: UUID,
    tenant_id: UUID,
    clip_id: UUID,
    expires_s: int,
) -> str:
    """Mint an HS256 JWT that authorises a single clip's bytes for ``expires_s``.

    Used only in local mode: cross-origin ``<video>`` elements can't send
    the dev-headers or ``Authorization`` header, so the token rides in the
    query string. The token is signed with ``settings.jwt_secret`` (same
    secret as session JWTs); the stream endpoint verifies the signature,
    the ``clip_id`` claim against the URL path, and the ``tenant_id`` claim
    against the loaded clip. The ``purpose`` claim is a defence-in-depth
    tag so this token can't be replayed as a session JWT.
    """
    now = int(time.time())
    claims = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "clip_id": str(clip_id),
        "iat": now,
        "exp": now + expires_s,
        "purpose": STREAM_TOKEN_PURPOSE,
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


async def get_playback_url(
    *,
    tenant_id: UUID,
    user_id: UUID,
    key: str,
    clip_id: UUID,
    expires_s: int = DEFAULT_SIGNED_URL_TTL_S,
) -> str:
    """Return a URL the player can use to fetch ``clip_id``'s bytes.

    Mode dispatch:

    * ``local`` → ``"/clips/{clip_id}/stream?t={jwt}"`` — a relative route
      on the backend that serves the file with HTTP Range support. The
      ``?t=`` query carries a short-lived HS256 JWT so cross-origin
      ``<video>`` elements (which can't attach custom auth headers) can
      authenticate by URL alone. See :func:`_mint_stream_token`.
    * ``s3`` → a SigV4 presigned GET URL via :func:`get_signed_url`. The
      ``user_id`` argument is unused in this mode (S3 carries its own
      signed-URL auth).

    The tenant-prefix check still runs in both branches, so a router that
    accidentally passed a cross-tenant key would be refused before the URL
    is constructed.

    Args:
        tenant_id: Caller's tenant; ``key`` must live under this prefix.
        user_id: Caller's user id — embedded in the local-mode token's
            ``sub`` claim for audit attribution. Ignored in s3 mode.
        key: Canonical storage key (see :func:`build_clip_key`).
        clip_id: Clip UUID — used in local mode to build the route and to
            bind the stream token to a single clip.
        expires_s: TTL in seconds, in ``(0, MAX_SIGNED_URL_TTL_S]``.
            Forwarded to :func:`get_signed_url` in s3 mode; used as the
            token's ``exp`` in local mode.

    Raises:
        ValueError: If ``key`` is not under the caller's tenant prefix, or
            if ``expires_s`` is out of range.
    """
    if expires_s <= 0:
        raise ValueError("expires_s must be positive")
    if expires_s > MAX_SIGNED_URL_TTL_S:
        raise ValueError(
            f"expires_s={expires_s} exceeds MAX_SIGNED_URL_TTL_S={MAX_SIGNED_URL_TTL_S}"
        )
    _validate_tenant_prefix(tenant_id, key)
    if settings.storage_backend == "local":
        token = _mint_stream_token(
            user_id=user_id,
            tenant_id=tenant_id,
            clip_id=clip_id,
            expires_s=expires_s,
        )
        return f"/clips/{clip_id}/stream?t={token}"
    return await get_signed_url(tenant_id, key, expires_s=expires_s)
