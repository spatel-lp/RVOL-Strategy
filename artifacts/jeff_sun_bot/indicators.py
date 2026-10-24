"""
Technical indicators – enhanced with ATR% from 50-MA + VARS
"""

import pandas as pd
import numpy as np

from config import ATR_EXT_MODE, MA_TREND


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    # Wilder smoothing (RMA) - matches TradingView's default ATR
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adr_pct(df: pd.DataFrame, period: int = 20) -> pd.Series:
    daily_range = (df["High"] - df["Low"]) / df["Close"] * 100
    return daily_range.rolling(period).mean()


def relative_volume(df: pd.DataFrame, period: int = 50) -> pd.Series:
    avg_vol = df["Volume"].rolling(period).mean()
    return df["Volume"] / avg_vol.replace(0, np.nan)


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def atr_pct_from_ma(df: pd.DataFrame, ma_period: int = 50, atr_period: int = 14) -> pd.Series:
    """
    Extension from the MA in ATR terms (used to avoid chasing and for partial profit-taking).

    ATR_EXT_MODE = "atr_pct": ((close / MA) - 1) / (ATR / close)   [ATR% multiple]
    ATR_EXT_MODE = "atr"    : (close - MA) / ATR                    [plain ATR multiple]
    """
    close = df["Close"]
    ma = sma(close, ma_period)
    atr_val = atr(df, atr_period).replace(0, np.nan)
    if ATR_EXT_MODE == "atr":
        return (close - ma) / atr_val
    return ((close / ma) - 1.0) / (atr_val / close)


def vars_rs(df: pd.DataFrame, benchmark: pd.DataFrame, period: int = 63) -> pd.Series:
    """
    Volatility-Adjusted Relative Strength (VARS) approximation.
    Better than raw RS because it accounts for each stock's own volatility.
    """
    stock_ret = df["Close"] / df["Close"].shift(period) - 1
    bench_ret = benchmark["Close"] / benchmark["Close"].shift(period) - 1

    stock_vol = df["Close"].pct_change().rolling(period).std()
    bench_vol = benchmark["Close"].pct_change().rolling(period).std()

    # Avoid division by zero
    stock_vol = stock_vol.replace(0, np.nan)
    bench_vol = bench_vol.replace(0, np.nan)

    # Volatility-adjusted returns
    stock_adj = stock_ret / stock_vol
    bench_adj = bench_ret / bench_vol

    # Raw volatility-adjusted relative strength. It is ranked 0-100 ACROSS the universe
    # on each date in data.add_cross_sectional_ranks (ranking a stock against its own
    # history, as before, leaked future data and measured the wrong thing).
    return stock_adj - bench_adj


def relative_strength(df: pd.DataFrame, benchmark: pd.DataFrame, period: int = 63) -> pd.Series:
    """Simple classic RS (kept for compatibility)"""
    stock_ret = df["Close"] / df["Close"].shift(period)
    bench_ret = benchmark["Close"] / benchmark["Close"].shift(period)
    return (stock_ret / bench_ret) * 100


def is_stage2(df: pd.DataFrame, ma_period: int = 50) -> pd.Series:
    ma = sma(df["Close"], ma_period)
    rising = ma > ma.shift(8)
    above = df["Close"] > ma
    return above & rising


def vcp_score(df: pd.DataFrame, lookback: int = 25) -> pd.Series:
    """Multi-factor VCP / contraction score (0–1)"""
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    vol = df["Volume"]
    atr_val = atr(df, 14)

    long_range = high.rolling(lookback).max() - low.rolling(lookback).min()
    short_range = high.rolling(5).max() - low.rolling(5).min()
    range_ratio = (short_range / long_range.replace(0, np.nan)).clip(0, 1)
    range_score = (1 - range_ratio).clip(0, 1)

    vol_ma20 = vol.rolling(20).mean()
    vol_ma5 = vol.rolling(5).mean()
    vol_ratio = (vol_ma5 / vol_ma20.replace(0, np.nan)).clip(0, 2)
    vol_score = (1.2 - vol_ratio).clip(0, 1)

    hl_score = ((low > low.shift(10)) & (low > low.shift(5))).astype(float)

    daily_move = (high - low) / atr_val.replace(0, np.nan)
    tight_score = (1.5 - daily_move.rolling(5).mean()).clip(0, 1)

    sma10 = sma(close, 10)
    sma20 = sma(close, 20)
    ma_score = ((close > sma10) & (close > sma20) & (sma10 > sma20.shift(3))).astype(float)

    score = (
        0.30 * range_score +
        0.25 * vol_score +
        0.15 * hl_score +
        0.15 * tight_score +
        0.15 * ma_score
    )
    return score.clip(0, 1)


