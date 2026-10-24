"""
Market regime gate: a top-down read of the tape that scales (or stops) NEW entries.

Jeff reads breadth, index extension and industry-group strength by eye before adding risk, and is more careful the
longer the market has run. This turns that into numbers, from data the bot already has:

  breadth   share of the universe above its 20-MA / 50-MA, and net 20-day highs minus lows
  index     SPY above its 20/50-MA, its ATR% multiple from the 50-MA (how stretched), and its up-close streak

  risk-off   breadth_20 < 30%, or SPY under both its 20- and 50-MA            -> no new entries
  stretched  SPY at/above 6x ATR% from the 50-MA, or 5 up-closes in a row     -> half risk
  neutral    breadth_20 below 50%                                              -> half risk
  risk-on    otherwise                                                         -> full risk

Every threshold is my assumption (config.REGIME_*), not Jeff's. The gate only touches NEW entries; open positions
keep their stops and trails. With fewer than REGIME_MIN_NAMES in the universe it gives no opinion (full risk).
"""

from typing import Dict, Optional

import numpy as np
import pandas as pd

from config import (
    REGIME_BREADTH_OFF, REGIME_BREADTH_ON, REGIME_INDEX_STRETCH, REGIME_MIN_NAMES, REGIME_MULT, REGIME_UP_DAYS,
)


def breadth_series(market_data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Per date: % above 20-MA, % above 50-MA, net % at 20-day highs minus lows, and how many names reported."""
    a20, a50, hi, lo = {}, {}, {}, {}
    for sym, df in market_data.items():
        if "SMA20" not in df or "SMA50" not in df:
            continue
        c = df["Close"]
        valid = df["SMA50"].notna()
        a20[sym] = (c > df["SMA20"]).where(valid)
        a50[sym] = (c > df["SMA50"]).where(valid)
        hi[sym] = (c >= c.rolling(20).max()).where(valid)
        lo[sym] = (c <= c.rolling(20).min()).where(valid)
    if not a20:
        return pd.DataFrame(columns=["pct20", "pct50", "net_hl", "n"])
    A20, A50 = pd.DataFrame(a20).astype(float), pd.DataFrame(a50).astype(float)
    H, L = pd.DataFrame(hi).astype(float), pd.DataFrame(lo).astype(float)
    return pd.DataFrame({
        "pct20": A20.mean(axis=1) * 100, "pct50": A50.mean(axis=1) * 100,
        "net_hl": (H.mean(axis=1) - L.mean(axis=1)) * 100, "n": A20.notna().sum(axis=1),
    })


def index_series(spy: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Per date: SPY above 20 / 50-MA, ATR% multiple from the 50-MA, and the current up-close streak."""
    if spy is None or spy.empty or "SMA50" not in spy:
        return pd.DataFrame(columns=["above20", "above50", "ext", "streak", "close"])
    up = (spy["Close"].diff() > 0).astype(int)
    streak = up.groupby((up == 0).cumsum()).cumsum()
    return pd.DataFrame({
        "above20": spy["Close"] > spy["SMA20"], "above50": spy["Close"] > spy["SMA50"],
        "ext": spy["ATR_ext_50"], "streak": streak, "close": spy["Close"],
    })


def classify(pct20: float, pct50: float, n: int, idx: Optional[dict]) -> dict:
    """idx = {above20, above50, ext, streak} for SPY, or None when no index data is available."""
    reasons = []
    if n < REGIME_MIN_NAMES or not np.isfinite(pct20):
        return {"label": "unknown", "mult": REGIME_MULT["unknown"], "reasons": [f"only {int(n)} names, no opinion"]}
    label = "risk-on"
    if pct20 < REGIME_BREADTH_OFF:
        label, reasons = "risk-off", [f"breadth {pct20:.0f}% of names above their 20-MA (< {REGIME_BREADTH_OFF:g}%)"]
    elif idx is not None and not idx["above20"] and not idx["above50"]:
        label, reasons = "risk-off", ["SPY is below both its 20-day and 50-day average"]
    elif idx is not None and (
            (np.isfinite(idx["ext"]) and idx["ext"] >= REGIME_INDEX_STRETCH) or idx["streak"] >= REGIME_UP_DAYS):
        why = []
        if np.isfinite(idx["ext"]) and idx["ext"] >= REGIME_INDEX_STRETCH:
            why.append(f"SPY {idx['ext']:.1f}x ATR% above its 50-MA")
        if idx["streak"] >= REGIME_UP_DAYS:
            why.append(f"{int(idx['streak'])} up-closes in a row")
        label, reasons = "stretched", why
    elif pct20 < REGIME_BREADTH_ON:
        label, reasons = "neutral", [f"breadth {pct20:.0f}% above 20-MA (< {REGIME_BREADTH_ON:g}%)"]
    else:
        reasons = [f"breadth {pct20:.0f}% above 20-MA, SPY trend intact"]
    return {"label": label, "mult": REGIME_MULT[label], "reasons": reasons}


def snapshot(market_data: Dict[str, pd.DataFrame], spy: Optional[pd.DataFrame], extra: Optional[dict] = None) -> dict:
    """Latest reading for the live scan; also what the dashboard shows."""
    b = breadth_series(market_data)
    ix = index_series(spy)
    row = b.iloc[-1] if len(b) else None
    idx = None
    if len(ix):
        r = ix.iloc[-1]
        idx = {"above20": bool(r["above20"]), "above50": bool(r["above50"]),
               "ext": float(r["ext"]) if pd.notna(r["ext"]) else float("nan"), "streak": int(r["streak"]),
               "close": float(r["close"])}
    if row is None:
        out = classify(float("nan"), float("nan"), 0, idx)
        out.update({"pct20": None, "pct50": None, "net_hl": None, "n": 0})
    else:
        out = classify(float(row["pct20"]), float(row["pct50"]), int(row["n"]), idx)
        out.update({"pct20": round(float(row["pct20"]), 1), "pct50": round(float(row["pct50"]), 1),
                    "net_hl": round(float(row["net_hl"]), 1), "n": int(row["n"])})
    out["spy"] = None if idx is None else {k: (round(v, 2) if isinstance(v, float) and np.isfinite(v) else (None if isinstance(v, float) else v)) for k, v in idx.items()}
    if extra:
        out.update(extra)
    return out


def series_labels(market_data: Dict[str, pd.DataFrame], spy: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Per date label + multiplier, for the backtest (same rules as the live gate, no look-ahead: each row uses its own date)."""
    b = breadth_series(market_data)
    ix = index_series(spy)
    rows = {}
    for d, r in b.iterrows():
        idx = None
        if d in ix.index:
            q = ix.loc[d]
            idx = {"above20": bool(q["above20"]), "above50": bool(q["above50"]),
                   "ext": float(q["ext"]) if pd.notna(q["ext"]) else float("nan"), "streak": int(q["streak"])}
        c = classify(float(r["pct20"]), float(r["pct50"]), int(r["n"]), idx)
        rows[d] = (c["label"], c["mult"], "; ".join(c["reasons"]))
    return pd.DataFrame.from_dict(rows, orient="index", columns=["label", "mult", "why"])
