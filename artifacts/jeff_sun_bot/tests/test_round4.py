"""
Round-4 unit tests: earnings filter (7-day window, fail-open, cache, slot handling), market-regime gate.
Run:  python tests/test_round4.py     (no network)
"""
import os, sys, tempfile, types, zlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["PAPER_STATE_DIR"] = tempfile.mkdtemp(prefix="r4_")
if "yfinance" not in sys.modules:
    try:
        import yfinance  # noqa: F401
    except ModuleNotFoundError:
        sys.modules["yfinance"] = types.ModuleType("yfinance")

import json, time
from datetime import date, timedelta
import numpy as np
import pandas as pd

import config, earnings, regime, scan_core
import strategy as S

S.COMMISSION_PCT = 0.0
S.SLIPPAGE_PCT = 0.0
TODAY = date(2026, 10, 23)


# ------------------------------------------------------------------ earnings
def _fresh():
    d = tempfile.mkdtemp(); os.environ["PAPER_STATE_DIR"] = d
    earnings.set_as_of(TODAY)


def test_window_is_zero_to_seven_days_inclusive():
    _fresh()
    for offset, blocked in [(0, True), (3, True), (7, True), (8, False), (30, False)]:
        os.environ["PAPER_STATE_DIR"] = tempfile.mkdtemp()               # new cache each time
        got = earnings.earnings_blocker("AAA", {}, fetcher=lambda s, t, o=offset: TODAY + timedelta(days=o))
        assert bool(got) == blocked, (offset, got)
    assert earnings.earnings_blocker("AAA", {}, fetcher=lambda s, t: None) == ""         # nothing scheduled
    assert earnings.REASON == "earnings within 7 days"


def test_past_reports_do_not_block_and_etfs_are_skipped():
    _fresh()
    # Yahoo returns only a report that already happened: PEAD / post-earnings-gap names stay eligible
    assert earnings.next_earnings("PAST", TODAY, fetcher=lambda s, t: None) is None
    assert earnings.earnings_blocker("SOXL", {}, fetcher=lambda s, t: TODAY) == "", "ETFs have no earnings"
    assert earnings.earnings_blocker("ANYTHING", {"LEV": "Y"}, fetcher=lambda s, t: TODAY) == ""


def test_lookup_failure_fails_open_is_counted_and_retried_later():
    _fresh()
    def boom(s, t): raise OSError("yahoo down")
    assert earnings.earnings_blocker("BAD", {}, fetcher=boom) == "", "an unknown date must never block"
    assert "BAD" in earnings.UNKNOWN
    calls = []
    earnings.next_earnings("BAD", TODAY, fetcher=lambda s, t: calls.append(1) or TODAY)
    assert not calls, "a failed lookup is not retried within the same day"
    cache = json.load(open(earnings._path())); cache["BAD"]["ts"] -= 2 * 86400
    json.dump(cache, open(earnings._path(), "w"))
    assert earnings.next_earnings("BAD", TODAY, fetcher=lambda s, t: calls.append(1) or TODAY + timedelta(days=2)) == TODAY + timedelta(days=2)
    assert calls, "after the retry window the lookup runs again"


def test_cache_prevents_repeat_network_calls_and_expires_past_dates():
    _fresh()
    n = []
    f = lambda s, t: n.append(1) or TODAY + timedelta(days=20)
    earnings.next_earnings("CCC", TODAY, fetcher=f); earnings.next_earnings("CCC", TODAY, fetcher=f)
    assert len(n) == 1
    earnings.next_earnings("CCC", TODAY + timedelta(days=21), fetcher=f)       # cached date is now in the past: ask again
    assert len(n) == 2


def _rows(k, blocked=()):
    return pd.DataFrame([{
        "symbol": f"S{i:02d}", "score": 0.9 - i * 0.01, "close": 50.0, "ADR%": 6.0, "RVOL": 1.2, "VCP": 0.95 - i * 0.01,
        "ATR_ext": 1.0, "MR": 0.0, "VARS": 90.0, "daily_ok": True, "trend_ok": True, "LEV": "", "mcap_B": 5.0,
        "sector": "Tech", "industry": "Software", "ATR": 1.0, "AvgVol50": 1e6, "flag": "", "LIN": 0.9, "chop": "",
    } for i in range(k)])


