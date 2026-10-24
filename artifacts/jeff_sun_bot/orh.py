"""
Opening Range High (ORH) Module
================================
Fetches recent intraday data to compute true Opening Range High
and evaluates M30 Re-ORH style conditions used by Jeff Sun.

Jeff terminology:
  ORH          = high of the opening range (first 5–30 minutes)
  M30 Re-ORH   = price reclaiming ORH after the first 30 minutes
  RVOL         = relative volume on the reclaim / breakout bar
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple
from dataclasses import dataclass
from config import ORH_MIN_DAYVOL_PCT, ORH_MAX_LOD_ATR, ORH_MIN_TIME_RVOL, PROFILE_MIN_SESSIONS, PROFILE_SESSIONS


@dataclass
class ORHResult:
    symbol: str
    date: str
    orh: float                    # Opening Range High
    orl: float                    # Opening Range Low
    open_price: float
    current_price: float
    reclaimed_orh: bool           # Currently above ORH
    m30_re_orh: bool              # Reclaimed after ~30 min (approx)
    rvol_at_reclaim: float        # legacy: reclaim-bar volume / mean bar volume (NOT vs 50-day avg)
    distance_to_orh_pct: float
    session_high: float
    session_low: float
    atr_daily: float
    lod_atr_multiple: float       # (current - session_low) / ATR
    notes: str
    valid: bool = True
    dayvol_pct: Optional[float] = None   # cumulative session volume at reclaim / avg full-day volume
    broke_without_dip: bool = False      # held above ORH from the first post-range bar (breakout, not a re-claim)
    time_rvol: Optional[float] = None    # volume so far vs what is typical BY THIS TIME of day (>1 = heavier than usual)


def fetch_intraday(symbol: str, interval: str = "5m", days: int = 5) -> pd.DataFrame:
    """Fetch recent intraday bars"""
    try:
        ticker = yf.Ticker(symbol)
        period = f"{days}d" if days <= 5 else ("1mo" if days <= 30 else "60d")
        df = ticker.history(period=period, interval=interval, auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        df = df[["Open", "High", "Low", "Close", "Volume"]]
        df.index = pd.to_datetime(df.index)
        # Make timezone-naive for easier handling
        if df.index.tz is not None:
            df.index = df.index.tz_convert("America/New_York").tz_localize(None)
        return df
    except Exception as e:
        print(f"  Intraday fetch error {symbol}: {e}")
        return pd.DataFrame()


def volume_profile(df: pd.DataFrame, today, n_sessions: int = PROFILE_SESSIONS) -> Optional[np.ndarray]:
    """
    Typical cumulative share of a session's volume traded by each 5-minute bar (index 0 = 09:30 bar), from
    the symbol's own recent full sessions. Volume is front-loaded, so "20% of an average day by 10:30"
    is a very different bar from "20% by 15:00". None if there are not enough clean sessions.
    """
    dates = df.index.date
    sessions = [g for d, g in df.groupby(dates) if d != today and len(g) >= 60][-n_sessions:]
    curves = []
    for g in sessions:
        v = g["Volume"].to_numpy(dtype=float)
        if v.sum() <= 0:
            continue
        cum = np.cumsum(v) / v.sum()
        arr = np.ones(78)
        arr[: min(78, len(cum))] = cum[:78]
        curves.append(arr)
    if len(curves) < PROFILE_MIN_SESSIONS:
        return None
    return np.mean(curves, axis=0)


def compute_orh(
    symbol: str,
    daily_atr: float = None,
    opening_minutes: int = 30,
    interval: str = "5m",
    avg_daily_volume: Optional[float] = None,
) -> Optional[ORHResult]:
    """
    Compute Opening Range High for the most recent session
    and evaluate reclaim conditions.
    """
    df = fetch_intraday(symbol, interval=interval, days=PROFILE_SESSIONS + 5)
    if df.empty or len(df) < 12:
        return None

    # Group by date
    df["date"] = df.index.date
    dates = sorted(df["date"].unique())
    if not dates:
        return None

    today = dates[-1]
    session = df[df["date"] == today].copy()

    # Opening range = first N minutes (5m bars -> 30 min = 6 bars)
    bars_for_or = max(1, opening_minutes // 5)
    if len(session) < bars_for_or:
        # The opening range is still forming. Do NOT silently fall back to yesterday's
        # session (that mixed two different days in the intraday-reversal check).
        return None
    opening_range = session.iloc[:bars_for_or]

    orh = float(opening_range["High"].max())
    orl = float(opening_range["Low"].min())
    open_price = float(session.iloc[0]["Open"])
    session_high = float(session["High"].max())
    session_low = float(session["Low"].min())
    current_price = float(session.iloc[-1]["Close"])

    reclaimed = current_price > orh

    # True M30 Re-ORH: after the opening range, price must CLOSE BELOW the ORH at some
    # point and later CLOSE BACK ABOVE it. Holding above from the start is a breakout, not a re-claim.
    post_or = session.iloc[bars_for_or:]
    m30_re = False
    broke_without_dip = False
    rvol_at_reclaim = 1.0
    dayvol_pct = None
    time_rvol = None
    signal_pos = None                    # position (in session) of the bar we treat as the trigger

    dipped = False
    for pos, (_, bar) in enumerate(post_or.iterrows(), start=bars_for_or):
        c = float(bar["Close"])
        if c < orh:
            dipped = True
        elif c > orh:
            if dipped:
                m30_re = True
            else:
                broke_without_dip = True
            signal_pos = pos
            break

    if signal_pos is not None:
        bar = session.iloc[signal_pos]
        avg_bar = session["Volume"].mean()
        if avg_bar > 0:
            rvol_at_reclaim = float(bar["Volume"] / avg_bar)
        if avg_daily_volume and avg_daily_volume > 0:
            dayvol_pct = float(session["Volume"].iloc[: signal_pos + 1].sum() / avg_daily_volume)
            profile = volume_profile(df, today)
            if profile is not None:
                expected = avg_daily_volume * float(profile[min(signal_pos, 77)])
                if expected > 0:
                    time_rvol = float(session["Volume"].iloc[: signal_pos + 1].sum() / expected)

    # LoD / ATR multiple (using daily ATR if provided)
    atr = daily_atr if daily_atr and daily_atr > 0 else (session_high - session_low)
    lod_atr = (current_price - session_low) / atr if atr > 0 else 9.0

    dist_pct = (current_price / orh - 1) * 100 if orh > 0 else 0

    notes = []
    if m30_re:
        notes.append("M30 Re-ORH confirmed")
    elif broke_without_dip:
        notes.append("Held above ORH (breakout, no re-claim)")
    elif reclaimed:
        notes.append("Above ORH")
    else:
        notes.append("Below ORH")

    if time_rvol is not None:
        notes.append(f"time-adjusted RVOL {time_rvol:.1f}x ({dayvol_pct:.0%} of an average day)")
    elif dayvol_pct is not None:
        notes.append(f"{dayvol_pct:.0%} of avg day's volume by trigger bar (no profile)")
    if lod_atr <= 0.60:
        notes.append(f"Tight LoD/ATR {lod_atr:.2f}")
    else:
        notes.append(f"Wide LoD/ATR {lod_atr:.2f}")

    return ORHResult(
        symbol=symbol,
        date=str(today),
        orh=orh,
        orl=orl,
        open_price=open_price,
        current_price=current_price,
        reclaimed_orh=reclaimed,
        m30_re_orh=m30_re,
        rvol_at_reclaim=round(rvol_at_reclaim, 2),
        distance_to_orh_pct=round(dist_pct, 2),
        session_high=session_high,
        session_low=session_low,
        atr_daily=atr,
        lod_atr_multiple=round(lod_atr, 2),
        notes=" | ".join(notes),
        valid=True,
        dayvol_pct=None if dayvol_pct is None else round(dayvol_pct, 3),
        time_rvol=None if time_rvol is None else round(time_rvol, 2),
        broke_without_dip=broke_without_dip,
    )


def evaluate_orh_entry(
    symbol: str,
    daily_atr: float = None,
    avg_daily_volume: float = None,
    min_dayvol_pct: float = ORH_MIN_DAYVOL_PCT,
    min_time_rvol: float = ORH_MIN_TIME_RVOL,
    max_lod_atr: float = ORH_MAX_LOD_ATR,
) -> Tuple[bool, Optional[ORHResult], str]:
    """
    Full ORH-based entry decision for a single symbol.
    Returns (passed, ORHResult, reason)
    """
    result = compute_orh(symbol, daily_atr=daily_atr, avg_daily_volume=avg_daily_volume)
    if result is None or not result.valid:
        return False, None, "No intraday data (or opening range still forming)"

    if not result.reclaimed_orh:
        return False, result, "Price still below ORH"

    if result.dayvol_pct is None:
        return False, result, "No average-volume reference; cannot judge RVOL"
    if result.time_rvol is not None:
        if result.time_rvol < min_time_rvol:
            return False, result, (f"Volume light for this time of day: {result.time_rvol:.1f}x typical "
                                   f"(< {min_time_rvol:.1f}x)")
    elif result.dayvol_pct < min_dayvol_pct:       # no usable profile: fall back to the flat rule
        return False, result, f"Volume at trigger only {result.dayvol_pct:.0%} of an average day (< {min_dayvol_pct:.0%})"

    if result.lod_atr_multiple > max_lod_atr:
        return False, result, f"LoD/ATR {result.lod_atr_multiple:.2f} exceeds {max_lod_atr}"

    return True, result, result.notes


def scan_orh_for_list(symbols: list, daily_atrs: Dict[str, float] = None,
                      avg_volumes: Dict[str, float] = None) -> pd.DataFrame:
    """
    Run ORH evaluation on a list of symbols (usually the Focus List).
    """
    daily_atrs = daily_atrs or {}
    avg_volumes = avg_volumes or {}
    rows = []

    print("\nEvaluating true ORH (5-min data) on Focus List names...")
    for sym in symbols:
        atr = daily_atrs.get(sym)
        passed, result, reason = evaluate_orh_entry(sym, daily_atr=atr, avg_daily_volume=avg_volumes.get(sym))
        if result is None:
            continue
        rows.append({
            "symbol": sym,
            "orh_passed": passed,
            "orh": round(result.orh, 2),
            "price": round(result.current_price, 2),
            "reclaimed": result.reclaimed_orh,
            "m30_re": result.m30_re_orh,
            "dayvol%": result.dayvol_pct,
            "lod_atr": result.lod_atr_multiple,
            "dist_orh%": result.distance_to_orh_pct,
            "notes": result.notes,
        })

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("orh_passed", ascending=False)
