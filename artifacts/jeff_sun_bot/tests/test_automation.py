"""
End-to-end test of the paper-trading automation on a synthetic market (no network).

Drives run_daily.py through: scan -> (idempotent re-scan) -> intraday ORH entry -> (no duplicate entry)
-> entry-day scan -> day 1..4 scans -> a separate stop-out path, then renders the dashboard and
executes its JavaScript in node (if node is installed) to prove it renders without errors.
Run:  python tests/test_automation.py
"""
import json, os, shutil, subprocess, sys, tempfile, types, zlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP = tempfile.mkdtemp(prefix="paper_")
os.environ["PAPER_STATE_DIR"] = os.path.join(TMP, "state")
os.environ["PAPER_DOCS_DIR"] = os.path.join(TMP, "docs")
os.environ["PAPER_UNIVERSE_MODE"] = "handpicked"      # no network in tests

import numpy as np
import pandas as pd

# ------------------------------------------------------------------ synthetic market
STATE = {"end": "2026-10-22"}          # last daily bar the fake Yahoo serves
TAIL = {}                              # {symbol: {date: (o, h, l, c)}} controlled bars after the entry
INTRA = {}                             # {symbol: 5m frame}
START = pd.Timestamp("2024-06-03")
DAYS = pd.bdate_range(START, periods=760)


def _path(sym):
    rng = np.random.default_rng(zlib.crc32(sym.encode()))
    n = len(DAYS)
    c = 50 * np.exp(np.cumsum(rng.normal(0.0016, 0.02, n)))
    o = c * (1 + rng.normal(0, 0.004, n))
    hi = np.maximum(o, c) * (1 + abs(rng.normal(0.012, 0.004, n)))
    lo = np.minimum(o, c) * (1 - abs(rng.normal(0.012, 0.004, n)))
    v = rng.integers(8e5, 3e6, n)
    return pd.DataFrame({"Open": o, "High": hi, "Low": lo, "Close": c, "Volume": v}, index=DAYS)


class _Ticker:
    def __init__(self, sym):
        self.sym = sym
        self.info = {"industry": "Software", "marketCap": 5e9, "sector": "Tech"}

    def history(self, period="2y", interval="1d", auto_adjust=True):
        if interval != "1d":
            return pd.DataFrame()
        df = _path(self.sym)
        df = df[df.index <= pd.Timestamp(STATE["end"])].copy()
        for d, (o, h, l, c) in TAIL.get(self.sym, {}).items():
            ts = pd.Timestamp(d)
            if ts in df.index:
                df.loc[ts, ["Open", "High", "Low", "Close"]] = [o, h, l, c]
        return df.iloc[-520:]


yf = types.ModuleType("yfinance"); yf.Ticker = _Ticker; sys.modules["yfinance"] = yf

import orh, run_daily, scan_core, paper, dashboard                   # noqa: E402
run_daily.fetch_stooq = lambda sym: pd.DataFrame()          # second data source: unavailable in tests
import earnings as _earn
_earn.fetch_next_earnings = lambda sym, today: None                # tests: nobody reports earnings unless a test says so
orh.fetch_intraday = run_daily.fetch_intraday = lambda sym, interval="5m", days=5: INTRA.get(sym, pd.DataFrame()).copy()
# random walks are too quiet for production thresholds; loosen the filters, not the logic
scan_core.MIN_ADR_PCT, scan_core.VCP_MIN_SCORE, scan_core.MIN_VARS = 1.0, 0.0, 0


def five_min(date, base, scenario, n_bars):
    idx = pd.date_range(f"{date} 09:30", periods=n_bars, freq="5min")
    rows = []
    for k in range(n_bars):
        if k < 6:                                           # opening range: ORH = base*1.004
            rows.append((base, base * 1.004, base * 0.998, base, 100_000))
        elif scenario == "reclaim":
            if k == 6:
                rows.append((base, base * 1.002, base * 0.999, base * 1.001, 100_000))   # dips below ORH
            else:
                rows.append((base * 1.004, base * 1.009, base * 1.000, base * 1.008, 100_000))  # re-claims it
        else:                                               # "weak": never gets back above ORH
            rows.append((base, base * 1.001, base * 0.999, base, 100_000))
    return pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close", "Volume"])


