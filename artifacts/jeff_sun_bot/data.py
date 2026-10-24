"""
Data + universe – now with leveraged ETF preference
"""

import yfinance as yf
import pandas as pd
import numpy as np
from typing import List, Dict
from indicators import add_indicators
from config import (EXCLUDED_INDUSTRY_KEYWORDS, EXCLUDED_SYMBOLS, LEVERAGED_UNIVERSE_SHARE)


USE_FUND_CACHE = False   # the automation turns this on


def fetch_history(symbol: str, period: str = "2y", interval: str = "1d") -> pd.DataFrame:
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval, auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        df = df[["Open", "High", "Low", "Close", "Volume"]]
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df
    except Exception as e:
        print(f"  Error {symbol}: {e}")
        return pd.DataFrame()


def _fund_cache_path() -> str:
    import os
    from config import PAPER_STATE_DIR
    return os.path.join(os.environ.get("PAPER_STATE_DIR", PAPER_STATE_DIR), "fundamentals_cache.json")


def _fund_cache_load() -> dict:
    import json, os
    try:
        with open(_fund_cache_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _fund_cache_save(cache: dict) -> None:
    import json, os
    path = _fund_cache_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(cache, f)
    except OSError:
        pass


def get_basic_fundamentals(symbol: str, use_cache: bool = False) -> dict:
    """yfinance .info is slow and rate-limited. With use_cache the result is kept for FUNDAMENTALS_TTL_DAYS."""
    if use_cache:
        import time
        from config import FUNDAMENTALS_TTL_DAYS
        cache = _fund_cache_load()
        hit = cache.get(symbol)
        if hit and time.time() - hit.get("ts", 0) < FUNDAMENTALS_TTL_DAYS * 86400 and hit.get("data"):
            return hit["data"]
        fresh = get_basic_fundamentals(symbol, use_cache=False)
        if fresh:
            cache[symbol] = {"ts": time.time(), "data": fresh}
            _fund_cache_save(cache)
        return fresh
    try:
        t = yf.Ticker(symbol)
        info = t.info or {}
        return {
            "marketCap": info.get("marketCap"),
            "forwardPE": info.get("forwardPE"),
            "trailingPE": info.get("trailingPE"),
            "pegRatio": info.get("pegRatio"),
            "revenueGrowth": info.get("revenueGrowth"),
            "earningsGrowth": info.get("earningsGrowth"),
            "earningsQuarterlyGrowth": info.get("earningsQuarterlyGrowth"),
            "profitMargins": info.get("profitMargins"),
            "floatShares": info.get("floatShares"),
            "averageVolume": info.get("averageVolume"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "isLeveraged": "leveraged" in (info.get("longName") or "").lower() or symbol in LEVERAGED_ETFS,
        }
    except Exception:
        return {}


# Explicit leveraged ETFs Jeff likes for higher ADR%
LEVERAGED_ETFS = {
    "SOXL", "TQQQ", "TECL", "FNGU", "TNA", "SPXL", "UPRO", "TMF",
    "LABU", "HIBL", "CURE", "DFEN", "WEBL", "FAS", "CWEB",
    "NVDL", "TSLL", "AMDL", "AAPU", "CRMG", "AAPB", "MSFU", "METU",
}

# Mapping underlying → preferred leveraged version (when available)
LEVERAGED_MAP = {
    "NVDA": "NVDL",
    "TSLA": "TSLL",
    "AMD": "AMDL",
    "AAPL": "AAPU",
    "CRM": "CRMG",
    "MSFT": "MSFU",
    "META": "METU",
    "SOXX": "SOXL",
    "QQQ": "TQQQ",
    "SPY": "SPXL",
    "IWM": "TNA",
}


def prepare_symbol(symbol: str, benchmark: pd.DataFrame = None) -> pd.DataFrame:
    df = fetch_history(symbol)
    if df.empty or len(df) < 120:
        return pd.DataFrame()
    df = add_indicators(df, benchmark)
    funds = get_basic_fundamentals(symbol, use_cache=USE_FUND_CACHE)
    if is_excluded(symbol, funds):
        return pd.DataFrame()
    df.attrs["fundamentals"] = funds
    df.attrs["symbol"] = symbol
    df.attrs["is_leveraged"] = symbol in LEVERAGED_ETFS
    return df.dropna(subset=["ATR", "SMA50"])


def is_excluded(symbol: str, funds: dict = None) -> bool:
    """Jeff excludes biotechs (gap risk). Exposure only through ETFs such as LABU/LABD."""
    if symbol in EXCLUDED_SYMBOLS:
        return True
    industry = ((funds or {}).get("industry") or "").lower()
    return any(k in industry for k in EXCLUDED_INDUSTRY_KEYWORDS)


def get_universe(max_symbols: int = 65) -> List[str]:
    """
    Hand-picked universe (NOTE: built from names that have already worked, so any
    backtest on it carries survivorship bias - use a point-in-time universe for real testing).
    Leveraged ETFs get a reserved share of slots in a deterministic order instead of
    being truncated away.
    """
    growth_software = [
        "CRWD", "NET", "DDOG", "SNOW", "MDB", "GTLB", "BILL", "TOST",
        "PATH", "AI", "PLTR", "APP", "DUOL", "HUBS", "VEEV", "WDAY", "CRM", "NOW", "TEAM"
    ]
    semis = [
        "NVDA", "AMD", "AVGO", "SMCI", "ARM", "VRT", "MOD", "AEIS",
        "FORM", "AMKR", "LSCC", "ONTO", "KLAC", "LRCX"
    ]
    consumer = ["CELH", "CAVA", "ANF", "DECK", "AEO", "URBN", "LULU", "ONON"]
    healthcare = ["HIMS", "OSCR"]          # biotechs removed (see config.EXCLUDED_SYMBOLS)
    industrials = ["STRL", "FIX", "PWR", "EME", "PRIM", "DY", "GVA"]
    fintech = ["SOFI", "HOOD", "COIN", "UPST", "AFRM"]
    other = ["TSLA", "RIVN", "ASTS", "RKLB", "JOBY"]

    stocks = []
    seen = set()
    for s in (growth_software + semis + consumer + healthcare + industrials + fintech + other):
        if s not in seen and not is_excluded(s):
            seen.add(s)
            stocks.append(s)

    lev = sorted(LEVERAGED_ETFS)           # sorted: set order is random per process
    lev_slots = min(len(lev), max(1, int(max_symbols * LEVERAGED_UNIVERSE_SHARE)))
    stock_slots = max_symbols - lev_slots
    return stocks[:stock_slots] + lev[:lev_slots]


def add_cross_sectional_ranks(data: Dict[str, pd.DataFrame]) -> None:
    """
    Rank VARS and momentum 0-100 / 0-1 ACROSS the universe on each date (in place).
    Uses only same-date information, so no look-ahead.
    """
    vars_panel = pd.DataFrame({s: d["VARS_raw"] for s, d in data.items() if "VARS_raw" in d.columns})
    mom_panel = pd.DataFrame({s: d["Mom_raw"] for s, d in data.items() if "Mom_raw" in d.columns})
    # Need a reasonable cross-section for a rank to mean anything
    min_names = 8
    vars_rank = vars_panel.rank(axis=1, pct=True) * 100
    vars_rank = vars_rank.where(vars_panel.notna().sum(axis=1) >= min_names)
    mom_rank = mom_panel.rank(axis=1, pct=True)
    mom_rank = mom_rank.where(mom_panel.notna().sum(axis=1) >= min_names)
    for s, d in data.items():
        if s in vars_rank.columns:
            d["VARS"] = vars_rank[s].reindex(d.index)
        if s in mom_rank.columns:
            d["Mom_score"] = mom_rank[s].reindex(d.index).fillna(0.5)


def load_market_data(symbols: List[str], show_progress: bool = True) -> Dict[str, pd.DataFrame]:
    if show_progress:
        print("Fetching benchmark (SPY)...")
    spy = fetch_history("SPY", period="2y")
    if not spy.empty:
        spy = add_indicators(spy)

    data = {}
    for i, sym in enumerate(symbols):
        if show_progress:
            print(f"[{i+1}/{len(symbols)}] {sym}...", end=" ", flush=True)
        df = prepare_symbol(sym, benchmark=spy)
        if not df.empty:
            data[sym] = df
            if show_progress:
                funds = df.attrs.get("fundamentals", {})
                mcap = funds.get("marketCap")
                mcap_str = f"${mcap/1e9:.1f}B" if mcap else "n/a"
                lev = " [LEV]" if df.attrs.get("is_leveraged") else ""
                ext = df["ATR_ext_50"].iloc[-1] if "ATR_ext_50" in df.columns else 0
                print(f"OK  ADR={df['ADR_pct'].iloc[-1]:.1f}%  Ext={ext:.1f}x  MCap={mcap_str}{lev}")
        else:
            if show_progress:
                print("failed")
    add_cross_sectional_ranks(data)
    if show_progress:
        print(f"\nLoaded {len(data)} / {len(symbols)} symbols.")
    return data


def fundamental_growth_proxy(funds: dict) -> float:
    score = 0.0
    count = 0
    eg = funds.get("earningsGrowth") or funds.get("earningsQuarterlyGrowth")
    if eg is not None:
        score += min(max(eg, 0) / 0.50, 1.0)
        count += 1
    rg = funds.get("revenueGrowth")
    if rg is not None:
        score += min(max(rg, 0) / 0.40, 1.0)
        count += 1
    peg = funds.get("pegRatio")
    if peg is not None and peg > 0:
        score += max(0, 1 - (peg / 3.0))
        count += 1
    if count == 0:
        return 0.4
    return score / count
