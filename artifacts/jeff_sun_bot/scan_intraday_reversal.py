"""
Intraday Reversal Scanner
=========================
Jeff Sun style – "Focus $TICKER (Intraday Reversal)"

Run this during market hours (especially first 1–2 hours).

Looks for:
- Developing bullish hammer / dragonfly doji (long lower wick, small body)
- Entry area near ORH of the developing print
- Risk ≤ 40% of ATR from session LoD
"""

import pandas as pd
from datetime import datetime
from data import load_market_data, get_universe
from intraday_mr import scan_intraday_mr, evaluate_intraday_mr
from strategy import JeffSunStrategy
from config import *
from entry_rules import evaluate_entry


def get_candidate_universe(max_n: int = 40) -> list:
    """
    Build a practical candidate list for intraday reversal scanning:
    - Stage 2 or recent strength
    - Decent ADR
    - Prefer names that are not extremely extended to the upside
    """
    symbols = get_universe(55)
    print(f"Loading universe ({len(symbols)} names)...")
    data = load_market_data(symbols, show_progress=False)

    candidates = []
    daily_atrs = {}
    strat = JeffSunStrategy()

    for sym, df in data.items():
        if len(df) < 40:
            continue
        row = df.iloc[-1]
        adr = float(row.get("ADR_pct", 0))
        if adr < 2.0:
            continue

        ext = float(row.get("ATR_ext_50", 0))
        # Allow mild upside or any downside stretch
        if ext > 8.0:
            continue

        atr_val = float(row.get("ATR", 0))
        daily_atrs[sym] = atr_val

        score = strat.compute_setup_score(df, len(df) - 1)
        mr = float(row.get("MR_score", 0))
        vcp = float(row.get("VCP_score", 0))

        candidates.append({
            "symbol": sym,
            "score": score,
            "mr": mr,
            "vcp": vcp,
            "ext": ext,
            "adr": adr,
            "atr": atr_val,
            "close": float(row["Close"]),
        })

    # Prefer higher MR + reasonable score
    cand_df = pd.DataFrame(candidates)
    if cand_df.empty:
        return [], {}

    cand_df = cand_df.sort_values(["mr", "score"], ascending=[False, False])
    top = cand_df.head(max_n)
    return top["symbol"].tolist(), daily_atrs


def run_intraday_reversal_scan(top_n: int = 30):
    print("=" * 72)
    print("INTRADAY REVERSAL SCANNER")
    print(f"Jeff Sun style – Focus $TICKER (Intraday Reversal)")
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Hard risk rule: ≤ {IMR_MAX_RISK_ATR:.0%} ATR from LoD")
    print("=" * 72)

    symbols, daily_atrs = get_candidate_universe(max_n=top_n)
    if not symbols:
        print("No candidates found.")
        return pd.DataFrame()

    print(f"\nEvaluating {len(symbols)} candidates on 5-min data...")
    results = scan_intraday_mr(symbols, daily_atrs=daily_atrs, max_risk_atr=IMR_MAX_RISK_ATR)

    if results.empty:
        print("\nNo intraday data returned (market may be closed or data unavailable).")
        return results

    # Separate passed vs near-misses
    passed = results[results["passed"] == True]
    near = results[(results["passed"] == False) & (results["hammer"] == True)]

    print("\n" + "=" * 72)
    print("★ INTRADAY REVERSAL ALERTS  (passed full rules)")
    print("=" * 72)
    if passed.empty:
        print("  None right now.")
    else:
        for _, r in passed.iterrows():
            print(f"  ★ {r['label']}")
            print(f"      Price={r['price']:.2f}  ORH={r['orh']:.2f}  "
                  f"Low={r['session_low']:.2f}  Risk={r['risk_ATR']:.2f} ATR")
            print(f"      {r['notes']}")
            print()

    print("-" * 72)
    print("Near misses (hammer-like but risk > 40% ATR or still weak)")
    print("-" * 72)
    if near.empty:
        print("  None")
    else:
        print(near[["symbol", "price", "risk_ATR", "lower_wick", "notes"]].head(8).to_string(index=False))

    # Save alerts
    alert_file = "intraday_reversal_alerts.txt"
    with open(alert_file, "w") as f:
        f.write(f"Intraday Reversal Scan – {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"Risk rule: ≤ {IMR_MAX_RISK_ATR:.0%} ATR\n\n")
        if passed.empty:
            f.write("No alerts.\n")
        else:
            for _, r in passed.iterrows():
                f.write(f"★ {r['label']}\n")
                f.write(f"   Price={r['price']:.2f}  Risk={r['risk_ATR']:.2f} ATR\n")
                f.write(f"   {r['notes']}\n\n")

    print(f"\nSaved → {alert_file}")
    print(f"Alerts: {len(passed)}")
    return results


if __name__ == "__main__":
    run_intraday_reversal_scan(top_n=30)
