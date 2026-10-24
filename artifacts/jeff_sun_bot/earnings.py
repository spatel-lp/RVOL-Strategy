"""
Earnings lookups with a disk cache, and the scanner filter built on them.

Jeff's hub rule is "no entry right before ... pre/post-market earnings releases". Report timing (before the open
vs after the close) is not reliably available from Yahoo, so the scanner uses a plain 7-day window: a name
whose NEXT report is 0..EARNINGS_WINDOW_DAYS days away cannot be on the Focus list. Reports that already
happened do not block (post-earnings gaps are one of Jeff's setups).

A failed lookup NEVER blocks (fail open) and is retried the next day; the dashboard counts the unknowns so you
can see how much of the filter is flying blind. ETFs have no earnings and are skipped.
"""

import json
import os
import time
from datetime import date, datetime
from typing import Callable, Optional

import pandas as pd

from config import (
    EARNINGS_CACHE_DAYS, EARNINGS_RETRY_DAYS, EARNINGS_WINDOW_DAYS, ETF_CLUSTER, PAPER_STATE_DIR,
)

REASON = f"earnings within {EARNINGS_WINDOW_DAYS} days"
_AS_OF: Optional[date] = None
UNKNOWN: set = set()                      # symbols whose lookup failed during this run


def set_as_of(day: date) -> None:
    global _AS_OF
    _AS_OF = day
    UNKNOWN.clear()


def _path() -> str:
    return os.path.join(os.environ.get("PAPER_STATE_DIR", PAPER_STATE_DIR), "earnings_cache.json")


def _load() -> dict:
    try:
        with open(_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(cache: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_path()) or ".", exist_ok=True)
        with open(_path(), "w") as f:
            json.dump(cache, f)
    except OSError:
        pass


def _to_date(v) -> Optional[date]:
    try:
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, date):
            return v
        return pd.Timestamp(v).date()
    except Exception:
        return None


def fetch_next_earnings(symbol: str, today: date) -> Optional[date]:
    """Next report on/after `today` from yfinance. Raises if Yahoo gives nothing usable, so the caller can fail open."""
    import yfinance as yf
    t = yf.Ticker(symbol)
    found = []
    try:
        cal = t.calendar
        vals = []
        if isinstance(cal, dict):
            vals = cal.get("Earnings Date") or []
        elif isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
            vals = list(cal.loc["Earnings Date"].dropna())
        for v in (vals if isinstance(vals, (list, tuple)) else [vals]):
            d = _to_date(v)
            if d:
                found.append(d)
    except Exception:
        pass
    if not found:
        df = t.get_earnings_dates(limit=8)                  # may raise: that is the "unknown" signal
        if df is not None and len(df):
            found = [d for d in (_to_date(i) for i in df.index) if d]
    if not found:
        raise LookupError("no earnings dates from Yahoo")
    upcoming = [d for d in found if d >= today]
    return min(upcoming) if upcoming else None


def next_earnings(symbol: str, today: Optional[date] = None,
                  fetcher: Callable[[str, date], Optional[date]] = None) -> Optional[date]:
    today = today or _AS_OF or date.today()
    fetcher = fetcher or fetch_next_earnings
    cache = _load()
    hit = cache.get(symbol)
    now = time.time()
    if hit:
        ttl = (EARNINGS_CACHE_DAYS if hit.get("ok") else EARNINGS_RETRY_DAYS) * 86400
        if now - hit.get("ts", 0) < ttl:
            d = _to_date(hit["date"]) if hit.get("date") else None
            if hit.get("ok") and d is not None and d < today:
                pass                                         # the cached report already happened: look up the next one
            else:
                if not hit.get("ok"):
                    UNKNOWN.add(symbol)
                return d
    try:
        d = fetcher(symbol, today)
        cache[symbol] = {"ts": now, "date": d.isoformat() if d else None, "ok": True}
    except Exception:
        d = None
        cache[symbol] = {"ts": now, "date": None, "ok": False}
        UNKNOWN.add(symbol)
    _save(cache)
    return d


def earnings_blocker(symbol: str, row: dict, fetcher: Callable = None) -> str:
    """FOCUS_BLOCKERS hook for scan_core: a reason string blocks the name, '' lets it through."""
    if symbol in ETF_CLUSTER or row.get("LEV") == "Y":
        return ""
    today = _AS_OF or date.today()
    d = next_earnings(symbol, today, fetcher=fetcher)
    if d is not None and 0 <= (d - today).days <= EARNINGS_WINDOW_DAYS:
        return REASON
    return ""


def days_until(symbol: str, today: Optional[date] = None) -> Optional[int]:
    today = today or _AS_OF or date.today()
    d = next_earnings(symbol, today)
    return (d - today).days if d else None
