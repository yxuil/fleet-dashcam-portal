"""Authentication primitives for the dashcam portal.

The portal does not mint JWTs; it verifies tokens issued by an upstream IdP.
Each request supplies an ``Authorization: Bearer <jwt>`` header. The token
must be HS256-signed with ``settings.jwt_secret`` and carry the claims
listed on :class:`Principal`.

Local development: when ``settings.app_env == "dev"`` the dependency also
accepts ``X-Dev-User-Id`` and ``X-Dev-Tenant-Id`` headers to mint a
synthetic principal without a JWT. This shortcut is intentionally disabled
in any non-dev environment.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from app.config import Settings, settings

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="invalid or missing credentials",
    headers={"WWW-Authenticate": "Bearer"},
)

# auto_error=False so we can return a consistent 401 ourselves instead of
# FastAPI's default 403 when the header is missing.
_bearer_scheme = HTTPBearer(auto_error=False)


class Principal(BaseModel):
    """The authenticated caller as derived from the JWT (or dev headers)."""

    model_config = ConfigDict(frozen=True)

    user_id: UUID
    tenant_id: UUID
    roles: list[str]
    email: str
    name: str


def get_settings() -> Settings:
    """Indirection so tests can override settings via ``app.dependency_overrides``."""
    return settings


def _dev_principal(user_id: str, tenant_id: str) -> Principal:
    """Mint a synthetic principal for local development."""
    try:
        uid = UUID(user_id)
        tid = UUID(tenant_id)
    except (ValueError, TypeError) as exc:
        raise _CREDENTIALS_ERROR from exc

    return Principal(
        user_id=uid,
        tenant_id=tid,
        roles=["viewer"],
        email=f"{uid}@dev.local",
        name="Dev User",
    )


def _verify_jwt(token: str, cfg: Settings) -> Principal:
    """Decode and validate a JWT, returning a :class:`Principal`.

    Any failure — bad signature, expired token, missing claims, malformed
    UUIDs — collapses into the generic 401 to avoid leaking which part of
    the token was wrong.

    Defence in depth: tokens carrying a ``purpose`` claim are rejected
    here. Session JWTs minted upstream don't include a ``purpose``; the
    only place we set one is :func:`app.storage._mint_stream_token`, which
    uses ``"clip-stream"`` for the cross-origin ``<video>`` flow. Refusing
    *any* non-empty ``purpose`` on the session path prevents a stream
    token from accidentally satisfying ``current_user``.
    """
    try:
        claims = jwt.decode(token, cfg.jwt_secret, algorithms=[cfg.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise _CREDENTIALS_ERROR from exc
    except jwt.InvalidTokenError as exc:
        raise _CREDENTIALS_ERROR from exc

    if claims.get("purpose"):
        raise _CREDENTIALS_ERROR

    try:
        return Principal(
            user_id=UUID(claims["sub"]),
            tenant_id=UUID(claims["tenant_id"]),
            roles=list(claims["roles"]),
            email=claims["email"],
            name=claims["name"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _CREDENTIALS_ERROR from exc


def current_user(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ] = None,
    cfg: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
) -> Principal:
    """FastAPI dependency returning the authenticated :class:`Principal`.

    Raises ``HTTPException(401, "invalid or missing credentials")`` if the
    request carries no usable credentials.
    """
    if cfg is None:  # pragma: no cover - defensive; Depends always supplies it
        cfg = get_settings()

    if cfg.app_env == "dev":
        dev_user = request.headers.get("X-Dev-User-Id")
        dev_tenant = request.headers.get("X-Dev-Tenant-Id")
        if dev_user and dev_tenant:
            return _dev_principal(dev_user, dev_tenant)

    if credentials is None or not credentials.credentials:
        raise _CREDENTIALS_ERROR

    return _verify_jwt(credentials.credentials, cfg)
