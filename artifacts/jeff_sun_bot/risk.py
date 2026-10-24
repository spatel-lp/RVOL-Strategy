"""
Portfolio-level risk controls that sit above the per-trade 0.20% sizing:

  * drawdown brake      cut risk as equity falls from its peak, halt at the last tier
  * daily loss limit    no new trades once the day is down X% from yesterday's close
  * cluster caps        max positions per industry / sector (five semis are one bet)
  * total open-risk cap sum of (price - stop) x shares across everything open
  * leveraged exposure  sum of value x leverage for leveraged ETFs, as a share of equity

Pure functions over plain numbers and Trade-like objects, so the backtest, the paper runner and the
tests all use exactly the same rules.
"""

from typing import Dict, Iterable, Optional, Tuple

import numpy as np
import pandas as pd

from config import (
    CORR_HIGH, CORR_MIN_OBS, CORR_PAIR_BLOCK, DAILY_LOSS_LIMIT_PCT, DD_TIERS, ETF_CLUSTER, LEVERAGE_X,
    LEVERAGED_RISK_MULT, MAX_HIGHLY_CORRELATED, MAX_LEVERAGED_EXPOSURE_PCT, MAX_PER_INDUSTRY, MAX_PER_SECTOR,
    MAX_TOTAL_OPEN_RISK_PCT,
)

UNKNOWN = {"", "Unknown", None}


def leverage_of(symbol: str, is_leveraged: bool = False) -> float:
    if symbol in LEVERAGE_X:
        return float(LEVERAGE_X[symbol])
    return 2.0 if (is_leveraged or symbol in ETF_CLUSTER) else 1.0


def risk_mult_for_symbol(symbol: str, is_leveraged: bool = False) -> float:
    return LEVERAGED_RISK_MULT if leverage_of(symbol, is_leveraged) > 1.0 else 1.0


def cluster_of(symbol: str, sector: Optional[str], industry: Optional[str]) -> Tuple[str, str]:
    """(sector, industry); leveraged ETFs count with what they track."""
    if symbol in ETF_CLUSTER:
        return ETF_CLUSTER[symbol]
    return (sector or "Unknown", industry or "Unknown")


def drawdown_state(equity: float, peak: float) -> Tuple[float, float, str]:
    """(risk multiplier, drawdown fraction, reason). Tiers are checked largest first."""
    if peak <= 0:
        return 1.0, 0.0, ""
    dd = max(0.0, 1.0 - equity / peak)
    for threshold, mult in DD_TIERS:
        if dd >= threshold:
            label = "halted" if mult == 0 else f"risk x{mult:g}"
            return mult, dd, f"drawdown {dd * 100:.1f}% from peak: {label}"
    return 1.0, dd, ""


def daily_loss_halt(equity: float, day_start: float) -> Tuple[bool, float]:
    if day_start <= 0:
        return False, 0.0
    loss = 1.0 - equity / day_start
    return loss >= DAILY_LOSS_LIMIT_PCT, loss


def _last(t) -> float:
    return float(getattr(t, "last_price", 0) or t.entry_price)


def open_risk_usd(positions: Iterable) -> float:
    total = 0.0
    for t in positions:
        px = _last(t)
        for tr in t.tranches:
            total += tr.shares * max(0.0, px - tr.stop)
    return total


def leveraged_exposure_usd(positions: Iterable) -> float:
    return sum(t.remaining_size * _last(t) * getattr(t, "leverage", 1.0)
               for t in positions if getattr(t, "leverage", 1.0) > 1.0)


def cluster_counts(positions: Iterable) -> Tuple[Dict[str, int], Dict[str, int]]:
    by_industry: Dict[str, int] = {}
    by_sector: Dict[str, int] = {}
    for t in positions:
        sec, ind = cluster_of(t.symbol, getattr(t, "sector", ""), getattr(t, "industry", ""))
        if ind not in UNKNOWN:
            by_industry[ind] = by_industry.get(ind, 0) + 1
        if sec not in UNKNOWN:
            by_sector[sec] = by_sector.get(sec, 0) + 1
    return by_industry, by_sector


def check_caps(positions: Iterable, equity: float, symbol: str, sector: str, industry: str,
               leverage: float, value: float, risk_usd: float) -> Tuple[bool, str]:
    """Would adding this position break a portfolio cap? Returns (ok, reason)."""
    positions = list(positions)
    sec, ind = cluster_of(symbol, sector, industry)
    by_ind, by_sec = cluster_counts(positions)
    if ind not in UNKNOWN and by_ind.get(ind, 0) >= MAX_PER_INDUSTRY:
        return False, f"industry cap: already {by_ind[ind]} in {ind}"
    if sec not in UNKNOWN and by_sec.get(sec, 0) >= MAX_PER_SECTOR:
        return False, f"sector cap: already {by_sec[sec]} in {sec}"
    cap = MAX_TOTAL_OPEN_RISK_PCT * equity
    if open_risk_usd(positions) + risk_usd > cap + 1e-9:
        return False, f"total open-risk cap ${cap:,.0f} would be exceeded"
    if leverage > 1.0:
        lcap = MAX_LEVERAGED_EXPOSURE_PCT * equity
        if leveraged_exposure_usd(positions) + value * leverage > lcap + 1e-9:
            return False, f"leveraged exposure cap ${lcap:,.0f} would be exceeded"
    return True, ""


def pair_correlation(a: pd.Series, b: pd.Series, min_obs: int = CORR_MIN_OBS) -> Optional[float]:
    """Pearson correlation of two daily-return series on their overlapping dates; None if too little overlap."""
    both = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(both) < min_obs:
        return None
    x, y = both.iloc[:, 0].to_numpy(float), both.iloc[:, 1].to_numpy(float)
    if x.std() == 0 or y.std() == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def check_correlation(candidate: pd.Series, held: Dict[str, pd.Series]) -> Tuple[bool, str]:
    """
    Industry labels miss names that trade as one bet (a software name and a semi name in the same AI trade).
    This measures it from returns: refuse a name that is almost a clone of an open position, or one that
    would be the third highly-correlated holding.
    """
    highs = []
    for sym, series in held.items():
        c = pair_correlation(candidate, series)
        if c is None:
            continue
        if c >= CORR_PAIR_BLOCK:
            return False, f"correlation {c:.2f} with open position {sym} (limit {CORR_PAIR_BLOCK:g})"
        if c >= CORR_HIGH:
            highs.append(f"{sym} {c:.2f}")
    if len(highs) >= MAX_HIGHLY_CORRELATED:
        return False, f"already {len(highs)} open positions correlated >= {CORR_HIGH:g}: {', '.join(highs)}"
    return True, ""


def held_correlations(held: Dict[str, pd.Series]) -> list:
    """Pairs of OPEN positions with correlation >= CORR_HIGH, for the dashboard."""
    out, syms = [], list(held)
    for i, a in enumerate(syms):
        for b in syms[i + 1:]:
            c = pair_correlation(held[a], held[b])
            if c is not None and c >= CORR_HIGH:
                out.append({"a": a, "b": b, "corr": round(c, 2)})
    return sorted(out, key=lambda r: -r["corr"])