def run(cmd, now, *extra):
    return run_daily.main([cmd, "--now", now, "--universe", "60", *extra])


def star_in_returns(st):
    first = st["scan"]["focus"][0]["symbol"]
    return len(st["returns"]["series"].get(first, [])) == 60


def load():
    st = paper.load_state()
    # accounting invariants that must hold after EVERY run (this caught a double-counting bug on reload)
    for p in st["positions"]:
        assert abs(p["remaining_size"] - sum(t["shares"] for t in p["tranches"])) < 1e-9, "remaining != sum of tranche shares"
    value = st["cash"] + sum(p["remaining_size"] * (p["last_price"] or p["entry_price"]) for p in st["positions"])
    assert abs(value - st["equity"]) < 0.01, f"equity {st['equity']} != cash + positions {value}"
    return st


# ------------------------------------------------------------------ the story
def test_all():
    # 1) post-close scan on Thu 22 Oct 2026
    assert run("scan", "2026-10-22T16:30:00-04:00") == 0
    st = load()
    assert st["last_run"]["scan"] == "2026-10-22"
    assert st["scan"]["focus"], "synthetic uptrend should produce at least one Focus name"
    assert len(st["equity_curve"]) == 1 and abs(st["equity"] - 100_000) < 1
    assert os.path.exists(os.path.join(TMP, "docs", "index.html"))
    assert st["universe"]["source"] == "handpicked" and st["datacheck"]["asof"], "universe + data-health must be recorded"
    kinds = {x["kind"] for x in st["signals"]}
    assert "focus" in kinds, kinds
    assert star_in_returns(st), "scan must store returns for the correlation cap"
    focus = [r["symbol"] for r in st["scan"]["focus"]]
    star = focus[0]
    base = next(r["close"] for r in st["scan"]["focus"] if r["symbol"] == star)
    print(f"      focus={focus}  trading {star} @ ~{base}")

    # 2) a second run the same evening does nothing
    n_events = len(st["events"])
    run("scan", "2026-10-22T20:00:00-04:00")
    assert len(load()["events"]) == n_events, "re-running the scan must be a no-op"

    # 3) too early / outside window guards
    run("intraday", "2026-10-23T09:40:00-04:00")
    assert not load()["positions"], "outside the window nothing may trade"
    run("scan", "2026-10-23T12:00:00-04:00")
    assert load()["last_run"]["scan"] == "2026-10-22"

    # 4) Fri 23 Oct 10:35: Focus name re-claims ORH, others do not -> exactly one paper entry
    for r in st["scan"]["focus"]:
        INTRA[r["symbol"]] = five_min("2026-10-23", r["close"], "reclaim" if r["symbol"] == star else "weak", 13)
    run("intraday", "2026-10-23T10:35:00-04:00")
    st = load()
    assert [p["symbol"] for p in st["positions"]] == [star], [p["symbol"] for p in st["positions"]]
    pos = st["positions"][0]
    assert len(pos["tranches"]) == 3 and pos["tranches"][0]["stop"] < pos["entry_price"]
    assert pos["risk_amount"] <= 100_000 * 0.0021, pos["risk_amount"]
    assert st["day"]["new_trades"] == 1 and st["cash"] < 100_000
    verdicts = {c["symbol"]: c for c in st["intraday"]["checked"]}
    assert verdicts[star]["passed"] and all(not v["passed"] for s, v in verdicts.items() if s != star)

    # 5) same session, later: no duplicate entry, position still open
    run("intraday", "2026-10-23T11:05:00-04:00")
    st = load()
    assert len(st["positions"]) == 1 and st["day"]["new_trades"] == 1
    assert any(c["reason"] == "held" for c in st["intraday"]["checked"] if c["symbol"] == star)
    entry_px = st["positions"][0]["entry_price"]
    sig = {(x["symbol"], x["kind"]): x for x in st["signals"] if x["date"] == "2026-10-23"}
    assert (star, "taken") in sig and (star, "orh_skip") not in sig, "the entry replaces the earlier skip for that name"
    assert any(k == "orh_skip" and v["reason"] == "price below ORH" for (_s, k), v in sig.items())
    assert st["positions"][0]["mfe_r"] >= 0 and st["positions"][0]["leverage"] >= 1.0

    # 6) entry-day close: judged with 5m bars, NOT the daily low that contains pre-entry lows
    TAIL[star] = {"2026-10-23": (base, base * 1.02, base * 0.99, base * 1.012)}
    STATE["end"] = "2026-10-23"
    run("scan", "2026-10-23T16:30:00-04:00")
    st = load()
    assert len(st["positions"]) == 1, "daily low below the stop must not stop out an entry-day trade"
    assert st["positions"][0]["day_count"] == 0 and st["last_run"]["scan"] == "2026-10-23"

    # 7) days 1..4 on a controlled rising tail: Day-3 reduction + breakeven stops fire once
    days = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29"]
    px = entry_px
    for k, d in enumerate(days, start=1):
        px *= 1.012
        TAIL[star][d] = (px * 0.998, px * 1.004, px * 0.994, px)
        STATE["end"] = d
        run("scan", f"{d}T16:30:00-04:00")
    st = load()
    pos = st["positions"][0] if st["positions"] else None
    assert pos is not None and pos["day_count"] == 4, pos and pos["day_count"]
    notes = " ".join(pos["notes"])
    assert notes.count("Day3 reduction") == 1 and "breakeven" in notes
    assert all(tr["stop"] >= entry_px - 1e-6 for tr in pos["tranches"]), "stops should sit at breakeven after day 3"
    assert len(st["equity_curve"]) >= 5
    assert 100_000 < st["equity"] < 101_500, f"equity {st['equity']} implausible for a ~$16k position"
    assert pos["remaining_size"] < pos["size"], "partials must have reduced the remaining size"

    # forward tracking resolved something by now (the 22 Oct focus names are 4+ trading days old)
    assert any(x["fwd"]["3"] is not None for x in st["signals"] if x["date"] == "2026-10-22"), "forward returns not filled"
    payload = dashboard.build_payload(st)
    assert payload["audit"] and payload["risk"]["max_industry"] == 2 and payload["summary"]["evidence"]["n"] == 0

    # 8) dashboard is self-contained and well formed
    html = open(os.path.join(TMP, "docs", "index.html"), encoding="utf-8").read()
    assert html.count("<script") == 2 and "http://" not in html.replace("http://www.w3.org", "")
    return star, entry_px


