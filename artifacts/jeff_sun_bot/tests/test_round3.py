"""
Tests for the round-3 additions: portfolio caps and drawdown brakes, fill model, trade excursions, exit variants,
signal log, rules-based universe, data checks, alerts, time-of-day volume, evidence statistics.
Run:  python tests/test_round3.py     (no network)
"""
import os, sys, tempfile, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["PAPER_STATE_DIR"] = tempfile.mkdtemp(prefix="r3_")
if "yfinance" not in sys.modules:
    try:
        import yfinance  # noqa: F401
    except ModuleNotFoundError:
        sys.modules["yfinance"] = types.ModuleType("yfinance")

from datetime import date
import numpy as np
import pandas as pd

import config, risk, signals, universe, datacheck, notify, orh, paper, scan_core
import strategy as S

S.COMMISSION_PCT = 0.0
S.SLIPPAGE_PCT = 0.0


# ------------------------------------------------------------------ helpers
def trade(sym="AAA", entry=100.0, stop=94.0, size=300, sector="Technology", industry="Semiconductors",
          lev=1.0, last=None):
    t = S.Trade(symbol=sym, entry_date=pd.Timestamp("2026-01-02"), entry_price=entry, stop_price=stop, size=size,
                risk_amount=(entry - stop) * size, equity_at_entry=100_000, entry_idx=0,
                sector=sector, industry=industry, leverage=lev, last_price=last or entry)
    t.tranches = [S.Tranche(size, stop)]
    return t


def df_days(rows, start="2026-01-01"):
    idx = pd.bdate_range(start, periods=len(rows))
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "SMA10"], index=idx)
    df["EMA21"] = df["SMA10"]; df["ATR"] = 2.0; df["ATR_ext_50"] = 1.0
    return df


# ------------------------------------------------------------------ risk
def test_drawdown_tiers_and_daily_limit():
    assert risk.drawdown_state(100_000, 100_000)[0] == 1.0
    assert risk.drawdown_state(96_500, 100_000)[0] == 0.5          # -3.5%
    assert risk.drawdown_state(93_000, 100_000)[0] == 0.25         # -7%
    assert risk.drawdown_state(89_000, 100_000)[0] == 0.0          # -11%: halted
    assert risk.daily_loss_halt(99_400, 100_000)[0] is True         # -0.6% > 0.5%
    assert risk.daily_loss_halt(99_700, 100_000)[0] is False


def test_caps_block_clusters_total_risk_and_leverage():
    pos = [trade("A", industry="Semiconductors"), trade("B", industry="Semiconductors")]
    ok, why = risk.check_caps(pos, 100_000, "C", "Technology", "Semiconductors", 1.0, 10_000, 100)
    assert not ok and "industry cap" in why
    pos3 = [trade("A", industry="Software"), trade("B", industry="Hardware"), trade("C", industry="Internet")]
    ok, why = risk.check_caps(pos3, 100_000, "D", "Technology", "Other", 1.0, 10_000, 100)
    assert not ok and "sector cap" in why
    heavy = [trade("A", entry=100, stop=94, size=300, last=100)]            # open risk = 300 * 6 = 1800
    ok, why = risk.check_caps(heavy, 100_000, "E", "Energy", "Oil", 1.0, 5_000, 200)
    assert not ok and "open-risk" in why                                    # 1800 + 200 > 1% of 100k
    lev = [trade("SOXL", entry=50, stop=48, size=400, lev=3.0, last=50)]    # 400*50*3 = 60k exposure
    ok, why = risk.check_caps(lev, 100_000, "TQQQ", "Index", "Nasdaq-100", 3.0, 5_000, 50)
    assert not ok and "leveraged" in why
    ok, _ = risk.check_caps([], 100_000, "ZZZ", "Energy", "Oil", 1.0, 5_000, 100)
    assert ok


def test_etf_counts_with_what_it_tracks():
    assert risk.cluster_of("SOXL", "", "")[1] == "Semiconductors"
    assert risk.leverage_of("SOXL") == 3 and risk.leverage_of("NVDL") == 2 and risk.leverage_of("AAPL") == 1


