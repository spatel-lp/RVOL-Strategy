# Review fixes

Run `python tests/test_fixes.py` (11 regression tests) and `python tests/smoke_backtest.py`
(offline end-to-end run on synthetic data). Neither needs network access.

## Python bot

| Problem | Fix |
|---|---|
| 10-MA trail never ran after Day 3 (stop == entry made risk 0, early return) | R/risk now uses the immutable `initial_risk_per_share`; trail runs from Day 4 |
| T+3 counted calendar days | `day_count = bar index - entry index` (trading days) |
| Day-3 reduction repeated on Day 4+ | One-shot `day3_done` flag |
| R wrong (moved stop, last fill only, partials ignored) | `r_multiple = net P&L / (original risk x original size)` |
| VARS / momentum ranked against the stock's own history (and leaked future data) | Cross-sectional rank per date in `data.add_cross_sectional_ranks` |
| Stop fills at the stop even on gaps | `min(open, stop)` |
| Entries taken in dict order | Backtest ranks candidates by setup score, then enters up to the limits |
| Universe: random set order, leveraged ETFs truncated away, biotechs included | Deterministic order, 25% of slots reserved for leveraged ETFs, biotechs excluded |
| ATR extension cap 7x | 4x (Jeff's hard rule); `STALK_MAX_ATR_EXT` for the stalk list only |
| No 3-stop | Three tranches (1/3, 2/3, full stop); full failure = -0.67R |
| No 200-MA rule | Reject price below a falling 200-MA |
| Focus list was an OR of three conditions | AND of the must-haves in RULES.md |
| M30 Re-ORH fired on any close above ORH; RVOL was bar vs session mean | Needs a close below ORH, then back above; volume judged as % of average full-day volume |
| Intraday-reversal check could mix today's candle with yesterday's ORH | Same session used for both; no fallback while the opening range is forming |
| ATR was a simple mean | Wilder smoothing (TradingView default) |

## Desk app (TypeScript)

- Desk tab no longer shows huge negative R for AAPU/CRMG (price 0).
- Journal R/win rate/profit factor use realized P&L over initial risk (partials count).
- 3-stop is modelled: stop levels shown, "Stop hit (1/3)" sells a third, third hit closes.
- "New today" uses New York time instead of UTC.
- Persisted books are migrated (`initialShares`, `stopsHit`).
- MRNA row flagged: ATR/price (4.2%) is below ADR (8.3%), which cannot both be right.

## Still open (not fixed, needs your input)

- **Unverified numbers:** 5 max open, 0.20% risk, +2R partial, the Day-3 cut, `ORH_MIN_DAYVOL_PCT = 0.20`, 40% ATR, the wick ratios. Tune them from your own journal.
- **Not implemented:** earnings/economic-data blackout, gap-resistance check, price-linearity filter, inverse-ETF shorts.
- **Backtest honesty:** hand-picked universe (survivorship bias); daily-close entry using full-day volume is optimistic versus a live 30-minute ORH entry.
- **Desk prices are static** (`NAMES`); equity and R do not move until you add a price feed.
- `daily_focus_list.txt` is output from the old logic until you run `live_scan.py` again.

## Round 2: verification + automation

- **ATR formula verified.** Jeff's TradingView script is closed-source, but its description gives the formula
  (A = ATR / price, B = % gain from the 50-MA, multiple = B / A) and a third-party Pine reproduction matches
  `((close / SMA50) - 1) / (ATR / close)` with Wilder ATR. `ATR_EXT_MODE = "atr_pct"` is correct.
- **Correction:** his 7-10x is where he starts *taking profits*, not an entry limit. Trims are now 7 / 8.5 / 10x
  (were 6 / 8 / 10). The 4x entry cap is the separate hard rule from his hub page.
- **Bug found by the new end-to-end test:** `Trade.__post_init__` reset `remaining_size` to the full size on every
  reload, so sold shares were counted twice (cash and position value) and equity was overstated. Fixed, with a
  round-trip test and accounting invariants checked after every automated run.
- New: `run_daily.py` (scan / intraday / status / dashboard), `paper.py` (state), `dashboard.py` (static HTML),
  `scan_core.py` (headless scan shared with `live_scan.py`), GitHub Actions workflow, cron example,
  fundamentals cache, data sanity flag (ATR% far below ADR%), `README_AUTOMATION.md`.

## Round 3: validation, portfolio risk, fills, alerts

Added (see README_AUTOMATION.md for how each works): rules-based universe with weekly point-in-time snapshots;
drawdown brake and daily loss limit; industry / sector caps with ETF clustering; total open-risk cap; leveraged
exposure cap and half-risk for leveraged ETFs; return-correlation cap; linearity filter; time-of-day volume
profile for the ORH check; volatility- and size-aware slippage; best / worst point (MFE / MAE) per trade;
reject / skip / taken signal log with forward returns and a filter audit; bootstrap evidence panel with a
100-trade target; exit-variant lab (`exit_lab.py`); data sanity checks plus a weekly Stooq cross-check;
Telegram / email alerts with a failed-run notice; `scan_core.FOCUS_BLOCKERS` hook for your own scanner rules.

Deferred at your request: earnings and economic-data blackouts, market-regime gate, gap-resistance filter
(code written and tested, switched off by `USE_GAP_RESISTANCE = False`).
Not built: Alpaca (or any broker) paper-order integration. It needs your API keys and live verification I cannot do from here.

Bugs the new tests caught while building this: ORH skip reasons were not mapped to audit categories;
the worst-point tracker counted the day's low after a stop had already filled (a -1R stop-out showed as -2.5R).

## Round 4: earnings filter, regime gate, Alpaca paper mirror

- **Earnings filter** in the scanner: no Focus name with a report in the next 7 days; cached, fail-open, counted on the dashboard.
- **Market regime gate:** breadth + SPY trend/extension decide risk-on / neutral / stretched (half risk) / risk-off (no new entries). Used live and in the backtest (no look-ahead).
- **Alpaca paper mirror** (`broker.py`): dry run by default, paper endpoint only, mirrors entries, stops and the simulator's exits, reconciles, self-heals missing stops, compares real fills with the fill model. A `broker-check` workflow job verifies your keys without sending anything.
- Dropped from the deferred list: earnings and regime. Still deferred: economic-data blackout, gap-resistance (code present, switched off).

Bugs the new tests caught while building this: an Alpaca authentication failure on the asset lookup was being read as "symbol not tradable" (a wrong key would have looked like a skipped trade instead of an error).
