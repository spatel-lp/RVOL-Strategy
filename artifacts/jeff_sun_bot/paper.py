"""
Paper-trading state: one JSON file that every job loads, updates and saves.

The state file is the single source of truth. The dashboard is rendered from it, and the
scheduled jobs (run_daily.py) are stateless apart from it, so they can run anywhere
(GitHub Actions, cron, a laptop) as long as the file survives between runs.
"""

import json
import math
import os
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from config import (
    BOOTSTRAP_N, CORR_LOOKBACK, INITIAL_EQUITY, MAX_LEVERAGED_EXPOSURE_PCT, MAX_PER_INDUSTRY, MAX_PER_SECTOR,
    MAX_TOTAL_OPEN_RISK_PCT, PAPER_DOCS_DIR, PAPER_STATE_DIR, TARGET_TRADES,
)
from strategy import JeffSunStrategy, Tranche, Trade, TradeStatus

STATE_VERSION = 1
MAX_EVENTS = 400


# ------------------------------------------------------------------ paths
def state_dir() -> str:
    return os.environ.get("PAPER_STATE_DIR", PAPER_STATE_DIR)


def docs_dir() -> str:
    return os.environ.get("PAPER_DOCS_DIR", PAPER_DOCS_DIR)


def state_path() -> str:
    return os.path.join(state_dir(), "paper_state.json")


# ------------------------------------------------------------------ JSON safety
def _clean(obj: Any) -> Any:
    """Make anything JSON-safe: numpy -> python, NaN/inf -> None, Timestamp -> ISO string, sets -> lists."""
    if obj is None or isinstance(obj, (str, bool)):
        return obj
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        return f if math.isfinite(f) else None
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        items = sorted(obj) if isinstance(obj, set) else obj
        return [_clean(v) for v in items]
    if is_dataclass(obj):
        return {f.name: _clean(getattr(obj, f.name)) for f in fields(obj)}
    return str(obj)


# ------------------------------------------------------------------ trade (de)serialisation
def trade_to_dict(t: Trade) -> dict:
    return _clean(t)


def trade_from_dict(d: dict) -> Trade:
    d = dict(d)
    d["entry_date"] = pd.Timestamp(d["entry_date"])
    if d.get("exit_date"):
        d["exit_date"] = pd.Timestamp(d["exit_date"])
    d["status"] = TradeStatus(d.get("status", "open"))
    d["tranches"] = [Tranche(**tr) for tr in d.get("tranches", [])]
    d["ext_levels_hit"] = set(d.get("ext_levels_hit", []))
    known = {f.name for f in fields(Trade)}
    d = {k: v for k, v in d.items() if k in known}
    return Trade(**d)


# ------------------------------------------------------------------ state
def new_state(initial_equity: float = INITIAL_EQUITY) -> dict:
    return {
        "version": STATE_VERSION,
        "created": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "initial_equity": float(initial_equity),
        "cash": float(initial_equity),
        "equity": float(initial_equity),
        "positions": [],
        "closed": [],
        "equity_curve": [],            # [{date, equity, cash}] one point per trading day
        "scan": {"date": None, "asof": None, "stalk": [], "focus": [], "mr": [], "n_universe": 0},
        "intraday": {"asof": None, "checked": []},   # last ORH pass: [{symbol, passed, reason}]
        "day": {"date": None, "new_trades": 0},
        "last_run": {"scan": None, "intraday": None},   # scan: bar date processed; intraday: ET timestamp
        "events": [],
        "alerts": [],                  # data / pipeline warnings shown on the dashboard
        "signals": [],                 # reject / skip / taken log with forward returns (signals.py)
        "datacheck": {},               # sanity checks + weekly Stooq cross-check (datacheck.py)
        "universe": {},                # which universe snapshot the scan used (universe.py)
        "exit_lab": {},                # last exit-variant comparison (exit_lab.py)
        "regime": {},                  # latest market-regime reading (regime.py)
        "broker": {},                  # Alpaca mirror state (broker.py)
        "returns": {},                 # {"dates": [...], "series": {symbol: [daily returns]}} for Focus + held names
    }


def load_state() -> dict:
    path = state_path()
    if not os.path.exists(path):
        return new_state()
    with open(path) as f:
        state = json.load(f)
    base = new_state(state.get("initial_equity", INITIAL_EQUITY))
    base.update(state)               # forward-compatible: missing keys get defaults
    return base


