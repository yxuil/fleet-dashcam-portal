"""Integration + unit tests for ``app.storage``.

The module supports two backends: ``local`` (files on disk under
``settings.storage_root``) and ``s3`` (MinIO/S3 via boto3). Tests are
grouped accordingly:

* Pure helpers (``build_clip_key``, ``_validate_tenant_prefix``) — no I/O.
* ``put_object`` / ``ensure_bucket`` in local mode — write to a tmp dir.
* ``get_playback_url`` — both modes, parametrized.
* ``get_signed_url`` integration — requires MinIO; skipped if unreachable.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
import pytest

from app import storage as storage_module
from app.config import settings
from app.storage import (
    DEFAULT_SIGNED_URL_TTL_S,
    MAX_SIGNED_URL_TTL_S,
    _validate_tenant_prefix,
    build_clip_key,
    ensure_bucket,
    get_playback_url,
    get_s3_client,
    get_signed_url,
    put_object,
)

# ---------------------------------------------------------------------------
# Reachability gate — s3 integration tests depend on this.
# ---------------------------------------------------------------------------


def _minio_reachable() -> bool:
    """Best-effort TCP probe of the S3 endpoint host/port.

    We deliberately don't go through boto3 here — we just want to know if
    *something* is listening. The real client will produce a more useful
    error if MinIO is up but misconfigured.
    """
    parsed = urlparse(settings.s3_endpoint)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


_MINIO_REACHABLE = _minio_reachable()

# S3 integration tests require BOTH the s3 backend selected AND MinIO up.
# Either gate alone isn't enough: with backend=local the s3 helper paths
# are deliberately unreachable from public funcs, and with MinIO down boto3
# would error out before any tenant check we want to exercise.
requires_s3 = pytest.mark.skipif(
    not (settings.storage_backend == "s3" and _MINIO_REACHABLE),
    reason=(
        f"Requires STORAGE_BACKEND=s3 (got {settings.storage_backend!r}) and "
        f"MinIO reachable at {settings.s3_endpoint}"
    ),
)


@pytest.fixture
def local_storage_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Point ``settings.storage_root`` at a fresh tmp dir for local-mode tests.

    We also force ``storage_backend="local"`` so a test running in an
    environment that defaults to s3 still exercises the local path.
    """
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    yield tmp_path


# ---------------------------------------------------------------------------
# Pure helper tests (no I/O)
# ---------------------------------------------------------------------------


def test_build_clip_key_layout() -> None:
    tenant_id = UUID("00000000-0000-0000-0000-000000000001")
    clip_id = UUID("00000000-0000-0000-0000-0000000000aa")
    started_at = datetime(2026, 3, 7, 12, 34, 56, tzinfo=UTC)

    key = build_clip_key(tenant_id, started_at, clip_id)

    assert key == (
        "00000000-0000-0000-0000-000000000001/"
        "2026/03/07/"
        "00000000-0000-0000-0000-0000000000aa.mp4"
    )


def test_build_clip_key_zero_pads_month_and_day() -> None:
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    clip_id = UUID("22222222-2222-2222-2222-222222222222")
    started_at = datetime(2026, 1, 5, 0, 0, 0, tzinfo=UTC)

    key = build_clip_key(tenant_id, started_at, clip_id)

    # Single-digit month/day must be zero-padded for lexicographic ordering.
    assert "/2026/01/05/" in key


def test_validate_tenant_prefix_accepts_correct_prefix() -> None:
    tenant_id = uuid4()
    key = f"{tenant_id}/2026/06/29/{uuid4()}.mp4"
    # Should not raise.
    _validate_tenant_prefix(tenant_id, key)


def test_validate_tenant_prefix_rejects_cross_tenant_key() -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    key = f"{tenant_b}/2026/06/29/{uuid4()}.mp4"
    with pytest.raises(ValueError, match="does not belong to caller's tenant"):
        _validate_tenant_prefix(tenant_a, key)


def test_validate_tenant_prefix_rejects_bare_tenant_id_no_slash() -> None:
    """A key that *starts with* the tenant id but isn't followed by ``/``
    must be rejected — e.g. ``{tenant_a}-evil/...`` could otherwise sneak
    through a naive ``startswith`` check.
    """
    tenant_a = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    key = f"{tenant_a}-evil/2026/06/29/file.mp4"
    with pytest.raises(ValueError):
        _validate_tenant_prefix(tenant_a, key)


def test_validate_tenant_prefix_rejects_empty_key() -> None:
    with pytest.raises(ValueError):
        _validate_tenant_prefix(uuid4(), "")