def test_stop_out_path():
    """Fresh account: ORH entry, then a flush through every stop. Loss must be about -0.67R, no re-entry that day."""
    shutil.rmtree(os.environ["PAPER_STATE_DIR"], ignore_errors=True)
    TAIL.clear(); INTRA.clear(); STATE["end"] = "2026-10-22"
    run("scan", "2026-10-22T16:30:00-04:00")
    st = load(); focus = st["scan"]["focus"]; star = focus[0]["symbol"]
    for r in focus:
        INTRA[r["symbol"]] = five_min("2026-10-23", r["close"], "reclaim" if r["symbol"] == star else "weak", 13)
    run("intraday", "2026-10-23T10:35:00-04:00")
    assert len(load()["positions"]) == 1
    base = focus[0]["close"]
    flush = five_min("2026-10-23", base, "reclaim", 13)
    crash = pd.DataFrame([(base * 1.006, base * 1.006, base * 0.97, base * 0.975, 300_000)],
                         index=pd.DatetimeIndex(["2026-10-23 11:00"]), columns=flush.columns)
    INTRA[star] = pd.concat([flush, crash])
    run("intraday", "2026-10-23T11:05:00-04:00")
    st = load()
    assert not st["positions"] and len(st["closed"]) == 1, "all three tranches should have been stopped"
    r = st["closed"][0]["r_multiple"]
    assert -0.95 < r < -0.5, f"3-stop loss should be near -0.67R (plus costs), got {r}"
    run("intraday", "2026-10-23T11:35:00-04:00")
    assert not load()["positions"], "no re-entry on a name stopped out today"
    assert any("stopped out today" in c["reason"] for c in load()["intraday"]["checked"])


