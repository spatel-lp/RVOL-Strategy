"""
Intraday Mean Reversion Module
==============================
Jeff Sun style: Enter ahead of a potential EOD long lower-wick
reversal (bullish hammer / dragonfly doji).

Rules from https://x.com/jfsrev/status/2067504357764206847

- Enter while the potential reversal candle is still forming
- Preferred entry: ORH of the developing print
- Hard risk: stop distance ≤ 40% of ATR from LoD
- Label: "Focus $TICKER (Intraday Reversal)"
"""

from dataclasses import dataclass
from typing import Optional, List, Dict
import pandas as pd
import numpy as np
from orh import fetch_intraday, compute_orh, ORHResult
from config import *


@dataclass
class IntradayMRResult:
    symbol: str
    date: str
    passed: bool
    orh: float
    current_price: float
    session_low: float
    atr_daily: float
    risk_atr: float              # (entry - stop) / ATR
    lower_wick_ratio: float      # lower wick / full range
    body_ratio: float            # body / full range
    is_hammer_like: bool
    notes: str
    label: str = ""


def _is_hammer_like(open_p: float, high: float, low: float, close: float,
                    min_lower_wick: float = 0.55,
                    max_body: float = 0.35) -> tuple[bool, float, float]:
    """
    Detect developing bullish hammer / dragonfly doji characteristics.
    Returns (is_hammer, lower_wick_ratio, body_ratio)
    """
    full_range = high - low
    if full_range <= 0:
        return False, 0.0, 0.0

    body = abs(close - open_p)
    lower_wick = min(open_p, close) - low
    upper_wick = high - max(open_p, close)

    lower_ratio = lower_wick / full_range
    body_ratio = body / full_range
    upper_ratio = upper_wick / full_range

    # Classic hammer / dragonfly: long lower wick, small body, little upper wick
    is_hammer = (
        lower_ratio >= min_lower_wick and
        body_ratio <= max_body and
        upper_ratio <= 0.25
    )
    return is_hammer, lower_ratio, body_ratio


def evaluate_intraday_mr(
    symbol: str,
    daily_atr: float = None,
    max_risk_atr: float = 0.40,
) -> Optional[IntradayMRResult]:
    """
    Evaluate a single symbol for Intraday Mean Reversion setup.
    Uses 5-min data to inspect the current/developing session.
    """
    orh_res = compute_orh(symbol, daily_atr=daily_atr, opening_minutes=30)
    if orh_res is None:
        return None

    # Fetch finer data for the candle shape
    df = fetch_intraday(symbol, interval="5m", days=3)
    if df.empty:
        return None

    df["date"] = df.index.date
    dates = sorted(df["date"].unique())
    if not dates:
        return None

    # Use exactly the session the ORH was computed on (no mixing of days)
    session = df[df["date"].astype(str) == orh_res.date]
    today = orh_res.date
    if len(session) < 3:
        return None

    # Session so far
    open_p = float(session.iloc[0]["Open"])
    high = float(session["High"].max())
    low = float(session["Low"].min())
    close = float(session.iloc[-1]["Close"])

    atr_val = daily_atr if daily_atr and daily_atr > 0 else (high - low)
    if atr_val <= 0:
        return None

    is_hammer, lower_ratio, body_ratio = _is_hammer_like(open_p, high, low, close)

    # Risk if we enter near current price / ORH and stop at session low
    entry_proxy = max(orh_res.orh, close)  # prefer ORH area
    risk_dist = entry_proxy - low
    risk_atr = risk_dist / atr_val

    notes = []
    passed = False

    if is_hammer:
        notes.append("Hammer/doji-like structure")
    else:
        notes.append(f"Lower wick {lower_ratio:.0%} / body {body_ratio:.0%}")

    if risk_atr <= max_risk_atr:
        notes.append(f"Tight risk {risk_atr:.2f} ATR (≤{max_risk_atr})")
    else:
        notes.append(f"Risk too wide {risk_atr:.2f} ATR")

    if orh_res.reclaimed_orh or close > orh_res.orh:
        notes.append("Above/reclaiming ORH")
    else:
        notes.append("Still below ORH")

    # Full pass criteria for Intraday MR
    # 1. Hammer-like developing candle
    # 2. Risk ≤ 40% ATR
    # 3. Preferably showing some reclaim / strength near ORH
    if is_hammer and risk_atr <= max_risk_atr:
        passed = True
        notes.append("INTRADAY REVERSAL CANDIDATE")

    label = f"Focus {symbol} (Intraday Reversal)" if passed else ""

    return IntradayMRResult(
        symbol=symbol,
        date=str(today),
        passed=passed,
        orh=orh_res.orh,
        current_price=close,
        session_low=low,
        atr_daily=atr_val,
        risk_atr=round(risk_atr, 2),
        lower_wick_ratio=round(lower_ratio, 2),
        body_ratio=round(body_ratio, 2),
        is_hammer_like=is_hammer,
        notes=" | ".join(notes),
        label=label,
    )


def scan_intraday_mr(
    symbols: List[str],
    daily_atrs: Dict[str, float] = None,
    max_risk_atr: float = 0.40,
) -> pd.DataFrame:
    """
    Run Intraday Mean Reversion evaluation on a list of symbols
    (usually the Mean Reversion / Focus lists).
    """
    daily_atrs = daily_atrs or {}
    rows = []

    print("\nScanning for Intraday Mean Reversion (hammer/doji + ≤40% ATR risk)...")
    for sym in symbols:
        atr = daily_atrs.get(sym)
        res = evaluate_intraday_mr(sym, daily_atr=atr, max_risk_atr=max_risk_atr)
        if res is None:
            continue
        rows.append({
            "symbol": res.symbol,
            "passed": res.passed,
            "price": round(res.current_price, 2),
            "orh": round(res.orh, 2),
            "session_low": round(res.session_low, 2),
            "risk_ATR": res.risk_atr,
            "lower_wick": res.lower_wick_ratio,
            "hammer": res.is_hammer_like,
            "label": res.label,
            "notes": res.notes,
        })

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["passed", "risk_ATR"], ascending=[False, True])