def test_brake_halts_entries_and_leverage_halves_risk():
    st = S.JeffSunStrategy(equity=100_000)
    t1 = st.enter_intraday("AAA", 100.0, 98.0, pd.Timestamp("2026-01-02"), "2026-01-02T10:00", sector="Energy", industry="Oil")
    t2 = st.enter_intraday("SOXL", 100.0, 98.0, pd.Timestamp("2026-01-02"), "2026-01-02T10:00", is_leveraged=True)
    assert t1 and t2 and abs(t2.risk_amount - t1.risk_amount / 2) < 5, (t1.risk_amount, t2.risk_amount)
    assert t2.leverage == 3.0 and any("risk x0.5" in n for n in t2.notes)
    st2 = S.JeffSunStrategy(equity=88_000); st2.peak_equity = 100_000
    assert st2.enter_intraday("AAA", 100.0, 98.0, pd.Timestamp("2026-01-02"), "x") is None
    assert "halted" in st2.last_block
    st3 = S.JeffSunStrategy(equity=99_000); st3.day_start_equity = 100_000
    assert st3.enter_intraday("AAA", 100.0, 98.0, pd.Timestamp("2026-01-02"), "x") is None
    assert "daily loss" in st3.last_block


# ------------------------------------------------------------------ fills, excursions, exits
def test_slippage_model_scales_with_volatility_and_participation():
    cheap = S.JeffSunStrategy._slip(100, 100.0, 1.0, 5_000_000)         # calm, tiny
    wild = S.JeffSunStrategy._slip(100, 100.0, 8.0, 5_000_000)          # ATR 8% of price
    crowded = S.JeffSunStrategy._slip(50_000, 100.0, 1.0, 500_000)      # 10% of daily volume
    assert cheap < wild < config.SLIP_CAP and crowded > cheap * 5
    assert crowded <= config.SLIP_CAP


def test_mfe_mae_tracked_in_r_of_original_risk():
    S.SLIPPAGE_PCT = 0.0
    st = S.JeffSunStrategy(); t = S.Trade(symbol="T", entry_date=pd.Timestamp("2026-01-01"), entry_price=100, stop_price=94,
                                         size=300, risk_amount=1800, equity_at_entry=100_000, entry_idx=0)
    t.tranches = st._build_tranches(t); st.positions["T"] = t
    df = df_days([(100, 100, 100, 100, 99), (101, 109, 99, 106, 99)])        # high 109 = +1.5R, low 99 = -0.17R
    st.manage_open_trades("T", df, 1)
    assert abs(t.mfe_r - 1.5) < 1e-6 and abs(t.mae_r + 1 / 6) < 1e-6, (t.mfe_r, t.mae_r)


def test_mae_is_capped_at_the_exit_when_stopped_out():
    st = S.JeffSunStrategy(); t = S.Trade(symbol="T", entry_date=pd.Timestamp("2026-01-01"), entry_price=100, stop_price=94,
                                         size=300, risk_amount=1800, equity_at_entry=100_000, entry_idx=0)
    t.tranches = st._build_tranches(t); st.positions["T"] = t
    df = df_days([(100, 100, 100, 100, 99), (99, 99.5, 80, 85, 99)])           # opens above the stops, then collapses to 80
    st.manage_open_trades("T", df, 1)
    assert t.status == S.TradeStatus.CLOSED
    assert abs(t.mae_r + 1.0) < 1e-6, f"worst point should be the full stop (-1R), not the day's low: {t.mae_r}"
    gap = S.JeffSunStrategy(); g = S.Trade(symbol="G", entry_date=pd.Timestamp("2026-01-01"), entry_price=100, stop_price=94,
                                          size=300, risk_amount=1800, equity_at_entry=100_000, entry_idx=0)
    g.tranches = gap._build_tranches(g); gap.positions["G"] = g
    gap.manage_open_trades("G", df_days([(100, 100, 100, 100, 99), (90, 91, 80, 85, 99)]), 1)
    assert abs(g.mae_r + 10 / 6) < 1e-6, f"a gap to 90 is a real -1.67R: {g.mae_r}"