def test_brake_blocks_entries_and_alerts_fire():
    """A 12% drawdown must stop new entries, say why on the dashboard, and send ONE Telegram message per job."""
    import notify
    shutil.rmtree(os.environ["PAPER_STATE_DIR"], ignore_errors=True)
    TAIL.clear(); INTRA.clear(); STATE["end"] = "2026-10-22"
    sent = []
    notify._post_json = lambda url, payload, timeout=10: sent.append(payload["text"])
    os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"] = "T", "1"
    try:
        run("scan", "2026-10-22T16:30:00-04:00")
        assert sent and "close: equity" in sent[-1] and len(sent) == 1, sent
        st = load(); focus = st["scan"]["focus"]
        st["equity_curve"] = [{"date": "2026-10-20", "equity": 100_000, "cash": 100_000}, {"date": "2026-10-21", "equity": 88_000, "cash": 88_000}]
        st["equity"] = st["cash"] = 88_000
        paper.save_state(st)
        for r in focus:
            INTRA[r["symbol"]] = five_min("2026-10-23", r["close"], "reclaim", 13)
        sent.clear()
        run("intraday", "2026-10-23T10:35:00-04:00")
        st = load()
        assert not st["positions"], "drawdown halt must block every entry"
        assert any(a.startswith("Risk brake") for a in st["alerts"])
        assert any(x["kind"] == "orh_skip" and x["reason"].startswith("blocked") for x in st["signals"])
        assert len(sent) == 1 and "paused" in sent[0], sent
        run("intraday", "2026-10-23T11:05:00-04:00")
        assert len(sent) == 1, "the pause is announced once per day, not every 30 minutes"
    finally:
        for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            os.environ.pop(k, None)


def test_correlation_blocks_a_clone_through_the_runner():
    """Two Focus names that move as one: the second ORH re-claim must be refused, with the reason logged."""
    shutil.rmtree(os.environ["PAPER_STATE_DIR"], ignore_errors=True)
    TAIL.clear(); INTRA.clear(); STATE["end"] = "2026-10-22"
    run("scan", "2026-10-22T16:30:00-04:00")
    st = load(); focus = st["scan"]["focus"]
    a, b = focus[0]["symbol"], focus[1]["symbol"]
    st["returns"]["series"][b] = list(st["returns"]["series"][a])           # make B an exact return-clone of A
    # (put them in different industries so ONLY the correlation cap can be what stops the second entry)
    st["scan"]["focus"][0]["industry"], st["scan"]["focus"][1]["industry"] = "Alpha Industry", "Beta Industry"
    st["scan"]["focus"][0]["sector"], st["scan"]["focus"][1]["sector"] = "Alpha Sector", "Beta Sector"
    paper.save_state(st)
    for r in focus:
        INTRA[r["symbol"]] = five_min("2026-10-23", r["close"], "reclaim" if r["symbol"] in (a, b) else "weak", 13)
    run("intraday", "2026-10-23T10:35:00-04:00")
    st = load()
    held = [p["symbol"] for p in st["positions"]]
    assert len(held) == 1 and held[0] in (a, b), held
    other = b if held[0] == a else a
    skip = [x for x in st["signals"] if x["symbol"] == other and x["kind"] == "orh_skip"]
    assert skip and skip[0]["reason"].startswith("blocked: correlation"), skip
    assert any("correlation" in e["msg"] for e in st["events"] if e["kind"] == "SKIP")


