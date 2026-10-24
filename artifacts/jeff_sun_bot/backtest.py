"""
Event-driven backtester for the enhanced Jeff Sun style system
"""

import pandas as pd
from strategy import JeffSunStrategy
from data import load_market_data, get_universe
from config import CORR_LOOKBACK, CORR_MIN_OBS, INITIAL_EQUITY, RISK_PRESETS, USE_REGIME_GATE


def compute_regime_labels(market_data):
    """Per-date regime label/multiplier from the loaded universe plus SPY (same rules as the live gate)."""
    import regime
    from data import fetch_history
    from indicators import add_indicators
    spy = fetch_history("SPY", period="2y")
    spy = add_indicators(spy) if not spy.empty else None
    return regime.series_labels(market_data, spy)


def run_backtest(
    symbols=None,
    start_date: str = None,
    end_date: str = None,
    initial_equity: float = INITIAL_EQUITY,
    risk_preset: str = "standard",
    market_data=None,
    exit_variant: str = None,
    day3_be: bool = None,
    quiet: bool = False,
    regime_labels=None,
):
    """market_data: pass an already-loaded dict to compare variants on IDENTICAL data (exit_lab does this)."""
    if symbols is None:
        symbols = get_universe(45)

    risk_pct = RISK_PRESETS.get(risk_preset, 0.0020)
    say = (lambda *a, **k: None) if quiet else print

    say("=" * 65)
    say("JEFF SUN STYLE – ENHANCED BACKTEST")
    say("=" * 65)
    say(f"Risk preset     : {risk_preset} ({risk_pct*100:.2f}% per trade)")
    say(f"Universe        : {len(symbols)} symbols")
    say(f"Initial equity  : ${initial_equity:,.0f}")
    say(f"Exit variant    : {exit_variant or 'config default'} | Day-3 breakeven: {'config default' if day3_be is None else day3_be}")
    say("CAVEATS: hand-picked universe (survivorship bias); entries at the daily close")
    say("         using full-day volume (optimistic vs. a live 30-min ORH entry).")
    say("         No earnings/macro blackout; same portfolio caps and drawdown brakes as the paper account.")
    say()

    if market_data is None:
        market_data = load_market_data(symbols)
    if not market_data:
        return None

    if USE_REGIME_GATE and regime_labels is None:
        regime_labels = compute_regime_labels(market_data)
    all_dates = sorted(set().union(*[set(df.index) for df in market_data.values()]))
    if start_date:
        all_dates = [d for d in all_dates if d >= pd.Timestamp(start_date)]
    if end_date:
        all_dates = [d for d in all_dates if d <= pd.Timestamp(end_date)]

    say(f"Period: {all_dates[0].date()} → {all_dates[-1].date()}  ({len(all_dates)} days)\n")

    strategy = JeffSunStrategy(equity=initial_equity, risk_pct=risk_pct, exit_variant=exit_variant, day3_be=day3_be)

    closes = {sym: df["Close"] for sym, df in market_data.items()}

    def returns_as_of(sym):
        c = closes.get(sym)
        if c is None:
            return None
        window = c.loc[:strategy.current_date].tail(CORR_LOOKBACK + 1)
        return window.pct_change().dropna() if len(window) > CORR_MIN_OBS else None

    strategy.returns_provider = returns_as_of

    for date in all_dates:
        strategy.current_date = date
        if regime_labels is not None and date in regime_labels.index:
            r = regime_labels.loc[date]
            strategy.regime_mult, strategy.regime_label, strategy.regime_why = float(r["mult"]), r["label"], r["why"]

        # Manage open positions
        for sym in list(strategy.positions.keys()):
            if sym in market_data and date in market_data[sym].index:
                i = market_data[sym].index.get_loc(date)
                strategy.manage_open_trades(sym, market_data[sym], i)

        # New entries: evaluate everything first, then take the BEST setups up to the
        # daily/open limits (not whichever symbols happen to come first in the dict).
        candidates = []
        for sym, df in market_data.items():
            if date not in df.index:
                continue
            i = df.index.get_loc(date)
            if i < 60:
                continue
            cand = strategy.evaluate_candidate(sym, df, i)
            if cand:
                candidates.append(cand)
        candidates.sort(key=lambda c: c["rank_score"], reverse=True)
        for cand in candidates:
            strategy.enter(cand)

        strategy.update_equity(date, market_data)
        strategy.end_of_day()

    summary = strategy.summary()
    if quiet:
        return strategy
    print("\n" + "=" * 65)
    print("RESULTS")
    print("=" * 65)
    for k, v in summary.items():
        if isinstance(v, float):
            print(f"  {k:18s}: {v:,.2f}")
        else:
            print(f"  {k:18s}: {v}")

    print("\nRecent closed trades:")
    for t in strategy.closed_trades[-8:]:
        print(f"  {t.symbol:6s} {t.entry_date.date()} → {t.exit_date.date() if t.exit_date else 'open'}  "
              f"R={t.r_multiple:+.2f}  PnL=${t.pnl:+,.0f}  | {t.notes[-1] if t.notes else ''}")

    return strategy


if __name__ == "__main__":
    run_backtest(
        start_date="2025-01-01",
        end_date="2026-10-01",
        risk_preset="standard",
    )
