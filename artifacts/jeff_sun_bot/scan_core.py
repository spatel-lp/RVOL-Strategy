"""
Headless scan logic shared by the printed scanner (live_scan.py) and the paper-trading
automation (run_daily.py). Pure functions: no printing, no file writes, no network.
"""

from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config import (
    MAX_ATR_EXT_50, MIN_ADR_PCT, MIN_RVOL, LOD_ATR_MAX, MIN_VARS, STALK_MAX_ATR_EXT, VCP_MIN_SCORE,
)
from entry_rules import evaluate_entry
from filters import entry_filter_failures, linearity_r2
from strategy import JeffSunStrategy

# ---------------------------------------------------------------------------------------------
# HOOK for your own Focus-list rules (e.g. an earnings blackout). Each callable gets
# (symbol, row_dict) and returns a short reason string to BLOCK the name, or "" to allow it.
# Blocked names are logged as rejects, so the filter audit will tell you what the rule costs/saves.
#     def earnings_soon(symbol, row): return "earnings within 1 day" if ... else ""
#     scan_core.FOCUS_BLOCKERS.append(earnings_soon)
# ---------------------------------------------------------------------------------------------
FOCUS_BLOCKERS: List[Callable[[str, dict], str]] = []


def data_flag(row: pd.Series) -> str:
    """Cheap sanity check on a row. ATR% should not sit far below ADR% (it caught the MRNA row)."""
    close = float(row["Close"])
    atr_pct = float(row.get("ATR", 0)) / close * 100 if close else 0.0
    adr = float(row.get("ADR_pct", 0))
    if adr > 0 and atr_pct < 0.6 * adr:
        return f"ATR% {atr_pct:.1f} far below ADR% {adr:.1f}: check data"
    return ""


def compute_rows(market_data: Dict[str, pd.DataFrame]) -> Tuple[List[dict], Dict[str, float], Dict[str, float]]:
    strat = JeffSunStrategy()
    rows: List[dict] = []
    daily_atrs: Dict[str, float] = {}
    avg_vols: Dict[str, float] = {}

    for sym, df in market_data.items():
        if len(df) < 60:
            continue
        i = len(df) - 1
        row = df.iloc[i]

        # Minimum for Stalk List
        if not bool(row.get("Stage2", False)):
            continue
        if row["Close"] < row["SMA20"]:
            continue
        if row.get("ADR_pct", 0) < 2.5:
            continue

        ext = float(row.get("ATR_ext_50", 0))
        if not pd.notna(ext) or ext > STALK_MAX_ATR_EXT:  # too extended even to stalk
            continue

        score = strat.compute_setup_score(df, i)
        checklist = evaluate_entry(
            df, i,
            min_rvol=MIN_RVOL,
            max_lod_atr=LOD_ATR_MAX,
            min_vcp=VCP_MIN_SCORE,
            min_adr=MIN_ADR_PCT,
            prefer_orh_proxy=True,
        )

        funds = df.attrs.get("fundamentals", {})
        mcap = funds.get("marketCap")
        atr = float(row.get("ATR", 0))
        avg_vol = float(row.get("AvgVol50", 0) or 0)
        daily_atrs[sym] = atr
        avg_vols[sym] = avg_vol

        rows.append({
            "symbol": sym,
            "score": round(score, 3),
            "close": round(float(row["Close"]), 2),
            "ADR%": round(float(row.get("ADR_pct", 0)), 1),
            "RVOL": round(float(row.get("RVOL", 0)), 2),
            "VCP": round(float(row.get("VCP_score", 0)), 2),
            "ATR_ext": round(ext, 1),
            "MR": round(float(row.get("MR_score", 0)), 2),
            "VARS": round(float(row.get("VARS", 0)), 1) if "VARS" in df.columns else float("nan"),
            "daily_ok": bool(checklist.passed),
            "trend_ok": bool(row["Close"] >= row["SMA10"] and row["Close"] >= row["SMA20"]),
            "LEV": "Y" if df.attrs.get("is_leveraged") else "",
            "mcap_B": round(mcap / 1e9, 1) if mcap else None,
            "sector": funds.get("sector") or "",
            "industry": funds.get("industry") or "",
            "LIN": round(float(linearity_r2(df["Close"], i)), 2),
            "chop": "; ".join(entry_filter_failures(df, i)),
            "ATR": round(atr, 4),
            "AvgVol50": round(avg_vol, 0),
            "flag": data_flag(row),
        })
    return rows, daily_atrs, avg_vols


def select_lists(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Stalk, Focus and Mean-reversion lists from the scan rows."""
    stalk = df[df["score"] >= 0.40].head(18)

    # Focus = ALL must-haves (RULES.md section 3): Stage 2 (already required in compute_rows), above 10/20-MA,
    # VCP >= min, not extended beyond the 4x cap, ADR >= min, and VARS >= min when available.
    must = (
        (df["trend_ok"])
        & (df["VCP"] >= VCP_MIN_SCORE)
        & (df["ATR_ext"] <= MAX_ATR_EXT_50)
        & (df["ADR%"] >= MIN_ADR_PCT)
        & (df["VARS"].isna() | (df["VARS"] >= MIN_VARS))
        & (df["chop"] == "")
    )
    ranked = df[must].sort_values(["VCP", "score"], ascending=[False, False])
    if FOCUS_BLOCKERS:
        # Blockers may hit the network (earnings), so they run best-first on names that already pass every other
        # rule and stop as soon as 12 names are kept. A blocked name never takes one of the 12 slots.
        keep = []
        for idx, r in ranked.iterrows():
            if not any(fn(r["symbol"], r.to_dict()) for fn in FOCUS_BLOCKERS):
                keep.append(idx)
            if len(keep) == 12:
                break
        focus = ranked.loc[keep]
    else:
        focus = ranked.head(12)

    mr_list = df[(df["MR"] >= 0.45) & (df["ATR_ext"] <= -1.5) & (df["ATR_ext"] >= -4.5)].copy()
    mr_list = mr_list.sort_values("MR", ascending=False).head(10)
    return stalk, focus, mr_list


def focus_failure(row: dict) -> str:
    """First Focus rule a stalk-list name fails ('' if it passes). Short stable categories, used by the filter audit."""
    if not row.get("trend_ok"):
        return "not above 10/20-MA"
    if row.get("VCP", 0) < VCP_MIN_SCORE:
        return "VCP too loose"
    if row.get("ATR_ext", 0) > MAX_ATR_EXT_50:
        return f"extended beyond {MAX_ATR_EXT_50:g}x ATR%"
    if row.get("ADR%", 0) < MIN_ADR_PCT:
        return "ADR below minimum"
    v = row.get("VARS")
    if v is not None and not pd.isna(v) and v < MIN_VARS:
        return "relative strength (VARS) low"
    if row.get("chop"):
        return "choppy trend (low linearity)"
    for fn in FOCUS_BLOCKERS:
        why = fn(row["symbol"], row)
        if why:
            return why
    return ""
