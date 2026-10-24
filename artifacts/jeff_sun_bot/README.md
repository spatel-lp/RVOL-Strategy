# Jeff Sun (@jfsrev) Style Trading Bot

Educational system that follows Jeff Sun’s publicly shared process.

**Master rules document → `RULES.md`** (keep this as your single reference)

---

## Daily Workflow (matches Jeff’s routine)

```bash
python live_scan.py
```

Output sections:

1. **Stalk List** – broader candidates to monitor  
2. **Focus List** – higher quality names (VCP + structure)  
3. **True ORH Check** – 5-min Opening Range evaluation  
4. **Actionable / Intraday Alerts** – only names that pass full entry rules (ready for execution)

---

## Key Files

| File | Purpose |
|------|---------|
| **RULES.md** | Complete rules reference (Stalk / Focus / Entry / Management) |
| `config.py` | All numeric thresholds |
| `live_scan.py` | Daily routine scanner |
| `orh.py` | True 5-min ORH / M30 Re-ORH module |
| `entry_rules.py` | Full entry checklist |
| `strategy.py` | Position sizing + trade management |
| `indicators.py` | VCP, ATR_ext_50, VARS, RVOL, etc. |
| `data.py` | Universe + leveraged ETFs |
| `backtest.py` | Historical simulation |

---

## Quick Reference

- Max open positions: **5**
- Max new trades per day: **3**
- Risk per trade: **0.15–0.33%** (default 0.20%)
- VCP minimum: **0.55**
- LoD / ATR hard limit: **≤ 0.58**
- Prefer M30 Re-ORH + high RVOL for entry

See **RULES.md** for the full detailed checklist.

---

## Intraday Reversal Scanner (Market Hours)

```bash
python scan_intraday_reversal.py
```

Dedicated scanner for Jeff’s **“Focus $TICKER (Intraday Reversal)”** setups:

- Developing hammer / dragonfly doji
- Entry near ORH
- Hard risk ≤ 40% ATR from LoD

Run during market hours (best in the first 1–2 hours).