def _run_exit(variant, be=True):
    S.SLIPPAGE_PCT = 0.0
    st = S.JeffSunStrategy(exit_variant=variant, day3_be=be)
    t = S.Trade(symbol="T", entry_date=pd.Timestamp("2026-01-01"), entry_price=100, stop_price=94,
                size=300, risk_amount=1800, equity_at_entry=100_000, entry_idx=0)
    t.tranches = st._build_tranches(t); st.positions["T"] = t
    # drifts up through day 4 (high water 106), then closes at 101 on day 5
    rows = [(100, 100, 100, 100, 99), (101, 103, 100.5, 102, 99), (102, 104, 101.5, 103, 99), (103, 105, 102.5, 104, 99),
            (104, 106, 103.5, 106, 99), (103, 103.2, 100.6, 101, 99)]
    df = df_days(rows)
    df["SMA10"] = [99, 99, 99, 99, 99, 102.0]          # 10-MA exit fires on the last bar (close 101 < 102)
    df["EMA21"] = [99, 99, 99, 99, 99, 99.0]           # 21-EMA never fires
    df["ATR"] = [2.0, 2.0, 2.0, 2.0, 2.0, 1.5]         # high_water 106 - 3*1.5 = 101.5 > close 101 -> ATR trail fires
    for i in range(1, len(df)):
        st.manage_open_trades("T", df, i)
    return st, t


def test_exit_variants_differ():
    _, t10 = _run_exit("ma10")
    assert t10.status == S.TradeStatus.CLOSED and "10-MA trail exit" in " ".join(t10.notes)
    _, tema = _run_exit("ema21")
    assert tema.status == S.TradeStatus.OPEN, "21-EMA exit must not fire when price holds above the EMA"
    _, tatr = _run_exit("atr_trail")
    assert tatr.status == S.TradeStatus.CLOSED and "ATR trail exit" in " ".join(tatr.notes)


def test_day3_breakeven_can_be_switched_off():
    _, with_be = _run_exit("ema21", be=True)
    _, no_be = _run_exit("ema21", be=False)
    assert all(tr.stop >= 100 for tr in with_be.tranches)
    assert any(tr.stop < 100 for tr in no_be.tranches)


# ------------------------------------------------------------------ signals
def test_signal_log_forward_returns_and_audit():
    st = {}
    for k in range(40):                                       # 40 'extended' rejects that all went up 3% in 5 days
        sym = f"S{k}"
        signals.record(st, "2026-10-12", sym, "reject", "extended beyond 4x ATR%", 100.0, 2.0)
    idx = pd.bdate_range("2026-10-12", periods=14)
    px = np.concatenate([[100.0], np.linspace(100.5, 103.0, 4), np.full(9, 103.0)])
    df = pd.DataFrame({"Close": px, "High": px * 1.01, "Low": px * 0.99}, index=idx)
    signals.update_forward(st, {f"S{k}": df for k in range(40)})
    row = [r for r in signals.audit(st) if r["reason"].startswith("extended")][0]
    assert row["resolved"] == 40 and row["avg_fwd_pct"] > 2.5 and row["win_pct"] == 100
    assert row["verdict"] == "possibly costing winners"
    few = {}
    signals.record(few, "2026-10-12", "X", "reject", "VCP too loose", 100.0, 2.0)
    signals.update_forward(few, {"X": df})
    assert signals.audit(few)[0]["verdict"] == "too few to judge"
    signals.record(st, "2026-10-12", "S0", "reject", "changed my mind", 100.0, 2.0)   # upsert, no duplicate
    assert sum(1 for s in st["signals"] if s["symbol"] == "S0") == 1


def test_orh_reason_categories_are_stable():
    assert signals.categorize_orh("Price still below ORH") == "price below ORH"
    assert signals.categorize_orh("Volume light for this time of day: 0.6x typical") == "volume too light"
    assert signals.categorize_orh("LoD/ATR 0.9 exceeds 0.6") == "low of day too far (risk)"


def test_scan_hook_blocks_focus_names():
    rows = [{"symbol": "AAA", "trend_ok": True, "VCP": 0.9, "ATR_ext": 1.0, "ADR%": 5.0, "VARS": 90.0, "chop": "", "score": 0.5, "close": 10}]
    assert scan_core.focus_failure(rows[0]) == ""
    scan_core.FOCUS_BLOCKERS.append(lambda sym, row: "earnings within 1 day" if sym == "AAA" else "")
    try:
        assert scan_core.focus_failure(rows[0]) == "earnings within 1 day"
    finally:
        scan_core.FOCUS_BLOCKERS.clear()


# ------------------------------------------------------------------ universe
def _frames():
    idx = pd.bdate_range("2026-08-01", periods=60)
    def mk(c, adr, vol):
        c = np.full(60, float(c)); return pd.DataFrame({"Close": c, "High": c * (1 + adr / 200), "Low": c * (1 - adr / 200), "Volume": np.full(60, float(vol))}, index=idx)
    return {"GOOD": mk(50, 5, 2e6), "SLOW": mk(50, 2, 9e6), "PENNY": mk(3, 7, 9e7), "THIN": mk(50, 6, 1e4), "SOXL": mk(40, 6, 1e6)}