# ---------------------------------------------------------------------------
# Input-validation tests for get_signed_url (no network needed —
# validation runs before the boto3 call).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_signed_url_rejects_ttl_above_max() -> None:
    tenant_id = uuid4()
    key = f"{tenant_id}/2026/06/29/{uuid4()}.mp4"
    with pytest.raises(ValueError, match="exceeds MAX_SIGNED_URL_TTL_S"):
        await get_signed_url(tenant_id, key, expires_s=MAX_SIGNED_URL_TTL_S + 1)


@pytest.mark.asyncio
async def test_get_signed_url_rejects_zero_ttl() -> None:
    tenant_id = uuid4()
    key = f"{tenant_id}/2026/06/29/{uuid4()}.mp4"
    with pytest.raises(ValueError, match="must be positive"):
        await get_signed_url(tenant_id, key, expires_s=0)


@pytest.mark.asyncio
async def test_get_signed_url_rejects_negative_ttl() -> None:
    tenant_id = uuid4()
    key = f"{tenant_id}/2026/06/29/{uuid4()}.mp4"
    with pytest.raises(ValueError, match="must be positive"):
        await get_signed_url(tenant_id, key, expires_s=-1)


@pytest.mark.asyncio
async def test_get_signed_url_rejects_cross_tenant_without_network() -> None:
    """Cross-tenant signing must be refused before any network call."""
    tenant_a = uuid4()
    tenant_b = uuid4()
    key_for_b = f"{tenant_b}/2026/06/29/{uuid4()}.mp4"
    with pytest.raises(ValueError, match="does not belong to caller's tenant"):
        await get_signed_url(tenant_a, key_for_b)


@pytest.mark.asyncio
async def test_put_object_rejects_cross_tenant_without_network(
    local_storage_root: Path,
) -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    key_for_b = f"{tenant_b}/2026/06/29/{uuid4()}.mp4"
    with pytest.raises(ValueError, match="does not belong to caller's tenant"):
        await put_object(tenant_a, key_for_b, b"payload")


# ---------------------------------------------------------------------------
# Local-mode tests (no network)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_put_object_writes_file_under_storage_root(
    local_storage_root: Path,
) -> None:
    """Local-mode put_object lands the bytes under STORAGE_ROOT/key."""
    tenant_id = uuid4()
    clip_id = uuid4()
    key = build_clip_key(
        tenant_id, datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC), clip_id
    )
    payload = b"local-clip-bytes-" + clip_id.bytes

    await put_object(tenant_id, key, payload, content_type="video/mp4")

    target = local_storage_root / key
    assert target.is_file()
    assert target.read_bytes() == payload


@pytest.mark.asyncio
async def test_local_put_object_rejects_cross_tenant_prefix(
    local_storage_root: Path,
) -> None:
    """Local-mode still enforces the tenant-prefix check before touching disk."""
    tenant_a = uuid4()
    tenant_b = uuid4()
    key_for_b = f"{tenant_b}/2026/06/29/{uuid4()}.mp4"

    with pytest.raises(ValueError, match="does not belong to caller's tenant"):
        await put_object(tenant_a, key_for_b, b"payload")

    # Nothing should have been written under either tenant's prefix.
    assert not (local_storage_root / key_for_b).exists()


@pytest.mark.asyncio
async def test_local_ensure_bucket_creates_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In local mode, ensure_bucket mkdirs STORAGE_ROOT (parents=True)."""
    root = tmp_path / "deeply" / "nested" / "clips"
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_root", root)

    assert not root.exists()
    await ensure_bucket()
    assert root.is_dir()
    # Idempotent.
    await ensure_bucket()
    assert root.is_dir()


# ---------------------------------------------------------------------------
# get_playback_url — both modes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_playback_url_local_mode_returns_stream_route(
    local_storage_root: Path,
) -> None:
    """Local-mode playback URL is the relative router path; no signing."""
    tenant_id = uuid4()
    clip_id = uuid4()
    key = build_clip_key(
        tenant_id, datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC), clip_id
    )

    url = await get_playback_url(tenant_id=tenant_id, key=key, clip_id=clip_id)
    assert url == f"/clips/{clip_id}/stream"


@pytest.mark.asyncio
async def test_get_playback_url_local_mode_rejects_cross_tenant(
    local_storage_root: Path,
) -> None:
    """Cross-tenant prefix is still rejected even though local mode doesn't sign."""
    tenant_a = uuid4()
    tenant_b = uuid4()
    clip_id = uuid4()
    key_for_b = f"{tenant_b}/2026/06/29/{clip_id}.mp4"
    with pytest.raises(ValueError, match="does not belong to caller's tenant"):
        await get_playback_url(tenant_id=tenant_a, key=key_for_b, clip_id=clip_id)


