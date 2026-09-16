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


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(g, "user", None):
            return redirect(url_for("web.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def role_required(*roles):
    roles = {r.upper() for r in roles}
    def deco(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not getattr(g, "user", None):
                return redirect(url_for("web.login", next=request.path))
            if g.user.role not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return deco
