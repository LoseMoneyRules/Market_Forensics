from __future__ import annotations

import os
from functools import wraps

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from flask import abort, g, redirect, request, url_for

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored: str, password: str) -> bool:
    try:
        return _hasher.verify(stored, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def _fernet() -> Fernet:
    key = os.environ.get("MF_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError("MF_ENCRYPTION_KEY is required")
    return Fernet(key.encode("ascii"))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode("ascii")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("Unable to decrypt protected secret") from exc


def _login_redirect():
    next_path = request.full_path if request.query_string else request.path
    return redirect(url_for("auth.login", next=next_path))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(g, "user", None):
            return _login_redirect()
        return view(*args, **kwargs)
    return wrapped


def role_required(*roles):
    roles = {r.upper() for r in roles}

    def deco(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not getattr(g, "user", None):
                return _login_redirect()
            if str(g.user.role or "").upper() not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return deco