def test_blocked_names_never_take_one_of_the_twelve_slots_and_lookups_stop_early():
    calls = []
    def blocker(sym, row):
        calls.append(sym)
        return "earnings within 7 days" if sym in ("S00", "S01", "S02") else ""
    scan_core.FOCUS_BLOCKERS[:] = [blocker]
    try:
        _, focus, _ = scan_core.select_lists(_rows(40))
    finally:
        scan_core.FOCUS_BLOCKERS.clear()
    syms = focus["symbol"].tolist()
    assert len(syms) == 12 and not {"S00", "S01", "S02"} & set(syms), syms
    assert len(calls) == 15, f"best-first with an early stop: expected 15 lookups, made {len(calls)}"
    assert scan_core.focus_failure({"symbol": "S00", "trend_ok": True, "VCP": 0.9, "ATR_ext": 1, "ADR%": 6, "VARS": 90, "chop": ""}) == ""


# ------------------------------------------------------------------ regime
def test_classify_thresholds_in_priority_order():
    ix = lambda **k: dict(dict(above20=True, above50=True, ext=2.0, streak=1), **k)
    assert regime.classify(70, 70, 100, ix())["label"] == "risk-on"
    assert regime.classify(40, 50, 100, ix())["label"] == "neutral"
    assert regime.classify(70, 70, 100, ix(ext=6.5))["label"] == "stretched"
    assert regime.classify(70, 70, 100, ix(streak=5))["label"] == "stretched"
    assert regime.classify(25, 40, 100, ix())["label"] == "risk-off"
    assert regime.classify(80, 80, 100, ix(above20=False, above50=False))["label"] == "risk-off"
    assert regime.classify(25, 40, 100, ix(ext=7))["label"] == "risk-off", "risk-off outranks stretched"
    assert regime.classify(70, 70, 100, None)["label"] == "risk-on", "no index data: breadth alone decides"
    r = regime.classify(10, 10, 5, ix())
    assert r["label"] == "unknown" and r["mult"] == 1.0, "too few names: no opinion"
    assert [regime.classify(*a)["mult"] for a in [(70, 70, 100, ix()), (40, 50, 100, ix()), (25, 40, 100, ix())]] == [1.0, 0.5, 0.0]


def _frames(n_up, n_down, days=80):
    idx = pd.bdate_range("2026-06-01", periods=days)
    out = {}
    for k in range(n_up + n_down):
        slope = 0.004 if k < n_up else -0.004
        c = pd.Series(100 * np.exp(np.cumsum(np.full(days, slope))), index=idx)
        out[f"X{k}"] = pd.DataFrame({"Close": c, "SMA20": c.rolling(20).mean(), "SMA50": c.rolling(50).mean()})
    return out


def test_breadth_series_counts_the_share_of_names_above_their_averages():
    b = regime.breadth_series(_frames(30, 10))
    last = b.iloc[-1]
    assert last["n"] == 40 and abs(last["pct20"] - 75.0) < 1e-9 and abs(last["pct50"] - 75.0) < 1e-9
    assert last["net_hl"] > 0
    assert pd.isna(b["pct20"].iloc[10]) or b["n"].iloc[10] == 0, "no averages yet early in the history"
    snap = regime.snapshot(_frames(30, 10), None)
    assert snap["label"] == "risk-on" and snap["n"] == 40 and snap["spy"] is None
    assert regime.snapshot(_frames(5, 5), None)["label"] == "unknown"
    assert regime.snapshot(_frames(8, 32), None)["label"] == "risk-off"


def test_series_labels_follow_the_tape_without_lookahead():
    md = _frames(30, 10)
    lab = regime.series_labels(md, None)
    assert lab["label"].iloc[-1] == "risk-on" and set(lab["mult"]) <= {0.0, 0.5, 1.0}
    md2 = {k: v.copy() for k, v in md.items()}
    for v in md2.values():
        v.iloc[-1, v.columns.get_loc("Close")] *= 0.2               # change only the LAST day
    lab2 = regime.series_labels(md2, None)
    assert (lab2["label"].iloc[:-1].values == lab["label"].iloc[:-1].values).all(), "earlier labels must not change"


