"""Password hashing for local accounts (catalog.auth.mode: local), with
the standard library's scrypt — no extra dependency. Stored as
scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>, so the cost can be raised later
without invalidating existing hashes."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_N, _R, _P = 2 ** 14, 8, 1
MIN_LENGTH = 8


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_N}${_R}${_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        scheme, n, r, p, salt, digest = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def check_strength(password: str) -> None:
    if len(password or "") < MIN_LENGTH:
        raise ValueError(f"Passwords need at least {MIN_LENGTH} characters.")
