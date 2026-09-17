from __future__ import annotations

import re
from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class SymbolValidation:
    ticker: str
    status: str
    source: str
    name: str = ""
    exchange: str = ""
    currency: str = "USD"
    instrument_type: str = ""
    message: str = ""

    @property
    def valid(self) -> bool:
        return self.status == "VALID"


def normalize_ticker(value: str) -> str:
    return str(value or "").strip().upper()


def validate_ticker(value: str) -> SymbolValidation:
    ticker = normalize_ticker(value)
    if not ticker:
        return SymbolValidation(ticker, "INVALID", "syntax", message="Ticker is required.")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,15}", ticker):
        return SymbolValidation(ticker, "INVALID", "syntax", message="Ticker not found / symbol not recognized.")
    try:
        provider_symbol = ticker.replace(".", "-")
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{provider_symbol}",
            params={"range": "5d", "interval": "1d", "events": "div,splits"},
            headers={"User-Agent": "Mozilla/5.0 MarketForensics/0.2.0"},
            timeout=8,
        )
        if response.status_code == 200:
            chart = (response.json() or {}).get("chart") or {}
            result = chart.get("result") or []
            if result and (result[0].get("meta") or {}).get("symbol"):
                meta = result[0].get("meta") or {}
                return SymbolValidation(
                    ticker=ticker,
                    status="VALID",
                    source="Public market chart",
                    name=meta.get("longName") or meta.get("shortName") or ticker,
                    exchange=str(meta.get("exchangeName") or meta.get("fullExchangeName") or ""),
                    currency=str(meta.get("currency") or "USD"),
                    instrument_type=str(meta.get("instrumentType") or ""),
                )
            return SymbolValidation(ticker, "INVALID", "Public market chart", message="Ticker not found / symbol not recognized.")
        if response.status_code in {404, 422}:
            return SymbolValidation(ticker, "INVALID", "Public market chart", message="Ticker not found / symbol not recognized.")
        return SymbolValidation(ticker, "UNAVAILABLE", "Public market chart", message="Ticker could not be validated right now, so it was not saved. Try again later.")
    except Exception:
        return SymbolValidation(ticker, "UNAVAILABLE", "Public market chart", message="Ticker could not be validated right now, so it was not saved. Try again later.")