def test_universe_rules_and_snapshots_are_point_in_time():
    frames = _frames()
    dl = lambda syms: pd.concat({s: frames[s] for s in syms if s in frames}, axis=1)
    snap = universe.build_snapshot(date(2026, 10, 23), {"SOXL"}, seed_fn=lambda: list(frames), downloader=dl, pause=0)
    assert snap["symbols"] == ["GOOD", "SOXL"], snap["symbols"]          # slow ADR, penny and thin names are out
    universe.save_snapshot(snap)
    old = dict(snap, date="2026-09-01", symbols=["GOOD"]); universe.save_snapshot(old)
    assert universe.snapshot_as_of(date(2026, 9, 15))["symbols"] == ["GOOD"]
    assert universe.snapshot_as_of(date(2026, 10, 30))["date"] == "2026-10-23"
    assert universe.snapshot_as_of(date(2026, 8, 1)) is None


def test_universe_never_raises_and_falls_back():
    os.environ["PAPER_UNIVERSE_MODE"] = "rules"
    d = tempfile.mkdtemp(); os.environ["PAPER_STATE_DIR"] = d
    boom = lambda: (_ for _ in ()).throw(OSError("offline"))
    syms, info = universe.ensure_universe(date(2026, 10, 23), handpicked=lambda n: ["H1", "H2"], leveraged=set(), seed_fn=boom, pause=0)
    assert syms == ["H1", "H2"] and info["source"] == "handpicked" and "no snapshot" in info["note"]
    universe.save_snapshot({"date": "2026-10-01", "symbols": ["K%d" % i for i in range(40)], "n_pass": 40})
    syms, info = universe.ensure_universe(date(2026, 10, 23), handpicked=lambda n: ["H1"], leveraged=set(), seed_fn=boom, pause=0)
    assert info["source"] == "rules" and info["date"] == "2026-10-01" and "refresh failed" in info["note"]
    os.environ["PAPER_UNIVERSE_MODE"] = "handpicked"


# ------------------------------------------------------------------ data checks
def test_sanity_checks_find_the_usual_yahoo_problems():
    idx = pd.bdate_range("2026-07-01", periods=70)
    base = pd.DataFrame({"Open": 10.0, "High": 10.5, "Low": 9.5, "Close": 10.0, "Volume": 1e6}, index=idx)
    stale = base.iloc[:-3].copy()
    split = base.copy(); split.loc[idx[40]:, ["Open", "High", "Low", "Close"]] = 5.0          # unadjusted 2:1 split
    gappy = base.drop(base.index[30:38])
    nov = base.copy(); nov.loc[idx[-10:], "Volume"] = 0
    found = datacheck.sanity_checks({"OK": base, "STALE": stale, "SPLIT": split, "GAP": gappy, "NOVOL": nov}, idx[-1], set())
    assert "STALE" in " ".join(found["stale last bar"])
    assert "SPLIT" in " ".join(found["extreme one-day move (split / bad tick?)"])
    assert "GAP" in " ".join(found["missing days"])
    assert "NOVOL" in " ".join(found["zero-volume days"])
    assert not any("OK" == x.split()[0] for v in found.values() for x in v)
    assert "SPLIT" not in " ".join(datacheck.sanity_checks({"SPLIT": split}, idx[-1], {"SPLIT"}).get("extreme one-day move (split / bad tick?)", []))


def test_crosscheck_flags_mismatch_and_survives_missing_source():
    idx = pd.bdate_range("2026-10-01", periods=10)
    mine = pd.DataFrame({"Close": 100.0}, index=idx)
    other = {"AAA": pd.DataFrame({"Close": 100.4}, index=idx), "BBB": pd.DataFrame({"Close": 90.0}, index=idx)}
    def fetch(sym):
        if sym == "CCC":
            raise OSError("blocked")
        return other.get(sym, pd.DataFrame())
    res = datacheck.crosscheck({"AAA": mine, "BBB": mine, "CCC": mine, "DDD": mine}, ["AAA", "BBB", "CCC", "DDD"], fetcher=fetch)
    assert res["checked"] == 2 and res["ok"] == 1 and res["mismatched"][0]["symbol"] == "BBB"
    assert set(res["unavailable"]) == {"CCC", "DDD"}


