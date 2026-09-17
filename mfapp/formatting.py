from __future__ import annotations

from decimal import Decimal
from math import isfinite
from typing import Any

from .extensions import db
from .models import UserPreference

NUMBER_FORMATS = ("AUTO", "FULL", "K", "M", "B", "T")


def get_number_format(user_id: int | None) -> str:
    if not user_id:
        return "AUTO"
    row = UserPreference.query.filter_by(user_id=user_id, key="number_format").first()
    raw = str((row.value or {}).get("mode") if row else "AUTO").upper()
    return raw if raw in NUMBER_FORMATS else "AUTO"


def set_number_format(user_id: int, mode: str) -> str:
    value = str(mode or "AUTO").upper()
    if value not in NUMBER_FORMATS:
        value = "AUTO"
    row = UserPreference.query.filter_by(user_id=user_id, key="number_format").first()
    if row is None:
        row = UserPreference(user_id=user_id, key="number_format", value={"mode": value})
        db.session.add(row)
    else:
        row.value = {"mode": value}
    return value


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def format_number(value: Any, mode: str = "AUTO", decimals: int = 1) -> str:
    n = _number(value)
    if n is None:
        return "—"
    mode = str(mode or "AUTO").upper()
    if mode not in NUMBER_FORMATS:
        mode = "AUTO"
    decimals = max(0, min(int(decimals), 4))

    if mode == "FULL":
        if abs(n) >= 1000:
            return f"{n:,.0f}"
        if float(n).is_integer():
            return f"{n:,.0f}"
        return f"{n:,.{decimals}f}"

    scales = {"K": (1e3, "K"), "M": (1e6, "M"), "B": (1e9, "B"), "T": (1e12, "T")}
    if mode == "AUTO":
        absolute = abs(n)
        if absolute >= 1e12:
            divisor, suffix = scales["T"]
        elif absolute >= 1e9:
            divisor, suffix = scales["B"]
        elif absolute >= 1e6:
            divisor, suffix = scales["M"]
        elif absolute >= 1e3:
            divisor, suffix = scales["K"]
        else:
            if float(n).is_integer():
                return f"{n:,.0f}"
            return f"{n:,.{decimals}f}"
    else:
        divisor, suffix = scales[mode]

    scaled = n / divisor
    if abs(scaled) >= 100:
        shown = f"{scaled:,.0f}"
    elif abs(scaled) >= 10:
        shown = f"{scaled:,.1f}"
    else:
        shown = f"{scaled:,.{decimals}f}"
    return f"{shown}{suffix}"


def format_money(value: Any, mode: str = "FULL", decimals: int = 2) -> str:
    n = _number(value)
    if n is None:
        return "—"
    if str(mode or "FULL").upper() == "FULL":
        return f"${n:,.{max(0, min(int(decimals), 4))}f}"
    return "$" + format_number(n, mode, decimals)


__all__ = ["NUMBER_FORMATS", "get_number_format", "set_number_format", "format_number", "format_money"]
