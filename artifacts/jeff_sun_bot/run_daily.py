#!/usr/bin/env python3
"""
End-to-end paper-trading automation.

  python run_daily.py scan       after the close (>= 16:15 ET): manage open trades on the final daily bar,
                                 rebuild Stalk / Focus / Mean-reversion lists, mark equity, refresh dashboard
  python run_daily.py intraday   during the session (10:00-15:50 ET), run every ~30 min: check stops on open
                                 positions with 5m bars, test every Focus name for a clean ORH re-claim and
                                 open paper positions (same sizing, limits and 3-stop as the backtest)
  python run_daily.py status     print the account summary
  python run_daily.py dashboard  re-render docs/index.html from the saved state

Both jobs are idempotent and guard their own time window, so scheduling them a little too often (or
in both the EST and EDT UTC slots) is harmless. Use --force to bypass the guards and --now ISO to fake the clock.
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

import broker as broker_mod
import data as data_mod
import datacheck
import earnings
import regime
import scan_core
import signals
import universe as universe_mod
from config import (
    CROSSCHECK_EVERY_DAYS, EARNINGS_WINDOW_DAYS, FOCUS_MAX_AGE_DAYS, INTRADAY_WINDOW_ET, MAX_OPEN_POSITIONS,
    PAPER_LAST_ENTRY_ET, SCAN_NOT_BEFORE_ET, USE_EARNINGS_FILTER, USE_REGIME_GATE,
)
from dashboard import write_dashboard
from data import LEVERAGED_ETFS, fetch_history, get_universe, load_market_data
from notify import Notifier
from indicators import add_indicators
from orh import evaluate_orh_entry, fetch_intraday
from paper import (
    docs_dir, load_state, log_event, mark_to_market, record_equity_point, returns_provider, save_state,
    state_path, store_returns, strategy_from_state, summarize, sync_state_from_strategy,
)
from scan_core import compute_rows, focus_failure, select_lists

ET = ZoneInfo("America/New_York")
LOD_STOP_BUFFER = 0.002          # stop sits 0.2% under the session low
fetch_stooq = datacheck.fetch_stooq   # module-level so tests can swap the second data source


# ------------------------------------------------------------------ clock
def now_et(override: Optional[str] = None) -> datetime:
    raw = override or os.environ.get("JS_FAKE_NOW")
    if raw:
        dt = datetime.fromisoformat(raw)
        return dt.astimezone(ET) if dt.tzinfo else dt.replace(tzinfo=ET)
    return datetime.now(ET)


def hm(dt: datetime):
    return (dt.hour, dt.minute)


def say(msg: str) -> None:
    print(msg, flush=True)


# ------------------------------------------------------------------ helpers
def _recs(df: pd.DataFrame) -> list:
    return df.where(pd.notna(df), None).to_dict("records")


def _index_df(sym: str):
    df = fetch_history(sym, period="1y")
    return add_indicators(df) if (not df.empty and len(df) >= 60) else None


def _market_context(frames: dict) -> list:
    """SPY / QQQ trend read for the dashboard header (objective, from the same indicators)."""
    out = []
    for sym, df in frames.items():
        if df is None:
            continue
        r = df.iloc[-1]
        out.append({
            "symbol": sym, "close": round(float(r["Close"]), 2),
            "above_20": bool(r["Close"] > r["SMA20"]), "above_50": bool(r["Close"] > r["SMA50"]),
            "ext": round(float(r["ATR_ext_50"]), 1) if pd.notna(r["ATR_ext_50"]) else None,
        })
    return out


def _event(state: dict, notifier: Notifier, stamp: str, kind: str, msg: str, notify: bool = True) -> None:
    log_event(state, stamp, kind, msg)
    if notify:
        notifier.add(kind if kind in ("ENTRY", "STOP", "CLOSE", "ERROR", "WARN") else "DIGEST", msg)


def _mirror(state: dict, notifier: Notifier, stamp: str):
    """The Alpaca paper mirror, or None when PAPER_BROKER is not 'alpaca' (or its keys / URL are wrong: then say so)."""
    try:
        client = broker_mod.make_client_from_env()
    except broker_mod.BrokerError as e:
        state["alerts"].append(f"Alpaca mirror is OFF: {e}")
        return None
    if client is None:
        return None
    return broker_mod.Mirror(client, state, lambda kind, msg: _event(state, notifier, stamp, kind, msg))


def _summarize_check(found: dict) -> dict:
    return {k: {"count": len(v), "examples": v[:6]} for k, v in found.items()}


def _entry_idx(df: pd.DataFrame, entry_date: pd.Timestamp) -> int:
    d = pd.Timestamp(entry_date).normalize()
    return int(df.index.get_loc(d)) if d in df.index else int(df.index.searchsorted(d))


# ------------------------------------------------------------------ SCAN (post-close)
def cmd_scan(args) -> int:
    now = now_et(args.now)
    today = now.strftime("%Y-%m-%d")
    if not args.force:
        if now.weekday() >= 5:
            say(f"scan: {today} is a weekend, nothing to do.")
            return 0
        if hm(now) < SCAN_NOT_BEFORE_ET:
            say(f"scan: {now:%H:%M} ET is before {SCAN_NOT_BEFORE_ET[0]:02d}:{SCAN_NOT_BEFORE_ET[1]:02d}; the daily bar is not final yet.")
            return 0

    state = load_state()
    notifier = Notifier()
    held = [p["symbol"] for p in state["positions"]]
    uni, uinfo = universe_mod.ensure_universe(
        now.date(), handpicked=get_universe, leveraged=LEVERAGED_ETFS,
        max_symbols=args.universe or None, force=args.refresh_universe)
    state["universe"] = uinfo
    symbols = list(dict.fromkeys(uni + held))
    data_mod.USE_FUND_CACHE = True
    say(f"scan: universe {uinfo['source']} ({uinfo['n']} names, {uinfo['note']}); loading {len(symbols)} symbols...")
    market_data = load_market_data(symbols, show_progress=False)
    if not market_data:
        state["alerts"] = ["Scan failed: no market data could be loaded (Yahoo Finance down or blocked?)."]
        _event(state, notifier, now.isoformat(timespec="minutes"), "ERROR", "scan: no market data could be loaded")
        save_state(state); write_dashboard(state); notifier.flush("paper-trading ERROR")
        return 1

    bar_ts = max(df.index[-1] for df in market_data.values())
    bar_date = bar_ts.strftime("%Y-%m-%d")
    if not args.force and bar_date != today:
        say(f"scan: newest bar is {bar_date}, not {today} (market holiday or data lag). Skipping.")
        return 0
    if not args.force and state["last_run"].get("scan") == bar_date:
        say(f"scan: {bar_date} already processed.")
        return 0

    stamp = now.isoformat(timespec="minutes")
    strat = strategy_from_state(state, today=today)
    state["alerts"] = []
    if uinfo["source"] == "handpicked":
        state["alerts"].append("Universe: hand-picked list in use (survivorship bias): " + uinfo["note"])

    mirror = _mirror(state, notifier, stamp)

    # ---- 0) data health
    found = datacheck.sanity_checks(market_data, bar_ts, LEVERAGED_ETFS)
    prev = state.get("datacheck", {})
    dc = {"asof": stamp, "sanity": _summarize_check(found), "crosscheck": prev.get("crosscheck"),
          "last_crosscheck": prev.get("last_crosscheck")}
    last_cc = dc["last_crosscheck"]
    if not last_cc or (now.date() - date.fromisoformat(last_cc)).days >= CROSSCHECK_EVERY_DAYS:
        sample = held + [r["symbol"] for r in state["scan"].get("focus", [])] + sorted(market_data)[:40]
        dc["crosscheck"] = datacheck.crosscheck(market_data, sample, fetcher=fetch_stooq)
        dc["last_crosscheck"] = today
        for m in dc["crosscheck"]["mismatched"]:
            msg = f"{m['symbol']}: Yahoo close {m['yahoo']} vs Stooq {m['stooq']} ({m['diff_pct']}% apart on {m['date']})"
            state["alerts"].append("Data cross-check: " + msg)
            _event(state, notifier, stamp, "WARN", "data cross-check mismatch: " + msg)
    for kind, v in found.items():
        if kind != "stale last bar" or len(v) > 3:        # a few stale names is normal; many means a data problem
            state["alerts"].append(f"Data check, {kind}: {len(v)} symbol(s), e.g. {', '.join(v[:4])}")
    state["datacheck"] = dc

    # ---- 1) manage open positions on the final daily bar
    for sym, trade in list(strat.positions.items()):
        df = market_data.get(sym)
        if df is None or bar_ts not in df.index:
            state["alerts"].append(f"{sym}: no daily bar for {bar_date}; position not managed today.")
            continue
        i = int(df.index.get_loc(bar_ts))
        trade.entry_idx = _entry_idx(df, trade.entry_date)
        close = float(df["Close"].iloc[i])
        before = len(trade.notes)
        sig_before = tuple((round(t.stop, 4), t.shares) for t in trade.tranches)
        if i - trade.entry_idx <= 0:
            # Entry day: the daily low includes lows from before we got in, so judge stops with 5m bars only.
            bars = fetch_intraday(sym, "5m", days=5)
            for ev in strat.apply_intraday_stops(trade, bars):
                _event(state, notifier, stamp, "STOP", ev)
            trade.day_count = 0
        else:
            strat.manage_open_trades(sym, df, i)
        if sym in strat.positions:
            trade.last_price = close
            trade.last_check = f"{bar_date}T23:59:59"      # intraday job only looks at NEW bars from here
        new_notes = trade.notes[before:]
        for note in new_notes:
            _event(state, notifier, stamp, "MANAGE", f"{sym}: {note}", notify=False)
        if mirror is not None and i - trade.entry_idx > 0:
            sells = broker_mod.sells_from_notes(new_notes)
            if sells:
                mirror.safe(f"exit {sym}", mirror.on_sim_sells, trade, sells, close)
            elif sym in strat.positions and tuple((round(t.stop, 4), t.shares) for t in trade.tranches) != sig_before:
                mirror.safe(f"stops {sym}", mirror.sync_protection, trade, None, close)
        if sym not in strat.positions:
            _event(state, notifier, stamp, "CLOSE",
                   f"{sym}: closed, R={trade.r_multiple:+.2f} (MFE {trade.mfe_r:+.1f}R / MAE {trade.mae_r:+.1f}R), P&L ${trade.pnl:,.0f}")

    if mirror is not None:
        mirror.safe("reconcile", mirror.reconcile, strat.positions, strat.equity)
        mirror.finish()

    # ---- 2) mark equity and record the day
    mark_to_market(strat)
    sync_state_from_strategy(state, strat, today=today)
    record_equity_point(state, bar_date, strat.equity, strat.cash)

    # ---- 3a) market regime: breadth of the universe + SPY trend/extension
    frames = {"SPY": _index_df("SPY"), "QQQ": _index_df("QQQ")}
    prev_label = (state.get("regime") or {}).get("label")
    reg = regime.snapshot(market_data, frames["SPY"], extra={"date": bar_date, "asof": stamp})
    state["regime"] = reg
    if prev_label and prev_label != reg["label"]:
        _event(state, notifier, stamp, "WARN",
               f"market regime {prev_label} -> {reg['label']} (new-entry risk x{reg['mult']:g}): {'; '.join(reg['reasons'])}")

    # ---- 3b) earnings filter: blockers run best-first on names that already pass every other Focus rule
    earnings.set_as_of(now.date())
    scan_core.FOCUS_BLOCKERS[:] = [earnings.earnings_blocker] if USE_EARNINGS_FILTER else []

    # ---- 3c) rebuild the lists for tomorrow
    rows, _atrs, _vols = compute_rows(market_data)
    if rows:
        df = pd.DataFrame(rows).sort_values("score", ascending=False)
        stalk, focus, mr = select_lists(df)
        state["scan"] = {
            "date": bar_date, "asof": stamp, "n_universe": len(market_data),
            "market": _market_context(frames),
            "stalk": _recs(stalk), "focus": _recs(focus), "mr": _recs(mr),
        }
        flagged = [r for r in _recs(pd.concat([stalk, focus]).drop_duplicates("symbol")) if r.get("flag")]
        state["alerts"] += [f"{r['symbol']}: {r['flag']}" for r in flagged]
        if USE_EARNINGS_FILTER:
            for rec in state["scan"]["focus"]:
                d = None if rec.get("LEV") == "Y" else earnings.next_earnings(rec["symbol"])
                rec["earn"] = d.isoformat() if d else ""
            for sym in list(strat.positions):
                n = earnings.days_until(sym) if sym not in data_mod.LEVERAGED_ETFS else None
                if n is not None and 0 <= n <= EARNINGS_WINDOW_DAYS:
                    msg = f"{sym} (open position) reports earnings in {n} day(s): gap risk through the 3-stop"
                    state["alerts"].append("Earnings: " + msg)
                    _event(state, notifier, stamp, "WARN", msg)
            if len(earnings.UNKNOWN) >= 5:
                state["alerts"].append(f"Earnings lookups failed for {len(earnings.UNKNOWN)} names (they were NOT blocked): "
                                       + ", ".join(sorted(earnings.UNKNOWN)[:6]))
            state["datacheck"]["earnings_unknown"] = sorted(earnings.UNKNOWN)[:30]
        store_returns(state, market_data, list(dict.fromkeys(focus["symbol"].tolist() + list(strat.positions))))
        failures = {r["symbol"]: focus_failure(r) for r in rows}
        signals.log_scan_rejects(state, bar_date, rows, focus["symbol"].tolist(), failures)
        log_event(state, stamp, "SCAN", f"{bar_date}: {len(stalk)} stalk, {len(focus)} focus "
                                        f"({', '.join(focus['symbol'].tolist()) or 'none'}), equity ${strat.equity:,.0f}")
    else:
        state["scan"].update({"date": bar_date, "asof": stamp, "n_universe": len(market_data)})
        log_event(state, stamp, "SCAN", f"{bar_date}: no names passed the minimum filters")

    signals.update_forward(state, market_data)
    signals.prune(state, today)
    rs = strat.risk_state()
    foc = ", ".join(r["symbol"] for r in state["scan"]["focus"]) or "none"
    _event(state, notifier, stamp, "DIGEST",
           f"{bar_date} close: equity ${strat.equity:,.0f} ({(strat.equity / state['initial_equity'] - 1) * 100:+.2f}%), "
           f"{len(strat.positions)} open. Regime {reg['label']} (x{reg['mult']:g}). Focus: {foc}." + (f" BRAKE: {'; '.join(rs['reasons'])}." if rs["reasons"] else ""))
    state["last_run"]["scan"] = bar_date
    save_state(state)
    write_dashboard(state)
    notifier.flush(f"paper-trading {bar_date}")
    say(f"scan: done. equity ${strat.equity:,.0f}, {len(strat.positions)} open, focus={[r['symbol'] for r in state['scan']['focus']]}")
    return 0


# ------------------------------------------------------------------ INTRADAY (ORH entries + stops)
def cmd_intraday(args) -> int:
    now = now_et(args.now)
    today = now.strftime("%Y-%m-%d")
    (h0, m0), (h1, m1) = INTRADAY_WINDOW_ET
    if not args.force:
        if now.weekday() >= 5:
            say("intraday: weekend.")
            return 0
        if not ((h0, m0) <= hm(now) <= (h1, m1)):
            say(f"intraday: {now:%H:%M} ET is outside {h0:02d}:{m0:02d}-{h1:02d}:{m1:02d}.")
            return 0

    state = load_state()
    notifier = Notifier()
    strat = strategy_from_state(state, today=today)
    strat.returns_provider = returns_provider(state)       # correlation cap uses the returns saved by the last scan
    reg = state.get("regime") or {}
    reg_fresh = bool(reg.get("date")) and (now.date() - date.fromisoformat(reg["date"])).days <= FOCUS_MAX_AGE_DAYS
    if USE_REGIME_GATE and reg_fresh:                       # last night's read: Jeff checks the tape before the open too
        strat.regime_mult, strat.regime_label = float(reg["mult"]), reg["label"]
        strat.regime_why = "; ".join(reg.get("reasons", []))
    regime_noted = state.get("day", {}).get("regime_noted") == today
    stamp = now.isoformat(timespec="minutes")
    data_mod.USE_FUND_CACHE = True
    brake_noted = state.get("day", {}).get("brake_noted") == today

    # ---- 1) stops on everything we hold, using 5m bars since the last check
    for sym, trade in list(strat.positions.items()):
        bars = fetch_intraday(sym, "5m", days=5)
        for ev in strat.apply_intraday_stops(trade, bars):
            _event(state, notifier, stamp, "CLOSE" if "position closed" in ev else "STOP", ev)
    mark_to_market(strat)                       # brakes must see today's equity BEFORE we decide to add risk

    # ---- 2) new entries from the Focus list
    checked = []
    scan = state.get("scan", {})
    focus = scan.get("focus", [])
    fresh = bool(scan.get("date")) and (now.date() - date.fromisoformat(scan["date"])).days <= FOCUS_MAX_AGE_DAYS
    closed_today = {c["symbol"] for c in state["closed"] if (c.get("exit_date") or "")[:10] == today}
    can_enter = hm(now) <= PAPER_LAST_ENTRY_ET or args.force
    rs = strat.risk_state()

    state["alerts"] = [a for a in state["alerts"] if not a.startswith(("Focus list", "Risk brake", "Regime gate", "Alpaca"))]
    mirror = _mirror(state, notifier, stamp)                   # after the alert reset, so "Alpaca mirror is OFF" survives
    if USE_REGIME_GATE and reg_fresh and strat.regime_mult < 1.0:
        state["alerts"].append(f"Regime gate: {strat.regime_label}, new-entry risk x{strat.regime_mult:g}: {strat.regime_why}")
        if strat.regime_mult <= 0 and not regime_noted:
            _event(state, notifier, stamp, "WARN", f"new entries paused by the regime gate ({strat.regime_label}): {strat.regime_why}")
            regime_noted = True
    if rs["reasons"]:
        state["alerts"].append("Risk brake: " + "; ".join(rs["reasons"]))
        if rs["halted"] and not brake_noted:
            _event(state, notifier, stamp, "WARN", "new entries paused: " + "; ".join(rs["reasons"]))
            brake_noted = True
    if not fresh:
        state["alerts"].append(f"Focus list is stale (scan date {scan.get('date')}); no new entries until the next scan.")
    else:
        candidates = []
        for rec in focus:
            sym = rec["symbol"]
            if sym in strat.positions:
                checked.append({"symbol": sym, "passed": False, "reason": "held"})
                continue
            if sym in closed_today:
                checked.append({"symbol": sym, "passed": False, "reason": "stopped out today, no re-entry"})
                continue
            passed, res, reason = evaluate_orh_entry(sym, daily_atr=rec.get("ATR"), avg_daily_volume=rec.get("AvgVol50"))
            if res is not None and res.date != today and not args.force:
                passed, reason = False, "no session today"
            checked.append({"symbol": sym, "passed": bool(passed), "reason": reason})
            if passed:
                candidates.append((res.time_rvol or res.dayvol_pct or 0.0, rec, res))
            else:
                signals.record(state, today, sym, "orh_skip", signals.categorize_orh(reason),
                               res.current_price if res else rec["close"], rec.get("ATR", 0), detail=reason)

        candidates.sort(key=lambda c: c[0], reverse=True)      # heaviest volume for the time of day first
        verdict = {c["symbol"]: c for c in checked}
        for _rank, rec, res in candidates:
            sym = rec["symbol"]
            why = None
            if not can_enter:
                why = "past the last-entry time"
            elif rs["halted"]:
                why = "; ".join(rs["reasons"]) or "risk halted"
            trade = None
            if why is None:
                stop = res.session_low * (1 - LOD_STOP_BUFFER)
                bar_label = pd.Timestamp(now.replace(tzinfo=None)).floor("5min").isoformat()
                trade = strat.enter_intraday(
                    sym, price=res.current_price, stop=stop, date=pd.Timestamp(today), bar_label=bar_label,
                    is_leveraged=(rec.get("LEV") == "Y"), sector=rec.get("sector") or "", industry=rec.get("industry") or "",
                    atr=rec.get("ATR") or 0.0, avg_vol=rec.get("AvgVol50") or 0.0,
                    note=f"ORH entry | {res.notes} | LoD/ATR {res.lod_atr_multiple:.2f} | VCP {rec.get('VCP')} ext {rec.get('ATR_ext')}x",
                )
                why = None if trade else strat.last_block
            if trade:
                _event(state, notifier, stamp, "ENTRY",
                       f"{trade.symbol}: bought {int(trade.size)} @ {trade.entry_price:.2f}, full stop {trade.stop_price:.2f} "
                       f"(risk ${trade.risk_amount:,.0f}); 3-stop {', '.join(f'{t.stop:.2f}' for t in trade.tranches)}")
                if mirror is not None:
                    mirror.safe(f"entry {sym}", mirror.on_entry, trade, today)
                state["signals"] = [x for x in state.get("signals", []) if x["id"] != f"{today}:{sym}:orh_skip"]
                signals.record(state, today, sym, "taken", "entered", res.current_price, rec.get("ATR", 0))
                verdict[sym]["reason"] = "entered: " + verdict[sym]["reason"]
            else:
                _event(state, notifier, stamp, "SKIP", f"{sym}: ORH ready but blocked: {why}", notify=False)
                verdict[sym].update(passed=False, reason=f"blocked: {why}")
                signals.record(state, today, sym, "orh_skip", signals.categorize_orh("blocked: " + str(why)),
                               res.current_price, rec.get("ATR", 0), detail=str(why))
            if len(strat.positions) >= MAX_OPEN_POSITIONS:
                break

    if mirror is not None:
        mirror.safe("reconcile", mirror.reconcile, strat.positions, strat.equity)
        mirror.finish()

    # ---- 3) mark and save (today's equity point is provisional; the scan job overwrites it at the close)
    mark_to_market(strat)
    sync_state_from_strategy(state, strat, today=today)
    if brake_noted:
        state["day"]["brake_noted"] = today
    if regime_noted:
        state["day"]["regime_noted"] = today
    record_equity_point(state, today, strat.equity, strat.cash)
    state["intraday"] = {"asof": now.replace(tzinfo=None).isoformat(timespec="minutes"), "checked": checked}
    state["last_run"]["intraday"] = now.replace(tzinfo=None).isoformat(timespec="minutes")
    save_state(state)
    write_dashboard(state)
    notifier.flush(f"paper-trading {now:%H:%M} ET")
    say(f"intraday: {len(checked)} focus names checked, {len(strat.positions)} open, equity ${strat.equity:,.0f}")
    return 0


# ------------------------------------------------------------------ misc commands
def cmd_status(args) -> int:
    state = load_state()
    s = summarize(state)
    say(f"state file : {state_path()}")
    say(f"equity     : ${s['equity']:,.0f} ({s['return_pct']:+.2f}%)   cash ${s['cash']:,.0f}")
    say(f"open       : {s['open_positions']}   closed: {s['closed_trades']}   win rate: {s['win_rate']}   avg R: {s['avg_r']}")
    say(f"last scan  : {state['last_run'].get('scan')}   last intraday: {state['last_run'].get('intraday')}")
    rs = strategy_from_state(state, today=None).risk_state()
    say(f"risk brake : drawdown {rs['dd'] * 100:.1f}% from peak, risk multiplier x{rs['mult']:g}" + (f"  ({'; '.join(rs['reasons'])})" if rs['reasons'] else ""))
    say(f"universe   : {state.get('universe', {}).get('source')} ({state.get('universe', {}).get('n')} names)")
    for p in state["positions"]:
        say(f"  {p['symbol']:6s} {int(p['remaining_size'])}/{int(p['size'])} sh  entry {p['entry_price']:.2f}  last {p.get('last_price') or 0:.2f}")
    return 0


def cmd_dashboard(args) -> int:
    path = write_dashboard(load_state())
    say(f"dashboard written: {path}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["scan", "intraday", "status", "dashboard"])
    ap.add_argument("--force", action="store_true", help="ignore time-window and already-processed guards")
    ap.add_argument("--now", help="fake the clock, e.g. 2026-10-23T10:35:00-04:00 (testing)")
    ap.add_argument("--universe", type=int, default=0, help="cap on symbols to scan (0 = the whole universe)")
    ap.add_argument("--refresh-universe", action="store_true", help="rebuild the rules-based universe now")
    args = ap.parse_args(argv)
    return {"scan": cmd_scan, "intraday": cmd_intraday, "status": cmd_status, "dashboard": cmd_dashboard}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
