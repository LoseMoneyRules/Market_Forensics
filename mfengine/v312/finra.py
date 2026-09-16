from __future__ import annotations
import requests
from datetime import date, timedelta
import csv, io

def get_daily_short_volume(ticker: str, lookback_calendar_days=45):
    ticker = ticker.upper()
    today = date.today()
    out = []
    for i in range(lookback_calendar_days, -1, -1):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        stamp = d.strftime("%Y%m%d")
        url = f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{stamp}.txt"
        try:
            r = requests.get(url, timeout=15)
            if r.status_code != 200:
                continue
            for row in csv.DictReader(io.StringIO(r.text), delimiter="|"):
                if (row.get("Symbol") or "").upper() != ticker:
                    continue
                sv = float(row.get("ShortVolume") or 0)
                sev = float(row.get("ShortExemptVolume") or 0)
                tv = float(row.get("TotalVolume") or 0)
                out.append({
                    "trade_date": d.isoformat(), "short_volume": sv,
                    "short_exempt_volume": sev, "total_reported_volume": tv,
                    "short_pct": (sv + sev) / tv if tv else None,
                    "market": row.get("Market") or "",
                })
        except Exception:
            continue
    return out