def test_strategy_gate_blocks_or_halves_new_entries():
    base = S.JeffSunStrategy(equity=100_000)
    t1 = base.enter_intraday("AAA", 100.0, 98.0, pd.Timestamp("2026-01-02"), "x", sector="Energy", industry="Oil")
    half = S.JeffSunStrategy(equity=100_000); half.regime_mult, half.regime_label, half.regime_why = 0.5, "neutral", "breadth 42%"
    t2 = half.enter_intraday("AAA", 100.0, 98.0, pd.Timestamp("2026-01-02"), "x", sector="Energy", industry="Oil")
    assert abs(t2.risk_amount - t1.risk_amount / 2) < 5 and any("regime neutral" in n for n in t2.notes)
    off = S.JeffSunStrategy(equity=100_000); off.regime_mult, off.regime_label, off.regime_why = 0.0, "risk-off", "breadth 22%"
    assert off.enter_intraday("AAA", 100.0, 98.0, pd.Timestamp("2026-01-02"), "x") is None
    assert off.last_block.startswith("market regime risk-off") and "breadth 22%" in off.last_block
    config_gate = S.USE_REGIME_GATE
    try:
        S.USE_REGIME_GATE = False
        assert off.enter_intraday("AAA", 100.0, 98.0, pd.Timestamp("2026-01-02"), "x"), "switching the gate off must work"
    finally:
        S.USE_REGIME_GATE = config_gate


def test_backtest_obeys_the_regime_labels():
    class _T:
        def __init__(s, sym): s.sym = sym; s.info = {"industry": "Software", "sector": "Technology", "marketCap": 5e9}
        def history(s, period="2y", interval="1d", auto_adjust=True):
            rng = np.random.default_rng(zlib.crc32(s.sym.encode())); n = 520
            idx = pd.bdate_range(end="2026-10-16", periods=n)
            c = 50 * np.exp(np.cumsum(rng.normal(0.0012, 0.025, n))); o = c * (1 + rng.normal(0, 0.005, n))
            hi = np.maximum(o, c) * (1 + abs(rng.normal(0.01, 0.005, n))); lo = np.minimum(o, c) * (1 - abs(rng.normal(0.01, 0.005, n)))
            return pd.DataFrame({"Open": o, "High": hi, "Low": lo, "Close": c, "Volume": rng.integers(8e5, 3e6, n)}, index=idx)
    sys.modules["yfinance"].Ticker = _T
    import data, backtest, filters
    S.MIN_ADR_PCT, S.MIN_RVOL, S.VCP_MIN_SCORE, S.MIN_VARS = 1.5, 0.8, 0.25, 0
    filters_min = filters.MIN_LINEARITY; filters.MIN_LINEARITY = 0.0
    try:
        md = data.load_market_data(data.get_universe(45), show_progress=False)
        dates = sorted(set().union(*[set(d.index) for d in md.values()]))
        off = pd.DataFrame({"label": "risk-off", "mult": 0.0, "why": "test"}, index=dates)
        on = pd.DataFrame({"label": "risk-on", "mult": 1.0, "why": "test"}, index=dates)
        none = backtest.run_backtest(symbols=list(md), start_date="2025-06-01", market_data=md, quiet=True, regime_labels=off)
        some = backtest.run_backtest(symbols=list(md), start_date="2025-06-01", market_data=md, quiet=True, regime_labels=on)
    finally:
        filters.MIN_LINEARITY = filters_min
    assert len(none.closed_trades) == 0 and len(none.positions) == 0, "risk-off must stop every new backtest entry"
    assert len(some.closed_trades) > 0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        S.COMMISSION_PCT = 0.0; S.SLIPPAGE_PCT = 0.0
        try:
            fn(); print(f"PASS  {fn.__name__}")
        except Exception as e:
            import traceback; traceback.print_exc()
            failed += 1; print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