def test_earnings_filter_removes_a_focus_name_and_logs_why():
    import earnings
    from datetime import date, timedelta
    shutil.rmtree(os.environ["PAPER_STATE_DIR"], ignore_errors=True)
    TAIL.clear(); INTRA.clear(); STATE["end"] = "2026-10-22"
    earnings.fetch_next_earnings = lambda sym, today: None                      # nobody reports: baseline
    run("scan", "2026-10-22T16:30:00-04:00")
    base = [r["symbol"] for r in load()["scan"]["focus"]]
    victim = next(s for s in base if s not in data_mod_leveraged())            # a stock, not an ETF
    shutil.rmtree(os.environ["PAPER_STATE_DIR"], ignore_errors=True)
    soon = date(2026, 10, 22) + timedelta(days=5)
    earnings.fetch_next_earnings = lambda sym, today: soon if sym == victim else date(2026, 12, 15)
    run("scan", "2026-10-22T16:30:00-04:00", "--force")
    st = load()
    focus = [r["symbol"] for r in st["scan"]["focus"]]
    assert victim not in focus and len(focus) >= len(base) - 1, (victim, focus, base)
    rej = [x for x in st["signals"] if x["symbol"] == victim and x["kind"] == "reject"]
    assert rej and rej[0]["reason"] == "earnings within 7 days", rej
    assert all(r.get("earn") == "2026-12-15" for r in st["scan"]["focus"] if r["LEV"] != "Y"), "dashboard needs the date"
    earnings.fetch_next_earnings = lambda sym, today: None


def _alpaca_env(send):
    os.environ.update(PAPER_BROKER="alpaca", ALPACA_API_KEY_ID="KEY", ALPACA_API_SECRET_KEY="SECRET")
    if send:
        os.environ["ALPACA_SEND_ORDERS"] = "1"
    else:
        os.environ.pop("ALPACA_SEND_ORDERS", None)


def _alpaca_off():
    import broker
    broker.TRANSPORT = None
    for k in ("PAPER_BROKER", "ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY", "ALPACA_SEND_ORDERS"):
        os.environ.pop(k, None)


def test_alpaca_mirror_follows_the_simulator_through_the_runner():
    """Entry -> three real stops at the broker; simulator partials -> queued market sells; stops re-sized; reconcile OK."""
    import broker
    from fake_alpaca import FakeAlpaca
    shutil.rmtree(os.environ["PAPER_STATE_DIR"], ignore_errors=True)
    TAIL.clear(); INTRA.clear(); STATE["end"] = "2026-10-22"
    fake = FakeAlpaca(); broker.TRANSPORT = fake; _alpaca_env(send=True)
    try:
        run("scan", "2026-10-22T16:30:00-04:00")
        st = load(); focus = st["scan"]["focus"]; star = focus[0]["symbol"]; base = focus[0]["close"]
        for r in focus:
            INTRA[r["symbol"]] = five_min("2026-10-23", r["close"], "reclaim" if r["symbol"] == star else "weak", 13)
            fake.prices[r["symbol"]] = r["close"] * 1.008
        run("intraday", "2026-10-23T10:35:00-04:00")
        st = load(); pos = st["positions"][0]
        assert st["broker"]["mode"] == "send" and fake.positions[star]["qty"] == int(pos["size"])
        live_stops = [o for o in fake.orders.values() if o["type"] == "stop" and o["status"] in ("new", "accepted")]
        assert len(live_stops) == 3 and sum(int(o["qty"]) for o in live_stops) == int(pos["size"])
        assert st["broker"]["fills"] and st["broker"]["fills"][0]["symbol"] == star
        entry_px = pos["entry_price"]

        TAIL[star] = {"2026-10-23": (base, base * 1.02, base * 0.99, base * 1.012)}
        STATE["end"] = "2026-10-23"; fake.market_open = False
        run("scan", "2026-10-23T16:30:00-04:00")
        assert not [o for o in fake.orders.values() if o["type"] == "market" and o["side"] == "sell"], "entry day: nothing to mirror"
        px = entry_px
        for d in ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29"]:
            fake.market_open = True; fake.open_market_day()                      # yesterday's queued sells fill at the open
            px *= 1.012
            TAIL[star][d] = (px * 0.998, px * 1.004, px * 0.994, px)
            fake.prices[star] = px; STATE["end"] = d; fake.market_open = False
            run("scan", f"{d}T16:30:00-04:00")
        fake.market_open = True; fake.open_market_day()
        st = load(); pos = st["positions"][0]
        sim_sold = int(pos["size"]) - int(pos["remaining_size"])
        assert sim_sold > 0, "the simulator should have taken partials by day 4"
        assert fake.positions[star]["qty"] == int(pos["remaining_size"]), (fake.positions[star], pos["remaining_size"])
        live = [o for o in fake.orders.values() if o["type"] == "stop" and o["status"] in ("new", "accepted")]
        assert sum(int(o["qty"]) for o in live) == int(pos["remaining_size"]), "stops must cover exactly what is left"
        run("intraday", "2026-10-30T10:05:00-04:00")
        rec = load()["broker"]["recon"]
        assert rec["ok"] and rec["rows"][0]["status"] == "ok", rec
        assert load()["broker"]["account"]["status"] == "ACTIVE"
    finally:
        _alpaca_off()


