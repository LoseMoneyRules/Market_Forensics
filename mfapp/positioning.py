from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import requests

from .data_providers import get_secret


def _headers(user_id: int) -> dict[str, str] | None:
    key = get_secret(user_id, "alpaca_key")
    secret = get_secret(user_id, "alpaca_secret")
    if not key or not secret:
        return None
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def refresh_positioning_bundle(ticker: str, user_id: int) -> dict[str, Any]:
    headers = _headers(user_id)
    symbol = str(ticker or "").strip().upper()
    if not headers or not symbol:
        return {"configured": False, "ticker": symbol, "errors": ["Alpaca credentials are not configured."]}

    errors: list[str] = []
    out: dict[str, Any] = {
        "configured": True,
        "ticker": symbol,
        "borrow": {},
        "options": {},
        "locate": {},
        "errors": errors,
    }

    try:
        asset = requests.get(
            f"https://paper-api.alpaca.markets/v2/assets/{symbol}",
            headers=headers,
            timeout=12,
        )
        if asset.status_code == 200:
            row = asset.json() or {}
            out["borrow"] = {
                "shortable": bool(row.get("shortable")),
                "borrow_status": row.get("borrow_status") or ("easy_to_borrow" if row.get("easy_to_borrow") else "unknown"),
                "easy_to_borrow": row.get("easy_to_borrow"),
                "marginable": row.get("marginable"),
                "attributes": row.get("attributes") or [],
            }
        else:
            errors.append(f"Asset metadata HTTP {asset.status_code}.")
    except Exception as exc:
        errors.append(f"Asset metadata: {type(exc).__name__}")

    start = date.today()
    end = start + timedelta(days=120)
    contracts: list[dict[str, Any]] = []
    token = None
    pages = 0
    try:
        while pages < 5:
            params = {
                "underlying_symbols": symbol,
                "status": "active",
                "expiration_date_gte": start.isoformat(),
                "expiration_date_lte": end.isoformat(),
                "limit": 1000,
            }
            if token:
                params["page_token"] = token
            response = requests.get(
                "https://paper-api.alpaca.markets/v2/options/contracts",
                headers=headers,
                params=params,
                timeout=15,
            )
            if response.status_code != 200:
                errors.append(f"Options contracts HTTP {response.status_code}.")
                break
            payload = response.json() or {}
            rows = list(payload.get("option_contracts") or [])
            contracts.extend(rows)
            token = payload.get("next_page_token") or payload.get("page_token")
            pages += 1
            if not token or not rows:
                break
    except Exception as exc:
        errors.append(f"Options contracts: {type(exc).__name__}")

    if contracts:
        call_oi = 0.0
        put_oi = 0.0
        call_contracts = put_contracts = 0
        expirations: set[str] = set()
        for row in contracts:
            try:
                oi = float(row.get("open_interest") or 0)
            except (TypeError, ValueError):
                oi = 0.0
            typ = str(row.get("type") or "").lower()
            if typ == "put":
                put_oi += oi
                put_contracts += 1
            elif typ == "call":
                call_oi += oi
                call_contracts += 1
            if row.get("expiration_date"):
                expirations.add(str(row["expiration_date"]))
        out["options"] = {
            "put_open_interest": put_oi,
            "call_open_interest": call_oi,
            "put_call_oi": (put_oi / call_oi) if call_oi > 0 else None,
            "contracts": len(contracts),
            "put_contracts": put_contracts,
            "call_contracts": call_contracts,
            "expirations": sorted(expirations),
            "horizon_days": 120,
        }

    # HTB locate preview is account/eligibility dependent. Failure is non-fatal.
    borrow_status = str((out.get("borrow") or {}).get("borrow_status") or "").lower()
    if "hard" in borrow_status:
        for base in ("https://api.alpaca.markets", "https://paper-api.alpaca.markets"):
            try:
                response = requests.get(
                    f"{base}/v1/locates/quotes",
                    headers=headers,
                    params={"symbols": symbol},
                    timeout=10,
                )
                if response.status_code == 200:
                    payload = response.json() or {}
                    quotes = payload.get("quotes") or []
                    quote = next((q for q in quotes if str(q.get("symbol") or "").upper() == symbol), None)
                    if quote:
                        out["locate"] = {
                            "available_qty": quote.get("available_qty"),
                            "price": quote.get("price"),
                            "quoted_at": quote.get("quoted_at"),
                        }
                        break
            except Exception:
                continue

    return out


__all__ = ["refresh_positioning_bundle"]