def momentum_raw(df: pd.DataFrame) -> pd.Series:
    """Blend of 1/3/6-month returns. Ranked across the universe per date in data.py."""
    ret_1m = df["Close"] / df["Close"].shift(21) - 1
    ret_3m = df["Close"] / df["Close"].shift(63) - 1
    ret_6m = df["Close"] / df["Close"].shift(126) - 1
    return 0.40 * ret_1m + 0.35 * ret_3m + 0.25 * ret_6m


def mean_reversion_score(df: pd.DataFrame) -> pd.Series:
    """
    Jeff-style Mean Reversion / Pullback score (0–1).
    Higher = more attractive stretched-downside + recovery setup.

    Factors:
    - Negative ATR extension from 50-MA (oversold)
    - Recent lower rate of change / slowing decline
    - Holding or reclaiming short-term structure
    - Volume behavior on the bounce attempt
    """
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    atr_val = atr(df, 14)
    sma50 = sma(close, 50)
    sma10 = sma(close, 10)
    sma20 = sma(close, 20)

    # 1. How stretched below 50-MA (in ATR terms)
    # Negative extension = below MA. We want moderately oversold (-1.5 to -4 ATR)
    ext = atr_pct_from_ma(df, 50, 14)
    # Score higher when between -1.5 and -4.0
    stretch = np.where(
        (ext <= -1.5) & (ext >= -4.5),
        1.0 - (np.abs(ext + 2.5) / 2.5),  # peak around -2.5
        np.where(ext < -4.5, 0.3, 0.0)     # too deep = lower score
    )
    stretch = pd.Series(stretch, index=df.index).clip(0, 1)

    # 2. Rate of change slowing (last 5 days vs prior 10)
    roc5 = close.pct_change(5)
    roc10 = close.pct_change(10)
    slowing = ((roc5 > roc10) & (roc5 > -0.08)).astype(float)  # decline decelerating

    # 3. Recent bounce / higher low attempt
    higher_low = (low > low.shift(3)).astype(float)
    above_sma10 = (close > sma10).astype(float) * 0.5

    # 4. Tight range after decline (contraction after stretch)
    recent_range = (high.rolling(5).max() - low.rolling(5).min()) / atr_val
    tight = (1.2 - recent_range).clip(0, 1)

    score = (
        0.40 * stretch +
        0.20 * slowing +
        0.15 * higher_low +
        0.10 * above_sma10 +
        0.15 * tight
    )
    return pd.Series(score, index=df.index).clip(0, 1)


def add_indicators(df: pd.DataFrame, benchmark: pd.DataFrame = None) -> pd.DataFrame:
    df = df.copy()
    df["ATR"] = atr(df, 14)
    df["ADR_pct"] = adr_pct(df, 20)
    df["RVOL"] = relative_volume(df, 50)
    df["SMA10"] = sma(df["Close"], 10)
    df["SMA20"] = sma(df["Close"], 20)
    df["SMA50"] = sma(df["Close"], 50)
    df["EMA21"] = ema(df["Close"], 21)
    df["Stage2"] = is_stage2(df, 50)
    df["VCP_score"] = vcp_score(df, 25)
    df["Mom_raw"] = momentum_raw(df)
    df["Mom_score"] = 0.5            # placeholder until cross-sectional ranking in data.py
    df["AvgVol50"] = df["Volume"].rolling(50).mean()
    df["SMA200"] = sma(df["Close"], MA_TREND)

    # New: ATR% extension from 50-MA (Jeff's key extension metric)
    df["ATR_ext_50"] = atr_pct_from_ma(df, ma_period=50, atr_period=14)

    # Mean Reversion / Pullback score
    df["MR_score"] = mean_reversion_score(df)

    if benchmark is not None and not benchmark.empty:
        common = df.index.intersection(benchmark.index)
        if len(common) > 50:
            df = df.loc[common]
            bench = benchmark.loc[common]
            df["RS"] = relative_strength(df, bench, 63)
            df["VARS_raw"] = vars_rs(df, bench, 63)

    return df
