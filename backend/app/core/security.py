"""Password hashing and JWT helpers.

Uses ``bcrypt`` directly instead of passlib so it is compatible with
Python 3.12 (passlib's bundled ``bcrypt`` wrapper breaks on the
``password cannot be longer than 72 bytes`` validation error).
"""
from __future__ import annotations

import datetime as dt

import bcrypt
import jwt

from app.core.config import settings

#: bcrypt only uses the first 72 bytes of a password; truncating keeps
#: long passphrases consistent with what bcrypt will actually compare.
_BCRYPT_MAX_BYTES = 72

_TOKEN_TYPE = "Bearer"


# --------------------------------------------------------------------- hashing --
def _prep_password(password: str) -> bytes:
    encoded = password.encode("utf-8")
    if len(encoded) > _BCRYPT_MAX_BYTES:
        encoded = encoded[:_BCRYPT_MAX_BYTES]
    return encoded


def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt. Returns a str hash."""
    return bcrypt.hashpw(_prep_password(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Constant-time comparison of a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(
            _prep_password(plain_password), hashed_password.encode("utf-8")
        )
    except (ValueError, TypeError):
        return False


# ------------------------------------------------------------------------- jwt --
def create_access_token(subject: str | int, expires_minutes: int | None = None) -> str:
    """Create a signed JWT access token for the given user id / subject."""
    minutes = expires_minutes or settings.JWT_EXPIRES_MINUTES
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": str(subject),
        "iat": now,
        "exp": now + dt.timedelta(minutes=minutes),
        "type": _TOKEN_TYPE,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT. Raises ``jwt.PyJWTError`` on any problem."""
    return jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        options={"require": ["sub", "exp", "iat"]},
    )