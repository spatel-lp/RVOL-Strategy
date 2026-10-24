"""
Regression tests for the review fixes. Run from the jeff_sun_bot folder:
    python tests/test_fixes.py        (or: pytest tests)
No network needed: yfinance is stubbed and all data is synthetic.
"""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if "yfinance" not in sys.modules:
    try:
        import yfinance  # noqa: F401
    except ModuleNotFoundError:
        sys.modules["yfinance"] = types.ModuleType("yfinance")

import numpy as np
import pandas as pd

import strategy as S
import data as D
import indicators as I
import orh as O

S.COMMISSION_PCT = 0.0
S.SLIPPAGE_PCT = 0.0


def make_df(dates, rows):
    """rows: list of (open, high, low, close, sma10)."""
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "SMA10"], index=pd.DatetimeIndex(dates))
    df["ATR_ext_50"] = 1.0
    return df


def new_trade(strat, entry=100.0, stop=94.0, size=300, idx=0, date="2026-01-01"):
    t = S.Trade(symbol="T", entry_date=pd.Timestamp(date), entry_price=entry, stop_price=stop,
                size=size, risk_amount=(entry - stop) * size, equity_at_entry=100_000, entry_idx=idx)
    t.tranches = strat._build_tranches(t)
    strat.positions["T"] = t
    return t


def test_trail_runs_after_day3_breakeven():
    # Thu entry; the old code returned early once the stop == entry and never trailed.
    dates = pd.bdate_range("2026-01-01", periods=7)
    rows = [(100, 100, 100, 100, 100),
            (100, 102, 100.5, 101, 100), (101, 103, 101.5, 102, 100),
            (102, 104, 102.5, 103, 101), (103, 105, 103.5, 104, 102),
            (104, 104, 101.0, 101.5, 103),   # closes under the 10-MA, low stays above BE stop
            (101, 102, 101, 101.5, 103)]
    df = make_df(dates, rows)
    st = S.JeffSunStrategy(); t = new_trade(st, date=dates[0])
    for i in range(1, len(df)):
        st.manage_open_trades("T", df, i)
    assert t.status == S.TradeStatus.CLOSED, "trade should have exited"
    assert any("10-MA trail exit" in n for n in t.notes), t.notes
    assert t.r_multiple > 0, f"R should be positive, got {t.r_multiple}"


def test_trading_days_not_calendar_days():
    dates = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06"])  # Thu Fri Mon Tue
    df = make_df(dates, [(100, 101, 99.5, 100.5, 100)] * 4)
    st = S.JeffSunStrategy(); t = new_trade(st, date=dates[0])
    st.manage_open_trades("T", df, 2)       # Monday
    assert t.day_count == 2, f"Monday should be T+2, got {t.day_count}"


def test_day3_reduction_fires_once():
    dates = pd.bdate_range("2026-01-01", periods=7)
    df = make_df(dates, [(100, 101, 99.9, 100.5, 99)] * 7)
    st = S.JeffSunStrategy(); t = new_trade(st, date=dates[0])
    for i in range(1, 7):
        st.manage_open_trades("T", df, i)
    assert sum("Day3 reduction" in n for n in t.notes) == 1, t.notes


def test_three_stop_caps_loss_at_two_thirds_r():
    dates = pd.bdate_range("2026-01-01", periods=2)
    df = make_df(dates, [(100, 100, 100, 100, 100), (99, 99, 93, 94, 100)])  # opens above stops, trades through all
    st = S.JeffSunStrategy(); t = new_trade(st, date=dates[0])
    st.manage_open_trades("T", df, 1)
    assert t.status == S.TradeStatus.CLOSED
    assert abs(t.r_multiple + 2 / 3) < 1e-6, t.r_multiple


def test_gap_below_stops_fills_at_open():
    dates = pd.bdate_range("2026-01-01", periods=2)
    df = make_df(dates, [(100, 100, 100, 100, 100), (90, 91, 88, 89, 100)])
    st = S.JeffSunStrategy(); t = new_trade(st, date=dates[0])
    st.manage_open_trades("T", df, 1)
    assert t.r_multiple < -1.5, f"gap should hurt more than -1R, got {t.r_multiple}"


def test_partial_then_breakeven_counts_as_win():
    dates = pd.bdate_range("2026-01-01", periods=6)
    rows = [(100, 100, 100, 100, 100),
            (110, 113, 109, 112, 100),       # +2R -> partial
            (112, 112, 111, 111, 100), (111, 111, 110.5, 110.8, 100),   # day 3 -> BE
            (100.5, 101, 99, 100, 100),      # falls back through breakeven
            (100, 100, 100, 100, 100)]
    df = make_df(dates, rows)
    st = S.JeffSunStrategy(); t = new_trade(st, date=dates[0])
    for i in range(1, len(df)):
        st.manage_open_trades("T", df, i)
    assert t.status == S.TradeStatus.CLOSED
    assert t.r_multiple > 0, f"banked partial must keep the trade positive, got {t.r_multiple}"


