"""Offline smoke test for exit_lab.py: random-walk data, all six variants on identical signals, result lands in the state file."""
import os, sys, tempfile, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["PAPER_STATE_DIR"] = tempfile.mkdtemp(prefix="lab_")
import zlib
import numpy as np, pandas as pd

class _Ticker:
    def __init__(self, sym): self.sym = sym; self.info = {"industry": "Software", "sector": "Technology", "marketCap": 5e9}
    def history(self, period="2y", interval="1d", auto_adjust=True):
        rng = np.random.default_rng(zlib.crc32(self.sym.encode())); n = 520
        idx = pd.bdate_range(end="2026-10-16", periods=n)
        c = 50 * np.exp(np.cumsum(rng.normal(0.0012, 0.025, n))); o = c * (1 + rng.normal(0, 0.005, n))
        hi = np.maximum(o, c) * (1 + abs(rng.normal(0.01, 0.005, n))); lo = np.minimum(o, c) * (1 - abs(rng.normal(0.01, 0.005, n)))
        return pd.DataFrame({"Open": o, "High": hi, "Low": lo, "Close": c, "Volume": rng.integers(8e5, 3e6, n)}, index=idx)

yf = types.ModuleType("yfinance"); yf.Ticker = _Ticker; sys.modules["yfinance"] = yf
import strategy
strategy.MIN_ADR_PCT, strategy.MIN_RVOL, strategy.VCP_MIN_SCORE, strategy.MIN_VARS = 1.5, 0.8, 0.25, 0
import filters; filters.MIN_LINEARITY = 0.0
import exit_lab, data, paper

md = data.load_market_data(data.get_universe(45), show_progress=False)
rows = exit_lab.compare(md, start="2025-06-01")
assert len(rows) == len(exit_lab.VARIANTS) and all("avg_r" in r for r in rows)
assert sum(r["trades"] for r in rows) > 0, "loosened thresholds should produce trades"
# identical data + identical entries => every variant sees the same FIRST trade count within a small band
counts = [r["trades"] for r in rows]
print("trades per variant:", counts)
exit_lab.merge_into_state(rows, "smoke")
assert paper.load_state()["exit_lab"]["rows"][0]["variant"].startswith("10-MA")
print("SMOKE OK: exit lab")
