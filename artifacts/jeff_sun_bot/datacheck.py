"""
Data health: cheap sanity checks on every daily frame, plus a weekly cross-check of a sample of closes
against a second source (Stooq, no API key). Yahoo is unofficial; this is how you find out when it is wrong.
"""

import io
import urllib.request
from typing import Callable, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from config import CROSSCHECK_SAMPLE, CROSSCHECK_TOL, DATA_EXTREME_MOVE, DATA_GAP_BDAYS


def sanity_checks(market_data: Dict[str, pd.DataFrame], expected_ts: pd.Timestamp, leveraged: set) -> dict:
    """Returns {check: [examples]} with only non-empty checks present."""
    found: Dict[str, List[str]] = {}

    def add(kind: str, msg: str):
        found.setdefault(kind, []).append(msg)

    for sym, df in market_data.items():
        if df.empty:
            continue
        if df.index[-1] != expected_ts:
            add("stale last bar", f"{sym} ({df.index[-1]:%Y-%m-%d})")
        tail = df.tail(60)
        dates = tail.index.to_numpy(dtype="datetime64[D]")
        if len(dates) > 1:
            gaps = np.busday_count(dates[:-1], dates[1:])
            if gaps.max() > DATA_GAP_BDAYS:
                add("missing days", f"{sym} (gap of {int(gaps.max())} business days)")
        if sym not in leveraged:
            move = tail["Close"].pct_change().abs()
            if move.max() > DATA_EXTREME_MOVE:
                add("extreme one-day move (split / bad tick?)", f"{sym} ({move.idxmax():%Y-%m-%d} {move.max() * 100:.0f}%)")
        if int((df["Volume"].tail(20) <= 0).sum()) >= 3:
            add("zero-volume days", sym)
        if bool(((tail["High"] < tail["Low"]) | (tail["Close"] <= 0)).any()):
            add("invalid OHLC", sym)
    return found


def fetch_stooq(symbol: str, timeout: int = 10) -> pd.DataFrame:
    url = f"https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (paper-trading-datacheck)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8", "replace")
    if not text.lower().startswith("date"):
        return pd.DataFrame()                                  # "No data" / blocked
    df = pd.read_csv(io.StringIO(text), parse_dates=["Date"]).set_index("Date")
    return df


def crosscheck(market_data: Dict[str, pd.DataFrame], symbols: Iterable[str],
               fetcher: Callable[[str], pd.DataFrame] = fetch_stooq, tol: float = CROSSCHECK_TOL,
               sample: int = CROSSCHECK_SAMPLE) -> dict:
    """Compare the latest common close with a second source. Unavailable symbols are reported, not failed."""
    checked, ok, bad, unavailable = 0, 0, [], []
    for sym in list(dict.fromkeys(symbols))[:sample]:
        df = market_data.get(sym)
        if df is None or df.empty:
            continue
        try:
            other = fetcher(sym)
        except Exception:
            other = pd.DataFrame()
        if other is None or other.empty or "Close" not in other.columns:
            unavailable.append(sym)
            continue
        common = df.index.intersection(other.index)
        if not len(common):
            unavailable.append(sym)
            continue
        d = common[-1]
        a, b = float(df.loc[d, "Close"]), float(other.loc[d, "Close"])
        checked += 1
        diff = abs(a - b) / b if b else 1.0
        if diff > tol:
            bad.append({"symbol": sym, "date": d.strftime("%Y-%m-%d"), "yahoo": round(a, 2), "stooq": round(b, 2),
                        "diff_pct": round(diff * 100, 1)})
        else:
            ok += 1
    return {"checked": checked, "ok": ok, "mismatched": bad, "unavailable": unavailable}
