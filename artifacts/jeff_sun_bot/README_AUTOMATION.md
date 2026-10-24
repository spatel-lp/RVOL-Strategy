# Paper-trading automation

A scheduled scan, simulated ORH entries, stop and trade management, portfolio risk controls, and a dashboard,
end to end. Nothing here places real orders.

## What runs when (New York time)

| Job | When | What it does |
|---|---|---|
| `scan` | every trading day after 16:15 (scheduled 16:45) | Refreshes the rules-based universe if it is 7+ days old, runs data-health checks, manages open trades on the final daily bar (partials, Day-3 cut, breakeven, trail, ATR-extension trims), reads the market regime, rebuilds the Stalk / Focus / Mean-reversion lists (dropping names that report earnings within 7 days), logs rejected near-misses, follows older signals forward, stores returns for the correlation cap, marks equity, refreshes the dashboard, sends one digest alert |
| `intraday` | every 30 min, 10:05 to 15:35 (acts only 10:00 to 15:50) | Checks 5-minute lows against every open stop, tests each Focus name for a clean ORH re-claim with time-of-day-adjusted volume, applies the regime gate, risk brakes and portfolio caps, opens paper positions, mirrors them to Alpaca if enabled, refreshes the dashboard |

Both jobs read and write one file, `state/paper_state.json`, and are idempotent. Market holidays are detected from missing bars.

## One trade, start to finish