# ------------------------------------------------------------------ alerts
def test_notifier_batches_filters_and_never_raises():
    sent = []
    notify._post_json = lambda url, payload, timeout=10: sent.append((url, payload))
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SMTP_HOST", "ALERT_EMAIL_TO", "NOTIFY_ONLY"):
        os.environ.pop(k, None)
    n = notify.Notifier(); n.add("ENTRY", "x")
    assert n.flush("t") == {"sent": [], "errors": []} or sent == []        # nothing configured: silent
    os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"] = "TOKEN", "42"
    n = notify.Notifier(); n.add("ENTRY", "bought AAA"); n.add("STOP", "stop BBB"); n.add("DIGEST", "close")
    res = n.flush("paper")
    assert res["sent"] == ["telegram"] and len(sent) == 1, "one batched message, not three"
    assert "bought AAA" in sent[0][1]["text"] and "stop BBB" in sent[0][1]["text"] and sent[0][1]["chat_id"] == "42"
    os.environ["NOTIFY_ONLY"] = "ERROR"
    n = notify.Notifier(); n.add("ENTRY", "ignored"); n.add("ERROR", "kept")
    assert [k for k, _ in n.items] == ["ERROR"]
    def bad(*a, **k): raise OSError("down")
    notify._post_json = bad
    res = notify.send("s", "b")
    assert res["errors"] and not res["sent"], "a failing channel must not raise"
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "NOTIFY_ONLY"):
        os.environ.pop(k, None)


# ------------------------------------------------------------------ time-of-day volume
def _intraday(today_vols, prior_sessions=8, today="2026-10-23"):
    frames, k = [], np.arange(78)
    shape = 1 + 3 * np.exp(-k / 10)                                  # front-loaded typical session
    for d in pd.bdate_range(end=pd.Timestamp(today) - pd.Timedelta(days=1), periods=prior_sessions):
        idx = pd.date_range(f"{d:%Y-%m-%d} 09:30", periods=78, freq="5min")
        frames.append(pd.DataFrame({"Open": 10, "High": 10.1, "Low": 9.9, "Close": 10, "Volume": shape * 1000}, index=idx))
    n = len(today_vols); idx = pd.date_range(f"{today} 09:30", periods=n, freq="5min")
    rows = []
    for j in range(n):
        if j < 6: rows.append((10, 10.04, 9.98, 10))
        elif j == 6: rows.append((10, 10.02, 9.99, 10.01))
        else: rows.append((10.04, 10.09, 10.0, 10.08))
    frames.append(pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close"]).assign(Volume=today_vols))
    return pd.concat(frames), float((shape * 1000).sum())


def test_time_adjusted_volume_is_stricter_early_and_fair_late():
    typical_first14 = (1 + 3 * np.exp(-np.arange(14) / 10)) * 1000
    heavy, avg = _intraday(typical_first14 * 3)
    light, _ = _intraday(typical_first14 * 0.3)
    orh.fetch_intraday = lambda *a, **k: heavy.copy()
    ok, res, why = orh.evaluate_orh_entry("X", daily_atr=1.0, avg_daily_volume=avg)
    assert res.time_rvol is not None and 2.5 < res.time_rvol < 3.5 and ok, (res.time_rvol, why)
    orh.fetch_intraday = lambda *a, **k: light.copy()
    ok, res, why = orh.evaluate_orh_entry("X", daily_atr=1.0, avg_daily_volume=avg)
    assert not ok and "light for this time of day" in why and res.time_rvol < 0.5
    # too little history: falls back to the flat share-of-day rule instead of failing closed
    short, avg2 = _intraday(typical_first14 * 3, prior_sessions=3)
    orh.fetch_intraday = lambda *a, **k: short.copy()
    ok, res, why = orh.evaluate_orh_entry("X", daily_atr=1.0, avg_daily_volume=avg2)
    assert res.time_rvol is None and res.dayvol_pct is not None


# ------------------------------------------------------------------ evidence
def test_evidence_bootstrap_is_honest_about_small_samples():
    assert paper.evidence([1.0, -0.5])["ci_low"] is None
    ev = paper.evidence([1.2, -0.5, -0.7, 2.5, -0.6, 0.3, -0.4, 1.1, -0.7, 0.9])
    assert ev["ci_low"] < ev["mean_r"] < ev["ci_high"] and ev["n"] == 10 and ev["progress_pct"] == 10
    big = paper.evidence([1.0] * 60 + [-0.5] * 40)
    assert big["p_positive"] > 0.99 and (big["ci_high"] - big["ci_low"]) < (ev["ci_high"] - ev["ci_low"])
    assert paper.evidence([1.2, -0.5, -0.7, 2.5, -0.6, 0.3, -0.4, 1.1, -0.7, 0.9]) == ev, "fixed seed: stable between runs"


def test_linearity_filter_in_candidates():
    from filters import entry_filter_failures
    n = 80
    smooth = pd.DataFrame({"Close": 100 * np.exp(np.linspace(0, 0.4, n))}); smooth["ATR"] = 2.0
    rng = np.random.default_rng(3)
    chop = pd.DataFrame({"Close": 100 * np.exp(np.cumsum(rng.normal(0, 0.03, n)))}); chop["ATR"] = 2.0
    assert entry_filter_failures(smooth, n - 1) == []
    assert any("choppy" in x for x in entry_filter_failures(chop, n - 1))


def _ret(seed=0, n=60, mix=None, noise=0.0):
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 0.02, n) if mix is None else mix
    return base + (rng.normal(0, noise, n) if noise else 0)


