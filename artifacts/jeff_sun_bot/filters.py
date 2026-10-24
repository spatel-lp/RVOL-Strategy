"""
Entry filters from Jeff's hard-rule list that the first version did not have:
  * prefer recent price linearity (avoid names that move a lot but chop, with low win rate and drawdowns): ACTIVE
  * no entry into an immediate gap-resistance zone: written and tested, but DEFERRED (config.USE_GAP_RESISTANCE = False)
Both work on a daily frame at bar index i and use only data up to and including that bar.
"""

from typing import List, Optional

import numpy as np
import pandas as pd

from config import GAP_RES_ATR, GAP_RES_LOOKBACK, LINEARITY_DAYS, MIN_LINEARITY, USE_GAP_RESISTANCE


def linearity_r2(close: pd.Series, i: Optional[int] = None, n: int = LINEARITY_DAYS) -> float:
    """R^2 of a straight-line fit to log price over the last n bars; 0 if the trend is not up."""
    i = len(close) - 1 if i is None else i
    if i < n - 1:
        return float("nan")
    y = np.log(close.iloc[i - n + 1: i + 1].to_numpy(dtype=float))
    x = np.arange(n, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    if slope <= 0:
        return 0.0
    resid = y - (slope * x + intercept)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return float(1.0 - (resid ** 2).sum() / ss_tot) if ss_tot > 0 else 0.0


def gap_resistance(df: pd.DataFrame, i: int, atr: float,
                   lookback: int = GAP_RES_LOOKBACK, atr_mult: float = GAP_RES_ATR) -> Optional[dict]:
    """
    Most recent unfilled gap-down above price that sits within `atr_mult` ATR of the close (or already
    contains it). A gap-down is a bar whose HIGH is below the previous bar's LOW; the zone is
    [that high, previous low], shrunk by anything that has traded back up into it since. A zone that has
    been fully refilled no longer counts. Returns {"top","bottom","bar"} or None.
    """
    hi = df["High"].to_numpy(dtype=float)
    lo = df["Low"].to_numpy(dtype=float)
    close = float(df["Close"].iloc[i])
    best = None
    for j in range(max(1, i - lookback + 1), i + 1):
        if hi[j] < lo[j - 1]:
            top, bottom = lo[j - 1], hi[j]
            later_high = hi[j + 1: i + 1].max() if j < i else hi[j]
            if later_high >= top:
                continue                                  # fully filled
            eff_bottom = max(bottom, later_high)
            if close < top and (eff_bottom - close) <= atr_mult * atr:
                best = {"top": float(top), "bottom": float(eff_bottom), "bar": int(j)}
    return best


def entry_filter_failures(df: pd.DataFrame, i: int) -> List[str]:
    """Human-readable reasons this bar fails the gap-resistance / linearity rules (empty = passes)."""
    out: List[str] = []
    atr = float(df["ATR"].iloc[i]) if "ATR" in df.columns else float("nan")
    if USE_GAP_RESISTANCE and np.isfinite(atr) and atr > 0:
        g = gap_resistance(df, i, atr)
        if g:
            out.append(f"gap resistance {g['bottom']:.2f}-{g['top']:.2f}")
    r2 = linearity_r2(df["Close"], i)
    if np.isfinite(r2) and r2 < MIN_LINEARITY:
        out.append(f"choppy trend (R2 {r2:.2f} < {MIN_LINEARITY:.2f})")
    return out