1. **Evening:** `scan` puts a name on the Focus list: above 10/20-MA, VCP, not beyond 4x ATR% from the 50-MA, ADR, VARS ranked across the universe, and a clean (linear) trend. Near-misses go to the signal log with the rule that rejected them.
2. **Next morning, 10:05+:** `intraday` finds that name closing below the opening-range high and then back above it, with volume at least 1.5x what is typical *by that time of day* (from the name's own last 15 sessions) and the low of day within 0.6 ATR.
3. **Before buying,** the entry must clear the brakes and caps below. It then buys at the current price plus a volatility- and size-aware slippage, puts the full stop 0.2% under the session low, and sizes so a full stop is 1R. Three tranches stop at 1/3, 2/3 and the full stop (full failure about -0.67R).
4. **Same day:** every 30 minutes the 5m lows are checked against each tranche stop; a gap through a stop fills at the bar's open.
5. **Close:** `scan` manages from day 1: +2R partial, Day-3 cut and breakeven, trail from day 4 (10-MA by default), trims at 7 / 8.5 / 10x ATR%. R is measured on the original risk, partials included, and each trade records its best and worst point.

## Risk controls (all in `risk.py`, all shown on the dashboard)

| Control | Default | Effect |
|---|---|---|
| Risk per trade | 0.20% of equity | sized to the full stop |
| Leveraged ETFs | half risk | `LEVERAGED_RISK_MULT` |
| Drawdown brake | -3% from peak: risk x0.5, -6%: x0.25, -10%: halt | the "don't give back two weeks" mechanism |
| Daily loss limit | -0.5% from last night's close | no new trades that day |
| Industry / sector cap | 2 per industry, 3 per sector | leveraged ETFs count with what they track (SOXL counts as semis) |
| Total open risk | 1.0% of equity | sum of (price - stop) x shares across all positions |
| Leveraged exposure | 60% of equity | sum of value x leverage |
| Correlation cap | refuse a name 0.85+ correlated with an open position, or a third at 0.70+ | 60-day daily returns; industries miss names that trade as one bet |
| Slots | 5 open, 3 new per day | |

Every number is in `config.py`, and the thresholds that are mine rather than Jeff's are marked there.

## Earnings filter (in the scanner)

A name whose next earnings report is 0 to 7 days away cannot be on the Focus list (`EARNINGS_WINDOW_DAYS`). Reports that already happened do not block, because post-earnings gaps are one of Jeff's setups. ETFs are skipped. Report timing (before the open or after the close) is not reliably available from Yahoo, hence the plain window. Blocked names are logged as rejects, so the filter audit shows what the rule saves or costs; the Focus table shows each name's next report date; an open position that reports within 7 days raises a warning.

A failed lookup never blocks a name (it fails open) and is retried the next day. The dashboard lists the unknowns, and a banner appears if 5 or more lookups failed, because then the filter is partly blind. Turn the whole thing off with `USE_EARNINGS_FILTER = False`.

## Market regime gate

A top-down read of the tape, applied to every NEW entry (open positions keep their stops and trails):

| Label | When | New-entry risk |
|---|---|---|
| risk-off | under 30% of the universe above its 20-MA, or SPY below both its 20- and 50-day average | none |
| stretched | SPY 6x ATR% or more above its 50-MA, or 5 up-closes in a row | half |
| neutral | under 50% of the universe above its 20-MA | half |
| risk-on | otherwise | full |

The scan reads it at the close and the next morning's entries use it. The backtest applies the same rules per day with no look-ahead, so you can see what the gate costs or saves. The thresholds are my assumptions (`REGIME_*` in `config.py`); Jeff reads breadth and index extension by eye. With fewer than 20 names in the universe it gives no opinion. `USE_REGIME_GATE = False` switches it off.

## Alpaca paper mirror

The simulator makes every decision. The mirror copies them to an Alpaca **paper** account so you get real paper fills to compare against the fill model, and real protective stops at the broker.

| Simulator does | Mirror does at Alpaca |
|---|---|
| ORH entry (intraday job) | market buy, wait for the fill, then one GTC stop order per 3-stop tranche |
| partial, trim, Day-3 cut, trail exit (scan job, after the close) | cancel the stops, market sell the same shares (queued for the next open), put stops back on what is left |
| stop hit | nothing: the broker's own stop fires |
| every run | reconcile positions and account equity; missing stops are re-placed; differences are reported, never auto-traded |

**Safety:** paper only (any other URL is refused, there is no live mode); dry run until you set `ALPACA_SEND_ORDERS=1`; keys only from environment secrets; a broker failure is logged and the simulation carries on; client order ids are deterministic so a retry cannot double an order.

**Setup**

1. In Alpaca, generate **paper** API keys. Never paste them into a chat or commit them to the repo; if you ever do, regenerate them.
2. GitHub, Settings, Secrets and variables, Actions: add secrets `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY`; add a repository **variable** `PAPER_BROKER` = `alpaca`.
3. Run the workflow with job `broker-check` (or locally `python broker.py --check`). It reads your account and sends nothing. Fix anything it complains about.
4. Leave it in dry run for a few days: the dashboard's "Alpaca paper mirror" panel shows the exact orders it would have sent.
5. When they look right, add the variable `ALPACA_SEND_ORDERS` = `1`.

**Know before sending orders**

- It was written from Alpaca's public API documentation and tested against an in-memory fake, not the real service. That is why steps 3 and 4 exist.
- Sizes come from the simulator's equity ($100k). If your Alpaca paper account has a different balance the two drift; the panel warns above 5%.
- Exits decided at the close are market orders queued for the next open, so they fill at the open, not the close the simulator used. That gap is real and shows up in reconciliation as "pending exit" until the open.
- Fractional shares, shorts, bracket orders and extended hours are not used. A symbol Alpaca does not list as tradable is skipped at the broker and flagged.
- If you reset the paper account below $25,000, Alpaca's pattern-day-trader rule may start rejecting orders.

## Does the system have an edge? (the Evidence panel)

- **Evidence:** closed-trade count toward 100, the average R with a bootstrap 95% range, and the probability the true expectancy is above zero. Under 8 trades it refuses to show a range. Do not tune rules before the 100.
- **Excursions:** each trade records its best (MFE) and worst (MAE) point in R; the dashboard shows the share of the best point actually captured.
- **Filter audit:** every near-miss the scanner rejected and every Focus name the ORH check skipped is followed 1/3/5/10 days. The table shows, per reason, whether those names then rose (the filter may be costing winners) or fell (it protected you). No verdict until 30 resolved signals per reason.
- **Exit lab:** `python exit_lab.py` replays the same signals under six exit variants (10-MA / 21-EMA / ATR trail, each with and without Day-3 breakeven) and saves the table to the dashboard.

## Universe

`universe.py` builds it from rules, not hindsight: every US-listed common stock from the Nasdaq Trader directories, filtered by price (8+), 20-day dollar volume (40M+) and ADR% (3.5+), top 150 by dollar volume, plus liquid leveraged ETFs. It is rebuilt weekly and every snapshot is kept in `state/universe/` so a future backtest can ask "what was the universe on date X?". It cannot repair the past: history before your first snapshot stays unknown. If the build fails the job falls back to the last snapshot, then to the old hand-picked list, and says so on the dashboard. Set `PAPER_UNIVERSE_MODE=handpicked` to force the old list.

## Data health

Each scan checks for stale last bars, missing days, one-day moves over 35% on non-leveraged names (unadjusted splits), zero-volume days and invalid OHLC. Weekly it compares a sample of closes with Stooq (no key needed) and flags anything more than 1.5% apart. Findings appear on the dashboard and are alerted.

## Alerts (optional)

Set any of these as GitHub repository secrets (or environment variables locally). Nothing is sent, and nothing breaks, when they are unset.

| Channel | Variables |
|---|---|
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| Email | `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASS`, `ALERT_EMAIL_TO` |

One batched message per job: entries, stops, closes, warnings (data mismatch, entries paused by a brake) and a daily digest. A failed workflow run sends its own alert. Limit the kinds with `NOTIFY_ONLY=ENTRY,STOP,CLOSE,ERROR`. Test with `python notify.py --test`.

## Setup A: GitHub Actions (no server)

1. Create a **private** GitHub repo and push this whole folder (`.github/`, `artifacts/`, `state/`, `docs/`).
2. Settings, Actions, General: Workflow permissions = **Read and write**.
3. Actions tab, "paper-trading", **Run workflow**: job `scan`, tick **force** and **refresh_universe**. The first run is slow (it screens about 7,000 symbols once, then fetches fundamentals, both cached).
4. From then on the schedule takes over and each run commits `state/` and `docs/index.html`.
5. View the dashboard: `git pull` and open `docs/index.html`, or enable GitHub Pages (Settings, Pages, branch `main`, folder `/docs`; private repos need a paid plan).

GitHub's scheduler can start jobs several minutes late or skip one under load. The dashboard's "Last scan" pill turns red after 4 days.

## Setup B: your own machine or a VPS

```bash
pip install -r requirements.txt
python run_daily.py scan --force --refresh-universe     # first run
crontab -e                                              # paste scripts/crontab.example, fix the paths
```

## Commands

```bash
python run_daily.py scan | intraday | status | dashboard
python run_daily.py scan --refresh-universe
python run_daily.py intraday --force --now 2026-10-23T10:35:00-04:00   # fake clock for testing
python exit_lab.py
```

## Your own scanner rules

`scan_core.FOCUS_BLOCKERS` is a list of functions `(symbol, row) -> reason or ""`; the earnings filter is one of them. Append your own and they run best-first on names that already pass every other rule. Blocked names are logged as rejects, so the filter audit measures what each rule costs or saves.

## What this does not do (read before trusting the P&L)

- **Not built:** economic-data blackouts (CPI / jobs / FOMC), and the gap-resistance filter (`filters.gap_resistance` exists and is tested, but `USE_GAP_RESISTANCE = False`). The Alpaca mirror is paper only and has never run against the real service.
- **Data is Yahoo Finance via yfinance** (unofficial): intraday bars can lag or gap. The data-health checks exist because of this.
- **Fills are simulated:** slippage = 2% of ATR% plus 0.5 x your share of average daily volume (floor 0.03%, cap 1.5%), plus 0.05% commission. 5-minute bars hide what happens inside a bar.
- **Daily-bar approximations:** stops 1 and 2 of the 3-stop are price levels, not Jeff's time-based "no confirmation in 1-2 hours" rule.
- **Assumptions that are mine, not Jeff's:** the regime thresholds, the 7-day earnings window, 1.5x time-adjusted volume, no entries after 15:00 ET, the 7 / 8.5 / 10x trim split, every risk-control threshold above, linearity R2 of 0.60 over 40 days. Tune them from your own journal, after 100 trades.
- **The universe snapshots start today.** Backtests on the hand-picked list stay survivorship-biased.

## Tests (no network needed)

```bash
python tests/test_fixes.py         # 12 rule and bookkeeping regression tests
python tests/test_round3.py        # 23 tests: caps, brakes, correlation, fills, exits, signals, universe, data checks, alerts, volume profile, evidence
python tests/test_round4.py        # 10 tests: earnings window / fail-open / cache, regime thresholds / breadth / no look-ahead / backtest gate
python tests/test_broker.py        # 13 tests: Alpaca mirror against an in-memory fake that enforces Alpaca's order rules
python tests/test_automation.py    # 11 end-to-end scenarios on a synthetic market (brakes, caps, earnings, regime, Alpaca), dashboard JS run in node
python tests/smoke_backtest.py     # offline backtest
python tests/smoke_exit_lab.py     # offline exit-variant comparison
python tests/build_demo.py         # regenerates demo/dashboard_demo.html (synthetic data, bannered as DEMO)
```
