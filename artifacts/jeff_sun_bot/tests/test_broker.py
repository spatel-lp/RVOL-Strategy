"""
Alpaca mirror tests against an in-memory fake that enforces Alpaca's documented rules (unique client_order_id,
no selling shares already held by open sell orders). Run:  python tests/test_broker.py
These prove OUR logic and request shapes. They cannot prove the real service behaves like the fake:
run `python broker.py --check` with your paper keys, then a dry run, before sending orders.
"""
import os, sys, tempfile, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PAPER_STATE_DIR"] = tempfile.mkdtemp(prefix="brk_")
if "yfinance" not in sys.modules:
    try:
        import yfinance  # noqa: F401
    except ModuleNotFoundError:
        sys.modules["yfinance"] = types.ModuleType("yfinance")

import pandas as pd
import broker as B
import strategy as S
from fake_alpaca import FakeAlpaca

S.COMMISSION_PCT = 0.0
S.SLIPPAGE_PCT = 0.0
TODAY = "2026-10-23"


def rig(send=True, price=100.0, stop=98.0):
    fake = FakeAlpaca(); fake.prices["AAA"] = price
    client = B.AlpacaPaper("KEY", "SECRET", send_orders=send, transport=fake, sleep=lambda s: None)
    state, said = {}, []
    mirror = B.Mirror(client, state, lambda kind, msg: said.append((kind, msg)))
    sim = S.JeffSunStrategy(equity=100_000)
    trade = sim.enter_intraday("AAA", price, stop, pd.Timestamp(TODAY), TODAY + "T10:30", sector="Energy", industry="Oil")
    assert trade and int(trade.size) == 100 and [t.shares for t in trade.tranches] == [33, 33, 34]
    return fake, client, mirror, state, said, sim, trade


def stops(fake, sym="AAA"):
    return sorted([o for o in fake.orders.values() if o["symbol"] == sym and o["type"] == "stop" and o["status"] in ("new", "accepted")],
                  key=lambda o: -float(o["stop_price"]))


def test_only_the_paper_endpoint_is_accepted():
    for bad in ("https://api.alpaca.markets", "https://evil.example.com/paper-api.alpaca.markets", "http://localhost:8080"):
        try:
            B.AlpacaPaper("K", "S", base_url=bad)
        except B.BrokerError as e:
            assert "PAPER" in str(e)
        else:
            raise AssertionError(f"{bad} must be refused")
    assert B.AlpacaPaper("K", "S", base_url="https://paper-api.alpaca.markets/").base == "https://paper-api.alpaca.markets"


def test_keys_come_from_env_and_missing_keys_are_reported():
    for k in ("PAPER_BROKER", "ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY", "ALPACA_SEND_ORDERS", "ALPACA_BASE_URL"):
        os.environ.pop(k, None)
    assert B.make_client_from_env() is None
    os.environ["PAPER_BROKER"] = "alpaca"
    try:
        B.make_client_from_env()
    except B.BrokerError as e:
        assert "not set" in str(e)
    else:
        raise AssertionError("missing keys must raise")
    os.environ.update(ALPACA_API_KEY_ID="KEY", ALPACA_API_SECRET_KEY="SECRET")
    c = B.make_client_from_env(transport=FakeAlpaca())
    assert c.send_orders is False, "default is DRY RUN"
    os.environ["ALPACA_SEND_ORDERS"] = "1"
    assert B.make_client_from_env(transport=FakeAlpaca()).send_orders is True
    os.environ["ALPACA_BASE_URL"] = "https://api.alpaca.markets"
    try:
        B.make_client_from_env(transport=FakeAlpaca())
    except B.BrokerError:
        pass
    else:
        raise AssertionError("a live URL must be refused even when set in the environment")
    for k in ("PAPER_BROKER", "ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY", "ALPACA_SEND_ORDERS", "ALPACA_BASE_URL"):
        os.environ.pop(k, None)


