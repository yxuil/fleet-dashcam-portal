"""S3/MinIO storage adapter with tenant-scoped keys.

This module is the single security boundary between the portal and object
storage. Every public function takes a ``tenant_id`` and verifies that the
S3 key it is about to touch lives under that tenant's prefix. Cross-tenant
operations raise :class:`ValueError` and never reach the wire.

Public surface:
    * :func:`put_object` — upload bytes/file-like to ``s3://{bucket}/{key}``
    * :func:`get_signed_url` — presigned GET URL with bounded TTL
    * :func:`ensure_bucket` — create the configured bucket if missing
    * :func:`build_clip_key` — canonical key layout helper

Boto3 is synchronous; we wrap every blocking call in
:func:`anyio.to_thread.run_sync` so async FastAPI handlers don't stall the
event loop.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import TYPE_CHECKING, BinaryIO
from uuid import UUID

import anyio.to_thread
import boto3
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
# Client factory
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
    """Return the canonical S3 key for a clip.

    Layout: ``{tenant_id}/{yyyy}/{mm}/{dd}/{clip_id}.mp4``. Date components
    come from ``started_at`` (the clip's start timestamp), in whatever
    timezone the caller chose — typically UTC. We don't enforce a tz here
    because the key is opaque to S3 and we want callers to pick their
    bucket sharding strategy explicitly.
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


# ---------------------------------------------------------------------------
# Bucket lifecycle
# ---------------------------------------------------------------------------


def _ensure_bucket_sync() -> None:
    """Create the configured bucket if it doesn't already exist."""
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


async def ensure_bucket() -> None:
    """Ensure the configured bucket exists. Safe to call repeatedly."""
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


async def put_object(
    tenant_id: UUID,
    key: str,
    body: Body,
    content_type: str = "video/mp4",
) -> None:
    """Upload ``body`` to ``s3://{bucket}/{key}``.

    Args:
        tenant_id: Caller's tenant; ``key`` must live under this prefix.
        key: Full object key. Use :func:`build_clip_key` to construct.
        body: Bytes or a binary file-like object.
        content_type: MIME type stored as object metadata; defaults to
            ``video/mp4`` since clips are the primary workload.

    Raises:
        ValueError: If ``key`` is not under ``{tenant_id}/``.
        botocore.exceptions.ClientError: On S3-side failures (network,
            credentials, permissions). Callers should let these bubble up
            to FastAPI's error handler unless they have a recovery path.
    """
    _validate_tenant_prefix(tenant_id, key)
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
    """Return a presigned GET URL for ``key``, valid for ``expires_s`` seconds.

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
