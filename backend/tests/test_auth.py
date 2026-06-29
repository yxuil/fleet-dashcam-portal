"""Unit + integration tests for ``app.auth`` and ``GET /me``.

These tests do not require a database. Settings are swapped per-test via
``app.dependency_overrides[get_settings]`` so we can flip ``app_env``
without touching real environment variables.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
import pytest_asyncio
from fastapi import HTTPException, Request

from app.auth import Principal, _verify_jwt, current_user, get_settings
from app.config import Settings
from app.main import app

# A single secret used across the JWT-signing tests; matches `_settings_for`
# below.
_TEST_SECRET = "unit-test-secret"  # noqa: S105 - test fixture, not production


def _settings_for(app_env: str = "prod", secret: str = _TEST_SECRET) -> Settings:
    return Settings(
        app_env=app_env,
        jwt_secret=secret,
        jwt_algorithm="HS256",
    )


def _make_token(
    *,
    secret: str = _TEST_SECRET,
    algorithm: str = "HS256",
    user_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    roles: list[str] | None = None,
    email: str = "user@example.com",
    name: str = "Real User",
    exp_delta: timedelta = timedelta(minutes=5),
    extra: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build a JWT and return ``(token, claims)``."""
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": str(user_id or uuid.uuid4()),
        "tenant_id": str(tenant_id or uuid.uuid4()),
        "roles": roles if roles is not None else ["viewer", "reviewer"],
        "email": email,
        "name": name,
        "iat": int(now.timestamp()),
        "exp": int((now + exp_delta).timestamp()),
    }
    if extra:
        claims.update(extra)
    token = jwt.encode(claims, secret, algorithm=algorithm)
    return token, claims


@pytest.fixture
def override_settings() -> Iterator[Any]:
    """Helper to swap ``get_settings`` for the FastAPI app under test."""

    def _apply(cfg: Settings) -> None:
        app.dependency_overrides[get_settings] = lambda: cfg

    yield _apply
    app.dependency_overrides.pop(get_settings, None)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Unit tests for the helpers
# ---------------------------------------------------------------------------


def _make_request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    """Build a minimal Starlette/FastAPI Request for direct dep testing."""
    scope: dict[str, Any] = {
        "type": "http",
        "headers": headers or [],
    }
    return Request(scope)


def test_dev_mode_headers_mint_principal() -> None:
    """In dev mode, X-Dev-* headers should yield a synthetic principal."""
    cfg = _settings_for(app_env="dev")
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    request = _make_request(
        [
            (b"x-dev-user-id", str(user_id).encode()),
            (b"x-dev-tenant-id", str(tenant_id).encode()),
        ]
    )

    principal = current_user(request=request, credentials=None, cfg=cfg)

    assert principal.user_id == user_id
    assert principal.tenant_id == tenant_id
    assert principal.roles == ["viewer"]
    assert principal.email == f"{user_id}@dev.local"
    assert principal.name == "Dev User"


def test_valid_jwt_returns_principal() -> None:
    cfg = _settings_for()
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    token, _ = _make_token(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=["admin"],
        email="ada@example.com",
        name="Ada Lovelace",
    )

    principal = _verify_jwt(token, cfg)

    assert principal.user_id == user_id
    assert principal.tenant_id == tenant_id
    assert principal.roles == ["admin"]
    assert principal.email == "ada@example.com"
    assert principal.name == "Ada Lovelace"


def test_expired_jwt_returns_401() -> None:
    cfg = _settings_for()
    token, _ = _make_token(exp_delta=timedelta(seconds=-30))

    with pytest.raises(HTTPException) as excinfo:
        _verify_jwt(token, cfg)

    assert excinfo.value.status_code == 401
    assert "invalid or missing credentials" in str(excinfo.value.detail)


def test_missing_auth_header_returns_401() -> None:
    """No Authorization header, not in dev mode → 401."""
    cfg = _settings_for(app_env="prod")
    request = _make_request()

    with pytest.raises(HTTPException) as excinfo:
        current_user(request=request, credentials=None, cfg=cfg)

    assert excinfo.value.status_code == 401


def test_malformed_jwt_returns_401() -> None:
    cfg = _settings_for()
    with pytest.raises(HTTPException) as excinfo:
        _verify_jwt("not-a-jwt", cfg)
    assert excinfo.value.status_code == 401


def test_wrong_signature_returns_401() -> None:
    cfg = _settings_for(secret="server-secret")
    # Sign with a different secret than the server expects.
    token, _ = _make_token(secret="attacker-secret")

    with pytest.raises(HTTPException) as excinfo:
        _verify_jwt(token, cfg)

    assert excinfo.value.status_code == 401


# ---------------------------------------------------------------------------
# Integration tests against GET /me
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_endpoint_returns_principal(
    client: httpx.AsyncClient, override_settings: Any
) -> None:
    cfg = _settings_for(app_env="prod")
    override_settings(cfg)

    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    token, _ = _make_token(
        secret=cfg.jwt_secret,
        user_id=user_id,
        tenant_id=tenant_id,
        roles=["reviewer"],
        email="grace@example.com",
        name="Grace Hopper",
    )

    resp = await client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == str(user_id)
    assert body["tenant_id"] == str(tenant_id)
    assert body["roles"] == ["reviewer"]
    assert body["email"] == "grace@example.com"
    assert body["name"] == "Grace Hopper"


@pytest.mark.asyncio
async def test_me_endpoint_dev_mode_headers(
    client: httpx.AsyncClient, override_settings: Any
) -> None:
    cfg = _settings_for(app_env="dev")
    override_settings(cfg)

    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    resp = await client.get(
        "/me",
        headers={
            "X-Dev-User-Id": str(user_id),
            "X-Dev-Tenant-Id": str(tenant_id),
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == str(user_id)
    assert body["tenant_id"] == str(tenant_id)
    assert body["roles"] == ["viewer"]
    assert body["email"] == f"{user_id}@dev.local"
    assert body["name"] == "Dev User"


@pytest.mark.asyncio
async def test_me_endpoint_production_mode_rejects_dev_headers(
    client: httpx.AsyncClient, override_settings: Any
) -> None:
    """Outside dev, the X-Dev-* headers must be ignored entirely."""
    cfg = _settings_for(app_env="prod")
    override_settings(cfg)

    resp = await client.get(
        "/me",
        headers={
            "X-Dev-User-Id": str(uuid.uuid4()),
            "X-Dev-Tenant-Id": str(uuid.uuid4()),
        },
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid or missing credentials"


@pytest.mark.asyncio
async def test_me_endpoint_no_credentials_returns_401(
    client: httpx.AsyncClient, override_settings: Any
) -> None:
    """No auth header, no dev headers → 401 even in dev mode."""
    cfg = _settings_for(app_env="dev")
    override_settings(cfg)

    resp = await client.get("/me")

    assert resp.status_code == 401


def test_principal_is_frozen() -> None:
    """Principal is immutable — guards against accidental mutation."""
    from pydantic import ValidationError

    p = Principal(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        roles=["viewer"],
        email="x@example.com",
        name="X",
    )
    with pytest.raises(ValidationError):
        p.email = "y@example.com"
