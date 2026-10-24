#!/usr/bin/env python3
"""
Exit-variant lab: replays the SAME entry signals under different exit rules and prints one comparison table.

    python exit_lab.py                      # hand-picked universe, last ~2 years of daily data
    python exit_lab.py --start 2025-01-01

Variants differ only in how the remainder of a position is exited (10-day MA vs 21-day EMA vs ATR trail) and
whether the Day-3 breakeven stop is used. Everything else (entries, sizing, caps, brakes, fills) is identical,
and the data is loaded once, so differences come from the exit rule alone.

Read it with care: it is a backtest on a hand-picked, survivorship-biased universe with close-of-day entries.
Use it to see which exit looks worse or better by a LOT, then confirm on your paper trades (the dashboard
journal shows best/worst excursion and the share of the best point captured). The result is saved to
state/exit_lab.json and shown on the dashboard.
"""

import argparse
import json
import os
from datetime import datetime

import numpy as np

from backtest import run_backtest
from config import PAPER_STATE_DIR
from data import get_universe, load_market_data

VARIANTS = [
    ("10-MA trail + Day-3 breakeven (current)", "ma10", True),
    ("10-MA trail, no breakeven", "ma10", False),
    ("21-EMA trail + Day-3 breakeven", "ema21", True),
    ("21-EMA trail, no breakeven", "ema21", False),
    ("ATR trail (3x) + Day-3 breakeven", "atr_trail", True),
    ("ATR trail (3x), no breakeven", "atr_trail", False),
]


def compare(market_data, start=None, end=None, variants=VARIANTS) -> list:
    from backtest import compute_regime_labels
    from config import USE_REGIME_GATE
    labels = compute_regime_labels(market_data) if USE_REGIME_GATE else None     # once, shared by every variant
    rows = []
    for label, variant, be in variants:
        strat = run_backtest(symbols=list(market_data), start_date=start, end_date=end, market_data=market_data,
                             exit_variant=variant, day3_be=be, quiet=True, regime_labels=labels)
        s = strat.summary() if strat and strat.closed_trades else {}
        rows.append({
            "variant": label, "trades": s.get("total_trades", 0), "win_rate": s.get("win_rate", 0.0),
            "avg_r": s.get("avg_r", 0.0), "profit_factor": float(min(s.get("profit_factor", 0.0), 999.0)),
            "return_pct": s.get("return_pct", 0.0), "max_drawdown_pct": s.get("max_drawdown_pct", 0.0),
            "capture_ratio": s.get("capture_ratio"),
        })
    return rows


def save(rows: list, note: str) -> str:
    folder = os.environ.get("PAPER_STATE_DIR", PAPER_STATE_DIR)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "exit_lab.json")
    with open(path, "w") as f:
        json.dump({"asof": datetime.utcnow().strftime("%Y-%m-%d"), "note": note, "rows": rows}, f, indent=1)
    return path


def merge_into_state(rows: list, note: str) -> None:
    """Also write the result into paper_state.json so the dashboard picks it up on its next render."""
    from paper import load_state, save_state
    st = load_state()
    st["exit_lab"] = {"asof": datetime.utcnow().strftime("%Y-%m-%d"), "note": note, "rows": rows}
    save_state(st)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--universe", type=int, default=45)
    args = ap.parse_args()
    data = load_market_data(get_universe(args.universe), show_progress=False)
    rows = compare(data, args.start, args.end)
    note = f"{len(data)} symbols, hand-picked universe (survivorship bias)"
    print(f"\n{'variant':44s} {'n':>4s} {'win%':>5s} {'avgR':>6s} {'PF':>5s} {'ret%':>6s} {'maxDD%':>7s} {'capt%':>6s}")
    for r in rows:
        cap = "-" if r["capture_ratio"] is None else f"{r['capture_ratio'] * 100:.0f}"
        print(f"{r['variant']:44s} {r['trades']:4d} {r['win_rate'] * 100:5.0f} {r['avg_r']:6.2f} "
              f"{r['profit_factor']:5.2f} {r['return_pct']:6.1f} {r['max_drawdown_pct']:7.1f} {cap:>6s}")
    print("\nsaved:", save(rows, note))
    merge_into_state(rows, note)


if __name__ == "__main__":
    main()