def test_dry_run_reads_but_never_writes():
    fake, client, mirror, state, said, sim, trade = rig(send=False)
    mirror.on_entry(trade, TODAY)
    mirror.reconcile(sim.positions, sim.equity)
    assert not [c for c in fake.calls if c[0] in ("POST", "DELETE")], "dry run must not send any write"
    assert [c for c in fake.calls if c[1] == "/v2/account"], "reads are fine"
    acts = [d["action"] for d in client.dry_log]
    assert acts.count("submit_order") == 4, acts                     # buy + 3 stops, all only logged
    assert state["broker"]["mode"] == "dry-run" and state["broker"]["log"] and not fake.positions
    assert "dry run" in state["broker"]["recon"]["note"]


def test_entry_buys_then_places_three_gtc_stops_and_records_the_fill():
    fake, client, mirror, state, said, sim, trade = rig(send=True)
    fake.fill_slip = 0.001                                           # the broker fills 0.1% worse than the simulator
    mirror.on_entry(trade, TODAY)
    assert fake.positions["AAA"]["qty"] == 100
    st = stops(fake)
    assert [int(o["qty"]) for o in st] == [33, 33, 34] and [o["stop_price"] for o in st] == ["99.34", "98.66", "98.00"]
    assert all(o["time_in_force"] == "gtc" and o["side"] == "sell" for o in st)
    posts = [c for c in fake.calls if c[0] == "POST"]
    assert posts[0][2]["side"] == "buy" and posts[0][2]["type"] == "market" and posts[0][2]["qty"] == "100"
    ids = [c[2]["client_order_id"] for c in posts]
    assert len(set(ids)) == len(ids) == 4 and all(i.startswith("orh-20261023-AAA-") for i in ids)
    fill = state["broker"]["fills"][0]
    assert abs(fill["diff_pct"] - 0.1) < 0.01 and fill["sim_price"] == 100.0, fill


def test_second_entry_call_is_idempotent():
    fake, client, mirror, state, said, sim, trade = rig(send=True)
    mirror.on_entry(trade, TODAY); mirror.on_entry(trade, TODAY)
    assert fake.positions["AAA"]["qty"] == 100, "the buy must not be placed twice"
    assert len(stops(fake)) == 3 and sum(int(o["qty"]) for o in stops(fake)) == 100


def test_untradable_symbol_is_skipped_and_reported():
    fake, client, mirror, state, said, sim, trade = rig(send=True)
    fake.tradable["AAA"] = False
    mirror.on_entry(trade, TODAY)
    assert not [c for c in fake.calls if c[0] == "POST"]
    assert state["broker"]["orders"]["AAA"]["status"] == "skipped" and any("not tradable" in m for _, m in said)


def test_simulator_exit_cancels_stops_first_then_sells_then_reprotects():
    fake, client, mirror, state, said, sim, trade = rig(send=True)
    mirror.on_entry(trade, TODAY)
    fake.market_open = False                                         # the scan runs after the close: market orders queue
    before = len(trade.notes)
    sim._reduce(trade, 0.33, 103.0, "2R partial")                    # the simulator sells 33
    sells = B.sells_from_notes(trade.notes[before:])
    assert sells == [(33, "2R partial")]
    mirror.on_sim_sells(trade, sells, 103.0)                         # would raise (403 insufficient qty) if stops were not freed first
    queued = [o for o in fake.orders.values() if o["type"] == "market" and o["side"] == "sell"]
    assert len(queued) == 1 and queued[0]["qty"] == "33" and queued[0]["status"] == "accepted"
    assert sum(int(o["qty"]) for o in stops(fake)) == int(trade.remaining_size) == 67, "stops cover exactly what is left"
    fake.market_open = True; fake.open_market_day()                  # next morning
    assert fake.positions["AAA"]["qty"] == 67
    rec = mirror.reconcile(sim.positions, sim.equity)
    assert rec["ok"] and rec["rows"][0]["status"] == "ok", rec


