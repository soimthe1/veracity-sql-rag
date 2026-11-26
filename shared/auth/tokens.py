"""
Session tokens — carry IDENTITY ONLY, never grants (grants/roles are
resolved fresh from this project's own Postgres on every request; see
login.py's resolve_principal and api/access.py — never cached in the
token).

HS256 by default,
secret read from an env var with NO fallback default (fails loudly at
first use rather than ever silently signing with a guessable secret),
and an explicit algorithm whitelist passed to jwt.decode() on verify()
(anti `alg=none` / algorithm-confusion forgery).
"""
from __future__ import annotations

import os
import time
from functools import lru_cache
from typing import Any, Dict

from shared.config import AuthConfig


class TokenError(Exception):
    pass


@lru_cache(maxsize=8)
def _secret(env_var: str) -> bytes:
    value = os.environ.get(env_var, "")
    if not value or len(value) < 16:
        raise TokenError(
            f"{env_var} is not set (or is under 16 characters) in the environment. Session tokens "
            f"cannot be signed without it. Set a random value, e.g.: "
            f'python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    return value.encode("utf-8")


def issue(cfg: AuthConfig, user_id: str, username: str, display_name: str) -> str:
    import jwt

    now = int(time.time())
    payload: Dict[str, Any] = {
        "sub": user_id,
        "username": username,
        "name": display_name,
        "iat": now,
        "exp": now + cfg.expiry_minutes * 60,
        "jti": f"{user_id}.{now}",
    }
    return jwt.encode(payload, _secret(cfg.jwt_secret_env_var), algorithm=cfg.algorithm)


def verify(cfg: AuthConfig, token: str) -> Dict[str, Any]:
    import jwt

    try:
        return jwt.decode(token, _secret(cfg.jwt_secret_env_var), algorithms=[cfg.algorithm])
    except jwt.PyJWTError as exc:
        raise TokenError(f"Invalid or expired session token: {exc}") from exc