def test_r_uses_original_risk_not_moved_stop():
    dates = pd.bdate_range("2026-01-01", periods=2)
    df = make_df(dates, [(100, 100, 100, 100, 100), (103, 104, 102, 103, 100)])
    st = S.JeffSunStrategy(); t = new_trade(st, date=dates[0])
    t.stop_price = t.entry_price                      # moved to breakeven
    assert t.initial_risk_per_share == 6.0


def test_vars_is_cross_sectional_and_has_no_lookahead():
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2026-01-01", periods=6)
    data = {f"S{k}": pd.DataFrame({"VARS_raw": rng.normal(size=6), "Mom_raw": rng.normal(size=6)}, index=idx)
            for k in range(10)}
    D.add_cross_sectional_ranks(data)
    top = max(data, key=lambda s: data[s]["VARS_raw"].iloc[2])
    assert data[top]["VARS"].iloc[2] == 100.0
    before = {s: d["VARS"].iloc[:5].copy() for s, d in data.items()}
    data2 = {s: pd.DataFrame({"VARS_raw": d["VARS_raw"].copy(), "Mom_raw": d["Mom_raw"].copy()}) for s, d in data.items()}
    data2["S0"].iloc[5, 0] = 999.0                    # change the FUTURE
    D.add_cross_sectional_ranks(data2)
    for s in data2:
        assert (data2[s]["VARS"].iloc[:5].values == before[s].values).all(), "future data leaked into past ranks"


def test_universe_is_deterministic_has_leveraged_and_no_biotech():
    a, b = D.get_universe(45), D.get_universe(45)
    assert a == b
    assert any(s in D.LEVERAGED_ETFS for s in a), "45-name universe must include leveraged ETFs"
    assert not ({"MRNA", "VKTX", "RXRX", "NVAX"} & set(D.get_universe(65)))


def test_atr_extension_modes():
    idx = pd.bdate_range("2025-01-01", periods=120)
    close = pd.Series(np.linspace(50, 80, 120), index=idx)
    df = pd.DataFrame({"Open": close, "High": close * 1.02, "Low": close * 0.98, "Close": close, "Volume": 1e6})
    ma = close.rolling(50).mean(); atr = I.atr(df)
    expected = ((close / ma) - 1) / (atr / close)
    got = I.atr_pct_from_ma(df)
    assert np.allclose(got.dropna(), expected.dropna())


def _fake_intraday(closes, highs=None, n_or=6):
    idx = pd.date_range("2026-10-20 09:30", periods=len(closes), freq="5min")
    highs = highs or closes
    return pd.DataFrame({"Open": closes, "High": highs, "Low": [c - 0.1 for c in closes],
                         "Close": closes, "Volume": [1000] * len(closes)}, index=idx)


def test_orh_true_reclaim_vs_breakout_vs_forming():
    O.fetch_intraday = lambda *a, **k: _fake_intraday([9.5] * 5 + [10.0] + [9.8, 10.3, 10.4, 10.5, 10.6, 10.7])
    r = O.compute_orh("X", daily_atr=1.0, avg_daily_volume=20000)
    assert r.m30_re_orh and not r.broke_without_dip, r.notes
    O.fetch_intraday = lambda *a, **k: _fake_intraday([9.5] * 5 + [10.0] + [10.2, 10.3, 10.4, 10.5, 10.6, 10.7])
    r = O.compute_orh("X", daily_atr=1.0, avg_daily_volume=20000)
    assert r.broke_without_dip and not r.m30_re_orh, r.notes
    O.fetch_intraday = lambda *a, **k: _fake_intraday([9.5, 9.6, 9.7, 9.8])
    assert O.compute_orh("X", daily_atr=1.0) is None, "opening range still forming must not fall back to yesterday"


def test_trade_roundtrip_keeps_remaining_size():
    import paper
    st = S.JeffSunStrategy(); t = new_trade(st)
    st._sell(t, 100, 105.0, "test"); t.tranches[0].shares -= 100; t.tranches = [x for x in t.tranches if x.shares]
    back = paper.trade_from_dict(paper.trade_to_dict(t))
    assert back.remaining_size == 200 and back.size == 300
    assert [x.shares for x in back.tranches] == [x.shares for x in t.tranches]
    assert back.initial_risk_per_share == t.initial_risk_per_share


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS  {fn.__name__}")
        except Exception as e:
            failed += 1; print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