def _series(vals):
    return pd.Series(vals, index=pd.bdate_range("2026-07-01", periods=len(vals)))


def test_correlation_cap_blocks_clones_and_the_third_correlated_name():
    a = _ret(1)
    clone = _series(a + np.random.default_rng(9).normal(0, 0.001, 60))            # ~0.99
    ok, why = risk.check_correlation(clone, {"HELD": _series(a)})
    assert not ok and "correlation" in why and "HELD" in why
    # two held names at ~0.75 each: a third highly-correlated name is refused, a single one is not
    c75 = lambda seed: _series(a * 0.75 + np.random.default_rng(seed).normal(0, 0.02 * 0.66, 60))
    cand = _series(a)
    h1, h2 = c75(11), c75(12)
    c1, c2 = risk.pair_correlation(cand, h1), risk.pair_correlation(cand, h2)
    assert 0.70 <= c1 < 0.85 and 0.70 <= c2 < 0.85, (c1, c2)
    assert risk.check_correlation(cand, {"H1": h1})[0] is True
    ok, why = risk.check_correlation(cand, {"H1": h1, "H2": h2})
    assert not ok and "already 2" in why
    assert risk.check_correlation(cand, {"IND": _series(_ret(99))})[0] is True                  # unrelated name passes
    assert risk.check_correlation(_series(a[:20]), {"H1": _series(a[:20])})[0] is True            # too little history: no opinion


def test_strategy_applies_the_correlation_cap_through_the_provider():
    a = _ret(5)
    rets = {"HELD": _series(a), "TWIN": _series(a + np.random.default_rng(2).normal(0, 0.001, 60)), "OTHER": _series(_ret(77))}
    st = S.JeffSunStrategy(equity=100_000); st.returns_provider = rets.get
    assert st.enter_intraday("HELD", 100.0, 98.0, pd.Timestamp("2026-01-02"), "x", sector="Energy", industry="Oil")
    assert st.enter_intraday("TWIN", 100.0, 98.0, pd.Timestamp("2026-01-02"), "x", sector="Utilities", industry="Power") is None
    assert "correlation" in st.last_block
    assert st.enter_intraday("OTHER", 100.0, 98.0, pd.Timestamp("2026-01-02"), "x", sector="Financials", industry="Banks")


def test_returns_survive_the_state_file_round_trip():
    idx = pd.bdate_range("2026-05-01", periods=80)
    rng = np.random.default_rng(4)
    md = {s: pd.DataFrame({"Close": 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 80)))}, index=idx) for s in ("AAA", "BBB")}
    st = paper.new_state()
    paper.store_returns(st, md, ["AAA", "BBB", "MISSING"])
    import json
    st = json.loads(json.dumps(st))
    get = paper.returns_provider(st)
    got = get("AAA")
    assert len(got) == 60 and get("MISSING") is None and get("ZZZ") is None
    assert np.allclose(got.to_numpy(), md["AAA"]["Close"].pct_change().iloc[-60:].to_numpy(), atol=1e-5)


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
