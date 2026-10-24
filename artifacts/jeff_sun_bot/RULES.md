# Jeff Sun Style Bot – Complete Rules Reference
**Single source of truth. Keep this file updated.**

Last updated: 2026-10-18

---

## 1. Overall Process (Jeff’s Daily Routine)

```
Post-market Screening
        ↓
Broad Watchlist / Stalk List   ← many names, not yet ready
        ↓
Focus List                     ← only names showing VCP + structure
        ↓
Intraday ORH / M30 Re-ORH      ← high RVOL + tight LoD → ENTRY
        ↓
Trade Management               ← partials, BE, 10-MA trail
```

### Position Limits
- Max **5** open positions at any time
- Max **3** new trades per day

### Risk
- **0.15% – 0.33%** of equity per trade (default 0.20%)
- Position size = (Equity × Risk%) / (Entry – Stop)
- Max single position ≈ 28% of equity

---

## 2. Stalk List (Broader Candidates)

Names that are interesting but **not yet ready** for entry.

**Criteria (any of these):**
- Stage 2 uptrend
- Above 20-MA
- Decent relative strength / VARS
- ADR% ≥ ~2.8%
- Score ≥ 0.40

**Purpose:** Monitor daily for tightening, volume dry-up, or emerging VCP.

---

## 3. Focus List (Higher Quality)

Only names that are getting close to actionable.

**Must have:**
- Stage 2 + price above 10-MA **and** 20-MA
- VCP score ≥ **0.55** (prefer ≥ 0.60)
- ADR% ≥ 3.5–4%
- Not extended: ATR_ext_50 ≤ **4×** (hard cap; Jeff focuses nothing beyond 4× ATR% from the 50-MA)
- Decent VARS / RS
- Score ≥ 0.55 **or** passes most daily filters

**Purpose:** Prepare levels and watch closely for ORH setup.

---

## 4. Entry / Actionable Rules (Intraday)

A name only becomes **Actionable** when **ALL** of the following are true:

### A. Structure
- Stage 2
- Price above 10-MA and 20-MA

### B. Contraction (VCP)
- Clear volatility contraction (VCP score ≥ 0.55)
- Jeff: “I will NEVER enter a stock with prior loose price action”

### C. Volume
- **RVOL ≥ 1.6** (ideally much higher on the breakout/reclaim bar)

### D. Opening Range (preferred trigger)
- Ideal: **M30 Re-ORH** (reclaim of Opening Range High after first 30 min)
- Or clean break/reclaim of ORH with expanding volume
- Avoid low-RVOL grinds

### E. Risk Definition – HARD RULE
- Stop near **LoD** (Low of Day) or recent structure low
- Distance from entry to stop ≤ **0.55–0.60 × ATR**
- Jeff: “I do not enter if LoD already exceeds 60% ATR”

### F. Extension Filter
- Prefer ATR_ext_50 roughly in 0.5× – 3.5× range
- Hard reject if already > **4×** above the 50-MA (do not chase)
- Hard reject if price is below a **falling 200-MA** (my reading of "never against a declining 200-MA")
- Biotechs are excluded entirely (gap risk); use ETFs such as LABU/LABD instead

### G. Other
- Prefer higher ADR% names
- Leveraged ETFs allowed/preferred when they offer cleaner higher-ADR exposure
- Market cap preferably under ~$45B (except strong leveraged names)

---

## 5. Trade Management Rules

1. **Entry Day (Day 0) – 3-stop**  
   - Size to the FULL stop (= 1R), then defend with three staged stops that each sell a third  
   - Levels (`THREE_STOP_LEVELS`): 1/3, 2/3 and all the way to the full stop → a full failure costs **−0.67R**, not −1R  
   - Daily-bar approximation: intraday timing of stops 1 and 2 is not modelled
   - A gap below a stop fills at the open (can be worse than −1R)

2. **Days 0–2** (all "days" are TRADING days / bars, weekends and holidays excluded)  
   - If trade reaches **+2R**, take ~33% partial profit

3. **Day 3** (fires once)  
   - Reduce size further (~33%)  
   - Move remaining stops to **breakeven**

4. **Day 4 onward**  
   - Trail / exit on close below the **10-day MA**  
   - Do not tinker while price stays above 10-MA

5. **ATR Extension Trims**  
   - 25% partials when price first reaches 7×, 8.5×, 10× ATR% above the 50-MA (each level fires once). Jeff: "start taking profits at 7–10× ATR% from the 50-SMA"; the split is mine.

**R is always measured on the ORIGINAL entry-minus-stop risk and the whole original position, using net P&L (partials included).** A trade that banks a partial and is later stopped at breakeven is a winner.

---

## 6. Key Metrics Explained

| Metric       | Meaning                                      | Typical Threshold |
|--------------|----------------------------------------------|-------------------|
| VCP_score    | Multi-factor contraction quality (0–1)       | ≥ 0.55 (prefer 0.60+) |
| RVOL         | Relative volume vs 50-day average            | ≥ 1.6 |
| ATR_ext_50   | ATR% multiple above the 50-MA (see `ATR_EXT_MODE`) | Hard cap 4×, ideal 0.5–3.5× |
| VARS         | Volatility-Adjusted Relative Strength       | ≥ 65–70 |
| LoD / ATR    | Risk distance in ATR terms                   | ≤ 0.58–0.60 |
| ORH          | Opening Range High (first 30 min)            | Reclaim preferred |
| M30 Re-ORH   | Reclaim of ORH after first 30 minutes        | Preferred entry trigger |