def save_state(state: dict) -> str:
    os.makedirs(state_dir(), exist_ok=True)
    state["summary"] = summarize(state)
    state["updated"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    path = state_path()
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(_clean(state), f, indent=1)
    os.replace(tmp, path)            # atomic: a crashed run never leaves half a file
    return path


def log_event(state: dict, when: str, kind: str, msg: str) -> None:
    state["events"].append({"t": when, "kind": kind, "msg": msg})
    state["events"] = state["events"][-MAX_EVENTS:]


# ------------------------------------------------------------------ strategy <-> state
def strategy_from_state(state: dict, today: Optional[str] = None) -> JeffSunStrategy:
    strat = JeffSunStrategy(equity=state["equity"])
    strat.cash = float(state["cash"])
    strat.equity = float(state["equity"])
    strat.positions = {}
    for d in state["positions"]:
        t = trade_from_dict(d)
        strat.positions[t.symbol] = t
    strat.closed_trades = [trade_from_dict(d) for d in state["closed"]]
    day = state.get("day", {})
    strat.daily_new_trades = int(day.get("new_trades", 0)) if (today and day.get("date") == today) else 0
    # Risk brakes need the equity high-water mark and last night's close
    curve = state.get("equity_curve", [])
    strat.peak_equity = max([state.get("initial_equity", INITIAL_EQUITY), strat.equity] + [p["equity"] for p in curve])
    prior = [p for p in curve if not today or p["date"] < today]
    strat.day_start_equity = prior[-1]["equity"] if prior else state.get("initial_equity", INITIAL_EQUITY)
    return strat


def sync_state_from_strategy(state: dict, strat: JeffSunStrategy, today: Optional[str] = None) -> None:
    state["cash"] = float(strat.cash)
    state["equity"] = float(strat.equity)
    state["positions"] = [trade_to_dict(t) for t in strat.positions.values()]
    # closed trades are append-only: persist the whole list (cheap, and keeps R stats exact)
    state["closed"] = [trade_to_dict(t) for t in strat.closed_trades]
    if today:
        state["day"] = {"date": today, "new_trades": int(strat.daily_new_trades)}


def mark_to_market(strat: JeffSunStrategy) -> float:
    """equity = cash + remaining shares at the latest price we have (last_price, else entry)."""
    value = strat.cash
    for t in strat.positions.values():
        value += t.remaining_size * (t.last_price or t.entry_price)
    strat.equity = value
    return value


def record_equity_point(state: dict, date: str, equity: float, cash: float) -> None:
    curve = [p for p in state["equity_curve"] if p["date"] != date]
    curve.append({"date": date, "equity": round(float(equity), 2), "cash": round(float(cash), 2)})
    state["equity_curve"] = sorted(curve, key=lambda p: p["date"])[-800:]


# ------------------------------------------------------------------ views for the dashboard
def evidence(rs: List[float]) -> dict:
    """How much do N closed trades actually tell you? Bootstrap CI of the mean R and P(true expectancy > 0)."""
    n = len(rs)
    out = {"n": n, "target": TARGET_TRADES, "progress_pct": round(min(100.0, n / TARGET_TRADES * 100), 0),
           "mean_r": round(float(np.mean(rs)), 3) if n else None, "ci_low": None, "ci_high": None, "p_positive": None}
    if n >= 8:
        rng = np.random.default_rng(0)                         # fixed seed: the dashboard does not flicker between runs
        arr = np.asarray(rs, dtype=float)
        means = arr[rng.integers(0, n, size=(BOOTSTRAP_N, n))].mean(axis=1)
        out["ci_low"], out["ci_high"] = (round(float(v), 3) for v in np.percentile(means, [2.5, 97.5]))
        out["p_positive"] = round(float((means > 0).mean()), 3)
    return out


def summarize(state: dict) -> dict:
    closed = state.get("closed", [])
    rs = [c.get("r_multiple") or 0.0 for c in closed]
    pnls = [c.get("pnl") or 0.0 for c in closed]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gp = sum(p for p in pnls if p > 0)
    gl = -sum(p for p in pnls if p < 0)
    eq = [p["equity"] for p in state.get("equity_curve", [])] or [state["equity"]]
    peak, max_dd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        max_dd = min(max_dd, v / peak - 1)
    open_risk = 0.0
    for t in state.get("positions", []):
        last = t.get("last_price") or t.get("entry_price")
        for tr in t.get("tranches", []):
            open_risk += tr["shares"] * max(0.0, last - tr["stop"])
    init = state.get("initial_equity", INITIAL_EQUITY) or 1
    return {
        "equity": round(state["equity"], 2),
        "return_pct": round((state["equity"] / init - 1) * 100, 2),
        "cash": round(state["cash"], 2),
        "closed_trades": len(closed),
        "open_positions": len(state.get("positions", [])),
        "win_rate": round(len(wins) / len(rs), 3) if rs else None,
        "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
        "avg_win_r": round(sum(wins) / len(wins), 3) if wins else None,
        "avg_loss_r": round(sum(losses) / len(losses), 3) if losses else None,
        "profit_factor": round(gp / gl, 2) if gl > 0 else (None if not gp else 999.0),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "open_risk_usd": round(open_risk, 2),
        "open_risk_pct": round(open_risk / state["equity"] * 100, 2) if state["equity"] else 0.0,
        "evidence": evidence(rs),
        "avg_mfe_r": round(sum(c.get("mfe_r") or 0.0 for c in closed) / len(closed), 2) if closed else None,
        "avg_mae_r": round(sum(c.get("mae_r") or 0.0 for c in closed) / len(closed), 2) if closed else None,
        "capture_ratio": (round(sum(max(c.get("r_multiple") or 0.0, 0.0) for c in closed) /
                                sum(c.get("mfe_r") or 0.0 for c in closed if (c.get("mfe_r") or 0) > 0), 2)
                          if any((c.get("mfe_r") or 0) > 0 for c in closed) else None),
    }


def positions_view(state: dict) -> List[dict]:
    out = []
    for t in state.get("positions", []):
        last = t.get("last_price") or t["entry_price"]
        risk = t.get("initial_risk_per_share") or (t["entry_price"] - t["stop_price"]) or 1e-9
        unreal = t["remaining_size"] * (last - t["entry_price"]) + (t.get("realized_pnl") or 0.0)
        out.append({
            "symbol": t["symbol"], "entry_date": (t["entry_date"] or "")[:10], "entry": t["entry_price"],
            "last": last, "r_now": round((last - t["entry_price"]) / risk, 2),
            "pnl": round(unreal, 2), "day": t.get("day_count", 0),
            "remaining": t["remaining_size"], "size": t["size"],
            "stops": [[round(tr["stop"], 2), tr["shares"]] for tr in t.get("tranches", [])],
            "last_note": (t.get("notes") or [""])[-1],
            "source": t.get("source", ""),
            "mfe": round(t.get("mfe_r") or 0.0, 2), "mae": round(t.get("mae_r") or 0.0, 2),
            "industry": t.get("industry") or t.get("sector") or "",
            "lev": t.get("leverage", 1.0),
        })
    return out


def closed_view(state: dict, limit: int = 60) -> List[dict]:
    out = []
    for c in reversed(state.get("closed", [])[-limit:]):
        out.append({
            "symbol": c["symbol"], "entry_date": (c["entry_date"] or "")[:10],
            "exit_date": (c.get("exit_date") or "")[:10], "entry": c["entry_price"],
            "exit": c.get("exit_price"), "r": round(c.get("r_multiple") or 0.0, 2),
            "pnl": round(c.get("pnl") or 0.0, 2),
            "mfe": round(c.get("mfe_r") or 0.0, 2), "mae": round(c.get("mae_r") or 0.0, 2),
            "note": (c.get("notes") or [""])[-1],
        })
    return out


def risk_view(state: dict) -> dict:
    """Everything the 'Risk controls' dashboard panel shows, computed with the same functions the entries use."""
    from risk import cluster_counts, leveraged_exposure_usd, open_risk_usd
    curve = state.get("equity_curve", [])
    today = curve[-1]["date"] if curve else None
    strat = strategy_from_state(state, today=today)
    rs = strat.risk_state()
    eq = state["equity"] or 1.0
    by_ind, by_sec = cluster_counts(strat.positions.values())
    from risk import held_correlations
    prov = returns_provider(state)
    held = {sym: prov(sym) for sym in strat.positions}
    pairs = held_correlations({k: v for k, v in held.items() if v is not None})
    return {
        "dd_pct": round(rs["dd"] * 100, 2), "mult": rs["mult"], "day_loss_pct": round(rs["day_loss"] * 100, 2),
        "halted": rs["halted"], "reasons": rs["reasons"],
        "open_risk_pct": round(open_risk_usd(strat.positions.values()) / eq * 100, 2),
        "open_risk_cap_pct": MAX_TOTAL_OPEN_RISK_PCT * 100,
        "lev_exposure_pct": round(leveraged_exposure_usd(strat.positions.values()) / eq * 100, 1),
        "lev_cap_pct": MAX_LEVERAGED_EXPOSURE_PCT * 100,
        "industries": by_ind, "sectors": by_sec, "max_industry": MAX_PER_INDUSTRY, "max_sector": MAX_PER_SECTOR,
        "corr_pairs": pairs,
    }


def store_returns(state: dict, market_data: Dict[str, pd.DataFrame], symbols: List[str]) -> None:
    """Keep the last CORR_LOOKBACK daily returns of the Focus + held names so the intraday job can test correlation."""
    closes = pd.DataFrame({s: market_data[s]["Close"] for s in symbols if s in market_data}).tail(CORR_LOOKBACK + 1)
    rets = closes.pct_change().iloc[1:]
    state["returns"] = {
        "dates": [d.strftime("%Y-%m-%d") for d in rets.index],
        "series": {s: [None if pd.isna(v) else round(float(v), 5) for v in rets[s]] for s in rets.columns},
    }


def returns_provider(state: dict):
    """Callable(symbol) -> pd.Series of daily returns from the stored snapshot, or None."""
    store = state.get("returns") or {}
    dates, series = store.get("dates") or [], store.get("series") or {}

    def get(symbol: str):
        vals = series.get(symbol)
        if vals is None or len(vals) != len(dates):
            return None
        return pd.Series([np.nan if v is None else v for v in vals], index=pd.to_datetime(dates), dtype=float)
    return get
