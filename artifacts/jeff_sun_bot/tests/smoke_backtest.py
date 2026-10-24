"""Offline smoke test: stubs yfinance with random-walk data and runs the backtest end to end."""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pandas as pd

class _Ticker:
    def __init__(self, sym): self.sym = sym; self.info = {"industry": "Software", "marketCap": 5e9}
    def history(self, period="2y", interval="1d", auto_adjust=True):
        rng = np.random.default_rng(abs(hash(self.sym)) % 2**32)
        n = 520; idx = pd.bdate_range(end="2026-10-16", periods=n)
        close = 50 * np.exp(np.cumsum(rng.normal(0.0008, 0.025, n)))
        op = close * (1 + rng.normal(0, 0.005, n))
        hi = np.maximum(op, close) * (1 + abs(rng.normal(0, 0.012, n)))
        lo = np.minimum(op, close) * (1 - abs(rng.normal(0, 0.012, n)))
        vol = rng.integers(8e5, 3e6, n)
        return pd.DataFrame({"Open": op, "High": hi, "Low": lo, "Close": close, "Volume": vol}, index=idx)

yf = types.ModuleType("yfinance"); yf.Ticker = _Ticker; sys.modules["yfinance"] = yf

import strategy
# Random walks are too quiet for the real thresholds; loosen them so entries actually occur
strategy.MIN_ADR_PCT, strategy.MIN_RVOL, strategy.VCP_MIN_SCORE, strategy.MIN_VARS = 1.5, 0.8, 0.25, 0
import backtest
s = backtest.run_backtest(symbols=None, start_date="2025-06-01", end_date="2026-10-01")
assert s is not None
assert s.closed_trades, "loosened thresholds should produce trades"
for t in s.closed_trades:
    assert np.isfinite(t.r_multiple) and t.status.value == "closed"
    assert t.initial_risk_per_share > 0
    assert t.day_count >= 0
assert len(s.positions) <= 5
print("\nSMOKE OK:", len(s.closed_trades), "closed trades")