---

## 7. Daily Workflow (How to Use the Bot)

### After market close
```bash
python live_scan.py
```
→ Produces:
- Stalk List
- Focus List
- Notes on which names are close

### Next morning / during market hours
- Watch the **Focus List** names
- Look for clean **ORH / M30 Re-ORH** with high RVOL and tight LoD
- Only take the trade when the full Entry rules above are satisfied

### Position limits while trading
- Never more than 5 open positions
- Never more than 3 new entries on the same day

---

## 8. Config File Reference (`config.py`)

All numeric thresholds live in `config.py`.  
Change them there — do not hard-code elsewhere.

Key settings:
- `RISK_PER_TRADE = 0.0020` (0.20%)
- `MAX_OPEN_POSITIONS = 5`
- `MAX_DAILY_NEW_TRADES = 3`
- `VCP_MIN_SCORE = 0.55`
- `MIN_RVOL = 1.6`
- `LOD_ATR_MAX = 0.58`
- `MAX_ATR_EXT_50 = 4.0`
- `USE_THREE_STOP = True`, `THREE_STOP_LEVELS = (0.33, 0.67, 1.0)`
- `ATR_EXT_MODE = "atr_pct"` (verified against Jeff's description: ((close/SMA50) − 1) / (ATR/close), Wilder ATR)

---

## 9. Philosophy Reminders (from Jeff)

- High selectivity beats high activity
- Process over prediction
- Tight risk first, then let winners run under the 10-MA rule
- Never force an entry when VCP or RVOL is missing
- Journal everything and refine year-over-year

---

**This file is the master reference.**  
Update it whenever rules change.

---

## 10. Mean Reversion / Pullback Module (Secondary Strategy)

Jeff runs a separate mean-reversion process alongside the main momentum/VCP approach.

### What it looks for
- Names **stretched to the downside** relative to the 50-MA
  - Typically **–1.5× to –4.5× ATR** below the 50-MA
- Decline that is **slowing** (rate of change improving)
- Early signs of stabilization: higher lows, tight range, doji/hammer type action
- Still has decent ADR% (so the bounce has room)

### How it is used
- Appears as a separate **Mean Reversion / Pullback List** in the post-close scan
- These are secondary ideas — not the primary Focus List
- Entry can still use **ORH**, sometimes with slightly lower RVOL requirements (especially on larger names)
- Goal: asymmetrical reward-to-risk on the reclaim/bounce

### Scoring
- `MR_score` (0–1) combines:
  - Degree of downside stretch (ATR_ext_50)
  - Slowing rate of change
  - Higher-low / short-term structure attempts
  - Tightness after the decline

### Config
- `MR_MIN_SCORE = 0.45`
- `MR_MIN_STRETCH = -1.5`
- `MR_MAX_STRETCH = -4.5`

This is a **secondary** watchlist. Primary process remains Stalk → Focus → ORH Actionable.

---

## 11. Intraday Mean Reversion (Live Session Module)

Source: Jeff’s post https://x.com/jfsrev/status/2067504357764206847

### Concept
Enter **during the session** ahead of a potential end-of-day long lower-wick reversal (bullish hammer / dragonfly doji), instead of waiting for full EOD confirmation.

### Hard Rules
- Developing candle shows **long lower wick** (≥ ~55% of the range) and **small body** (≤ ~35%)
- Preferred entry: **ORH** of that developing print
- Stop at session LoD
- Risk distance **≤ 40% of ATR** (tighter than the normal 60% rule)
- Label when it qualifies: **“Focus $TICKER (Intraday Reversal)”**

### Advantages (per Jeff)
- Better entry price than waiting for full confirmation
- Extra day for the trade to develop (helps T+3 / 10-MA management)
- Clear technical invalidation level

### Trade-off
- Reversal is not yet confirmed — the candle can still fail later in the day

### When to use
- During market hours, especially first 1–2 hours
- Best applied on names already on the Mean Reversion / Focus lists
- Requires exceptionally tight risk definition

### Config
- `IMR_MAX_RISK_ATR = 0.40`
- `IMR_MIN_LOWER_WICK = 0.55`
- `IMR_MAX_BODY = 0.35`

### How to run the dedicated scanner
```bash
python scan_intraday_reversal.py
```
Best used during live market hours. Outputs clear “Focus $TICKER (Intraday Reversal)” alerts when rules are met.


## Portfolio rules added in round 3

- Risk per trade 0.20% of equity (half for leveraged ETFs). Brakes: -3% from peak = half risk, -6% = quarter, -10% = no new trades; -0.5% on the day = no new trades.
- Caps: 2 per industry, 3 per sector (leveraged ETFs count with what they track), total open risk 1.0% of equity, leveraged exposure 60% of equity.
- Correlation: no name 0.85+ correlated (60-day returns) with an open position; no third name at 0.70+.
- Focus names also need a straight-line trend (R2 of log price over 40 days at least 0.60). Gap-resistance filter: written, switched off.
- ORH volume is judged against what is typical by that time of day (1.5x), falling back to 20% of an average day when the name has too little intraday history.

## Round 4 rules

- Scanner: no Focus name with earnings in the next 7 calendar days (post-earnings names are fine).
- Regime: risk-off (under 30% of names above their 20-MA, or SPY below both averages) = no new entries; neutral (under 50%) or stretched (SPY 6x ATR% over its 50-MA, or 5 up-closes in a row) = half risk; otherwise full.
- Alpaca mirror: paper only; every action the simulator takes is copied; the simulator is the source of truth.
