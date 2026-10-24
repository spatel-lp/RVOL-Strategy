"""
Signal log: every near-miss the scanner rejected, every Focus name the intraday ORH check skipped, and
every paper entry, each followed for 1/3/5/10 trading days. The audit answers a question a journal of
only-taken trades cannot: is a given filter protecting you from losers, or costing you winners?

Forward returns here are close-to-close from the signal day, with no stops or trims applied, so treat
the audit as a rough compass and wait for AUDIT_MIN_N resolved signals per reason before believing it.
"""

import statistics
from datetime import date as _date, timedelta
from typing import Dict, Iterable, List, Optional

import pandas as pd

from config import AUDIT_MIN_N, FORWARD_DAYS, SIGNAL_KEEP_DAYS, SIGNAL_MAX_PER_DAY

KINDS = ("reject", "focus", "orh_skip", "taken")


def _sid(day: str, symbol: str, kind: str) -> str:
    return f"{day}:{symbol}:{kind}"


def record(state: dict, day: str, symbol: str, kind: str, reason: str, price: float, atr: float,
           detail: str = "") -> None:
    """Upsert one signal. Re-recording the same day/symbol/kind updates its reason (the latest verdict wins)."""
    log: List[dict] = state.setdefault("signals", [])
    sid = _sid(day, symbol, kind)
    for s in reversed(log[-600:]):
        if s["id"] == sid:
            s["reason"], s["detail"] = reason, detail
            return
    log.append({
        "id": sid, "date": day, "symbol": symbol, "kind": kind, "reason": reason, "detail": detail,
        "price": round(float(price), 4), "atr": round(float(atr or 0), 4),
        "fwd": {str(h): None for h in FORWARD_DAYS}, "mfe_atr": None, "mae_atr": None, "done": False,
    })


def categorize_orh(reason: str) -> str:
    """Collapse the free-text ORH verdicts into a few stable categories so the audit can group them."""
    r = (reason or "").lower()
    if "below orh" in r:
        return "price below ORH"
    if "no average-volume" in r or "no intraday" in r or "no session" in r:
        return "no data"
    if "volume" in r:
        return "volume too light"
    if "lod" in r:
        return "low of day too far (risk)"
    if r.startswith("blocked"):
        return "blocked: " + r.split(":", 1)[-1].strip()[:40]
    return (reason or "other")[:30]


def log_scan_rejects(state: dict, day: str, rows: List[dict], focus_symbols: Iterable[str],
                     failures: Dict[str, str]) -> int:
    """Log today's near-misses (passed the stalk minimums, failed a Focus rule) and the Focus names themselves."""
    focus = set(focus_symbols)
    n = 0
    near = [r for r in rows if r["symbol"] not in focus and failures.get(r["symbol"])]
    near.sort(key=lambda r: r.get("score", 0), reverse=True)
    for r in near[:SIGNAL_MAX_PER_DAY]:
        record(state, day, r["symbol"], "reject", failures[r["symbol"]], r["close"], r.get("ATR", 0))
        n += 1
    for r in rows:
        if r["symbol"] in focus:
            record(state, day, r["symbol"], "focus", "on focus list", r["close"], r.get("ATR", 0))
            n += 1
    return n


def update_forward(state: dict, market_data: Dict[str, pd.DataFrame]) -> int:
    """Fill in forward returns and excursions for open signals using the newest daily bars."""
    changed = 0
    horizon = max(FORWARD_DAYS)
    for s in state.get("signals", []):
        if s.get("done"):
            continue
        df = market_data.get(s["symbol"])
        if df is None or df.empty:
            continue
        ts = pd.Timestamp(s["date"])
        if ts not in df.index:
            continue
        i = int(df.index.get_loc(ts))
        price = s["price"] or float(df["Close"].iloc[i])
        n_after = len(df) - 1 - i
        for h in FORWARD_DAYS:
            if s["fwd"].get(str(h)) is None and n_after >= h and price > 0:
                s["fwd"][str(h)] = round(float(df["Close"].iloc[i + h]) / price - 1.0, 5)
                changed += 1
        window = df.iloc[i + 1: i + 1 + horizon]
        if len(window) and s["atr"] > 0:
            s["mfe_atr"] = round((float(window["High"].max()) - price) / s["atr"], 3)
            s["mae_atr"] = round((float(window["Low"].min()) - price) / s["atr"], 3)
        s["done"] = all(v is not None for v in s["fwd"].values())
    return changed


def prune(state: dict, today: str) -> None:
    cutoff = (_date.fromisoformat(today) - timedelta(days=SIGNAL_KEEP_DAYS)).isoformat()
    state["signals"] = [s for s in state.get("signals", []) if s["date"] >= cutoff]


def audit(state: dict, horizon: int = 5) -> List[dict]:
    """Group by (kind, reason). Verdicts are deliberately cautious: wait for AUDIT_MIN_N resolved signals."""
    groups: Dict[tuple, List[dict]] = {}
    for s in state.get("signals", []):
        groups.setdefault((s["kind"], s["reason"]), []).append(s)
    out = []
    for (kind, reason), items in groups.items():
        fwd = [s["fwd"].get(str(horizon)) for s in items if s["fwd"].get(str(horizon)) is not None]
        mfe = [s["mfe_atr"] for s in items if s.get("mfe_atr") is not None]
        mae = [s["mae_atr"] for s in items if s.get("mae_atr") is not None]
        avg = statistics.fmean(fwd) if fwd else None
        win = (sum(1 for v in fwd if v > 0) / len(fwd)) if fwd else None
        if len(fwd) < AUDIT_MIN_N:
            verdict = "too few to judge"
        elif kind in ("reject", "orh_skip"):
            verdict = ("possibly costing winners" if (avg > 0.015 and win >= 0.55)
                       else "protecting" if avg <= 0 else "neutral")
        else:
            verdict = "benchmark"
        out.append({
            "kind": kind, "reason": reason, "n": len(items), "resolved": len(fwd),
            "avg_fwd_pct": round(avg * 100, 2) if avg is not None else None,
            "median_fwd_pct": round(statistics.median(fwd) * 100, 2) if fwd else None,
            "win_pct": round(win * 100, 0) if win is not None else None,
            "avg_mfe_atr": round(statistics.fmean(mfe), 2) if mfe else None,
            "avg_mae_atr": round(statistics.fmean(mae), 2) if mae else None,
            "verdict": verdict,
        })
    order = {k: i for i, k in enumerate(KINDS)}
    return sorted(out, key=lambda r: (order.get(r["kind"], 9), -r["n"]))