def test_alpaca_is_dry_run_by_default_through_the_runner():
    import broker
    from fake_alpaca import FakeAlpaca
    shutil.rmtree(os.environ["PAPER_STATE_DIR"], ignore_errors=True)
    TAIL.clear(); INTRA.clear(); STATE["end"] = "2026-10-22"
    fake = FakeAlpaca(); broker.TRANSPORT = fake; _alpaca_env(send=False)
    try:
        run("scan", "2026-10-22T16:30:00-04:00")
        st = load(); focus = st["scan"]["focus"]; star = focus[0]["symbol"]
        for r in focus:
            INTRA[r["symbol"]] = five_min("2026-10-23", r["close"], "reclaim" if r["symbol"] == star else "weak", 13)
            fake.prices[r["symbol"]] = r["close"]
        run("intraday", "2026-10-23T10:35:00-04:00")
        st = load()
        assert len(st["positions"]) == 1, "the simulator trades as usual"
        assert not [c for c in fake.calls if c[0] in ("POST", "DELETE")], "dry run must not write to the broker"
        assert st["broker"]["mode"] == "dry-run" and any(x["action"] == "buy" for x in st["broker"]["log"])
    finally:
        _alpaca_off()


def test_a_broker_outage_never_stops_the_simulation():
    import broker
    from fake_alpaca import FakeAlpaca
    shutil.rmtree(os.environ["PAPER_STATE_DIR"], ignore_errors=True)
    TAIL.clear(); INTRA.clear(); STATE["end"] = "2026-10-22"
    fake = FakeAlpaca(); fake.auth_ok = False; broker.TRANSPORT = fake; _alpaca_env(send=True)
    try:
        run("scan", "2026-10-22T16:30:00-04:00")
        st = load(); focus = st["scan"]["focus"]; star = focus[0]["symbol"]
        for r in focus:
            INTRA[r["symbol"]] = five_min("2026-10-23", r["close"], "reclaim" if r["symbol"] == star else "weak", 13)
        run("intraday", "2026-10-23T10:35:00-04:00")
        st = load()
        assert len(st["positions"]) == 1, "a dead broker must not block the paper account"
        assert st["broker"]["errors"] and any("Alpaca mirror" in e["msg"] for e in st["events"])
    finally:
        _alpaca_off()


def data_mod_leveraged():
    import data
    return data.LEVERAGED_ETFS