def test_full_exit_leaves_no_stops_behind():
    fake, client, mirror, state, said, sim, trade = rig(send=True)
    mirror.on_entry(trade, TODAY)
    fake.market_open = False
    qty = int(trade.remaining_size)
    sim._sell(trade, qty, 104.0, "10-MA trail exit"); trade.tranches = []
    mirror.on_sim_sells(trade, [(qty, "10-MA trail exit")], 104.0)
    assert not stops(fake)
    fake.market_open = True; fake.open_market_day()
    assert "AAA" not in fake.positions


def test_a_stop_at_or_above_the_market_becomes_an_immediate_exit_order():
    fake, client, mirror, state, said, sim, trade = rig(send=True)
    mirror.on_entry(trade, TODAY)
    for tr in trade.tranches:
        tr.stop = 101.0                                              # breakeven stop with the price back under it
    fake.market_open = False
    mirror.sync_protection(trade, last_price=100.0)
    assert not stops(fake)
    assert sum(int(o["qty"]) for o in fake.orders.values() if o["type"] == "market" and o["side"] == "sell") == 100


def test_reconcile_reports_differences_and_heals_missing_stops():
    fake, client, mirror, state, said, sim, trade = rig(send=True)
    mirror.on_entry(trade, TODAY)
    ok = mirror.reconcile(sim.positions, sim.equity)
    assert ok["ok"] and state["broker"]["account"]["status"] == "ACTIVE"
    for o in stops(fake):                                            # someone cancels the stops by hand
        o["status"] = "canceled"
    healed = mirror.reconcile(sim.positions, sim.equity)
    assert "stops re-placed" in healed["rows"][0]["status"] and len(stops(fake)) == 3
    fake.positions["AAA"]["qty"] = 90                                # broker holds fewer shares than the simulator
    bad = mirror.reconcile(sim.positions, sim.equity, ensure_stops=False)
    assert not bad["ok"] and bad["rows"][0]["status"] == "quantity differs" and any(k == "WARN" for k, _ in said)
    fake.positions["ZZZ"] = {"qty": 5, "avg": 10.0}
    assert {r["symbol"]: r["status"] for r in mirror.reconcile(sim.positions, sim.equity, ensure_stops=False)["rows"]}["ZZZ"] == "at broker only"


def test_pending_exit_is_not_reported_as_a_mismatch():
    fake, client, mirror, state, said, sim, trade = rig(send=True)
    mirror.on_entry(trade, TODAY)
    fake.market_open = False
    sim._reduce(trade, 0.33, 103.0, "2R partial")
    mirror.on_sim_sells(trade, [(33, "2R partial")], 103.0)
    rec = mirror.reconcile(sim.positions, sim.equity)                # broker 100, sim 67, 33 queued to sell
    assert rec["ok"] and rec["rows"][0]["status"].startswith("pending exit")


def test_failures_never_propagate_and_never_leak_keys():
    fake, client, mirror, state, said, sim, trade = rig(send=True)
    fake.auth_ok = False
    assert mirror.safe("entry AAA", mirror.on_entry, trade, TODAY) is None
    errs = state["broker"]["errors"]
    assert errs and "401" in errs[-1]["msg"], errs
    assert "SECRET" not in str(state) and "KEY" not in errs[-1]["msg"].replace("401", ""), "keys must never be stored or echoed"
    assert not state["broker"]["orders"].get("AAA", {}).get("status") == "skipped", "an auth failure is not 'not tradable'"
    assert any("Alpaca mirror" in m for _, m in said)


def test_sells_from_notes_ignores_stop_fills_and_entries():
    notes = ["ORH entry | M30 Re-ORH", "Stop @ 99.34: sold 33 @ 99.34", "2R partial: sold 33 @ 103.00",
             "Day3 reduction: sold 12 @ 104.00", "ATR-ext trim (7.2x >= 7x): sold 5 @ 110.10", "Stops -> breakeven (Day 3)", "Closed: Hit stop(s)"]
    assert B.sells_from_notes(notes) == [(33, "2R partial"), (12, "Day3 reduction"), (5, "ATR-ext trim (7.2x >= 7x)")]


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