@pytest.mark.asyncio
async def test_get_playback_url_s3_mode_delegates_to_signed_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S3-mode playback URL routes through ``get_signed_url`` with the same args."""
    monkeypatch.setattr(settings, "storage_backend", "s3")

    sentinel = "https://signed.example.test/x.mp4?sig=stub"
    seen: dict[str, object] = {}

    async def fake_signed(
        tenant_id: UUID, key: str, expires_s: int = DEFAULT_SIGNED_URL_TTL_S
    ) -> str:
        seen["tenant_id"] = tenant_id
        seen["key"] = key
        seen["expires_s"] = expires_s
        return sentinel

    monkeypatch.setattr(storage_module, "get_signed_url", fake_signed)

    tenant_id = uuid4()
    clip_id = uuid4()
    key = build_clip_key(
        tenant_id, datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC), clip_id
    )

    url = await get_playback_url(
        tenant_id=tenant_id, key=key, clip_id=clip_id, expires_s=123
    )

    assert url == sentinel
    assert seen == {"tenant_id": tenant_id, "key": key, "expires_s": 123}


# ---------------------------------------------------------------------------
# S3 integration tests against real MinIO (skipped unless backend=s3 + MinIO up).
# ---------------------------------------------------------------------------


@requires_s3
@pytest.mark.asyncio
async def test_ensure_bucket_is_idempotent() -> None:
    # The compose ``minio-init`` job already created the bucket; calling
    # ensure_bucket twice must not raise.
    await ensure_bucket()
    await ensure_bucket()


@requires_s3
@pytest.mark.asyncio
async def test_put_then_signed_url_roundtrip() -> None:
    """End-to-end: upload bytes, sign a URL, GET it, verify body."""
    await ensure_bucket()

    tenant_id = uuid4()
    clip_id = uuid4()
    # Unique per test run so concurrent runs don't collide.
    key = f"{tenant_id}/test/{clip_id}/sample.mp4"
    payload = b"hello-dashcam-" + clip_id.bytes

    try:
        await put_object(tenant_id, key, payload, content_type="video/mp4")

        url = await get_signed_url(tenant_id, key, expires_s=60)
        assert isinstance(url, str)
        # Sanity-check: presigned URL should carry SigV4 query params.
        assert "X-Amz-Signature=" in url
        assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url

        async with httpx.AsyncClient() as http:
            resp = await http.get(url)
        assert resp.status_code == 200, resp.text
        assert resp.content == payload
    finally:
        # Direct boto3 cleanup — we intentionally don't expose
        # delete_object in the public API, so use the client we built.
        client = get_s3_client()
        client.delete_object(Bucket=settings.s3_bucket, Key=key)


@requires_s3
@pytest.mark.asyncio
async def test_signed_url_default_ttl_is_one_hour() -> None:
    """Default TTL constant is documented as 1h; verify the constant
    matches the presigned URL's X-Amz-Expires query parameter when no
    explicit TTL is passed."""
    await ensure_bucket()
    tenant_id = uuid4()
    key = f"{tenant_id}/test/{uuid4()}/never-uploaded.mp4"
    # We don't need the object to exist to sign a URL — signing is purely
    # a local credential operation.
    url = await get_signed_url(tenant_id, key)
    assert f"X-Amz-Expires={DEFAULT_SIGNED_URL_TTL_S}" in url


@requires_s3
@pytest.mark.asyncio
async def test_cross_tenant_signing_raises_against_real_backend() -> None:
    """Repeats the unit-level cross-tenant check, but with the real
    backend available — guarantees the check fires *before* any network
    activity even when boto3 would otherwise succeed."""
    await ensure_bucket()

    tenant_a = uuid4()
    tenant_b = uuid4()
    clip_id = uuid4()
    key_for_b = f"{tenant_b}/test/{clip_id}/sample.mp4"

    # Seed a real object under tenant B (so an unscoped signer would
    # actually produce a working URL — making the check meaningful).
    payload = b"tenant-b-only"
    try:
        await put_object(tenant_b, key_for_b, payload)

        with pytest.raises(ValueError, match="does not belong to caller's tenant"):
            await get_signed_url(tenant_a, key_for_b)
    finally:
        client = get_s3_client()
        client.delete_object(Bucket=settings.s3_bucket, Key=key_for_b)