def test_regime_gate_blocks_or_halves_entries_in_the_runner():
    shutil.rmtree(os.environ["PAPER_STATE_DIR"], ignore_errors=True)
    TAIL.clear(); INTRA.clear(); STATE["end"] = "2026-10-22"
    run("scan", "2026-10-22T16:30:00-04:00")
    st = load(); focus = st["scan"]["focus"]; star = focus[0]["symbol"]
    assert st["regime"]["label"] in ("risk-on", "neutral", "stretched", "risk-off", "unknown") and st["regime"]["date"] == "2026-10-22"
    for r in focus:
        INTRA[r["symbol"]] = five_min("2026-10-23", r["close"], "reclaim" if r["symbol"] == star else "weak", 13)

    st["regime"].update(label="risk-off", mult=0.0, reasons=["breadth 21% of names above their 20-MA"]); paper.save_state(st)
    run("intraday", "2026-10-23T10:35:00-04:00")
    st = load()
    assert not st["positions"], "risk-off must block the entry"
    assert any(a.startswith("Regime gate") for a in st["alerts"])
    assert any(x["symbol"] == star and x["reason"].startswith("blocked: market regime") for x in st["signals"])

    st["regime"].update(label="neutral", mult=0.5, reasons=["breadth 42%"]); paper.save_state(st)
    run("intraday", "2026-10-23T11:05:00-04:00")
    pos = load()["positions"]
    assert len(pos) == 1 and pos[0]["risk_amount"] <= 100_000 * 0.0020 * 0.5 + 5, pos[0]["risk_amount"]
    assert any("regime neutral" in n for n in pos[0]["notes"])


def test_dashboard_js_runs():
    node = shutil.which("node")
    if not node:
        print("      (node not installed, skipping JS execution)"); return
    html = open(os.path.join(TMP, "docs", "index.html"), encoding="utf-8").read()
    data = html.split('<script id="data" type="application/json">')[1].split("</script>")[0]
    json.loads(data.replace("<\\/", "</"))                      # payload is valid JSON
    code = html.split("<script>")[1].split("</script>")[0]
    harness = r"""
const els = {};
const mk = () => ({ innerHTML: '', textContent: '' });
global.document = { getElementById: id => id === 'data' ? { textContent: process.env.PAYLOAD } : (els[id] = els[id] || mk()),
                    querySelector: s => (els[s] = els[s] || mk()) };
eval(process.env.CODE);
const need = ['#kpis', '#positions', '#focus', '#closed', '#events', '#rules', '#curve'];
for (const k of need) if (!els[k] || !els[k].innerHTML) { console.error('EMPTY ' + k); process.exit(2); }
if (!/Equity/.test(els['#kpis'].innerHTML)) { console.error('no KPI'); process.exit(3); }
console.log('JS OK');
"""
    out = subprocess.run([node, "-e", harness], capture_output=True, text=True,
                         env={**os.environ, "PAYLOAD": data.replace("<\\/", "</"), "CODE": code})
    assert out.returncode == 0, out.stdout + out.stderr


def test_escaping():
    st = paper.new_state()
    st["alerts"] = ["</script><img src=x onerror=alert(1)>"]
    html = dashboard.render_html(st)
    assert html.count("</script>") == 2, "payload must not be able to close the script tag"


if __name__ == "__main__":
    fns = [test_all, test_dashboard_js_runs, test_escaping, test_stop_out_path, test_brake_blocks_entries_and_alerts_fire, test_correlation_blocks_a_clone_through_the_runner, test_earnings_filter_removes_a_focus_name_and_logs_why, test_regime_gate_blocks_or_halves_entries_in_the_runner, test_alpaca_mirror_follows_the_simulator_through_the_runner, test_alpaca_is_dry_run_by_default_through_the_runner, test_a_broker_outage_never_stops_the_simulation]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS  {fn.__name__}")
        except Exception as e:
            import traceback; traceback.print_exc()
            failed += 1; print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if failed else 0)
