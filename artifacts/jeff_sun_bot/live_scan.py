"""
Jeff Sun Daily Routine Scanner
Outputs: Stalk List → Focus List → Actionable (ORH ready)
"""

import pandas as pd
from datetime import datetime
from typing import List
from data import load_market_data, get_universe
from strategy import JeffSunStrategy
from entry_rules import evaluate_entry, print_entry_rules
from scan_core import compute_rows, select_lists
from orh import scan_orh_for_list
from intraday_mr import scan_intraday_mr
from config import *


def scan_universe(symbols: List[str] = None, top_n: int = 20, run_orh: bool = True):
    if symbols is None:
        symbols = get_universe(60)

    print("=" * 72)
    print("JEFF SUN STYLE – DAILY ROUTINE SCAN")
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Limits: {MAX_OPEN_POSITIONS} max open | {MAX_DAILY_NEW_TRADES} max new/day")
    print("=" * 72)

    market_data = load_market_data(symbols, show_progress=True)
    if not market_data:
        return

    rows, daily_atrs, avg_vols = compute_rows(market_data)

    if not rows:
        print("No names found.")
        return

    df = pd.DataFrame(rows).sort_values("score", ascending=False)

    # ---------- STALK LIST ----------
    stalk, focus, mr_list = select_lists(df)
    print("\n" + "=" * 72)
    print("1. STALK LIST  (broader candidates – monitor for tightening)")
    print("=" * 72)
    if stalk.empty:
        print("  Empty")
    else:
        print(stalk[["symbol", "score", "close", "ADR%", "RVOL", "VCP", "ATR_ext", "VARS", "LEV"]].to_string(index=False))

    # ---------- FOCUS LIST ----------
    print("\n" + "=" * 72)
    print("2. FOCUS LIST  (higher quality – prepare levels, watch for ORH)")
    print("=" * 72)
    if focus.empty:
        print("  Empty")
    else:
        print(focus[["symbol", "score", "close", "ADR%", "RVOL", "VCP", "ATR_ext", "VARS", "daily_ok", "LEV"]].to_string(index=False))

    # ---------- MEAN REVERSION LIST ----------
    print("\n" + "=" * 72)
    print("2b. MEAN REVERSION / PULLBACK LIST  (stretched downside candidates)")
    print("=" * 72)
    if mr_list.empty:
        print("  Empty")
    else:
        print(mr_list[["symbol", "MR", "close", "ADR%", "ATR_ext", "VCP", "RVOL", "LEV"]].to_string(index=False))

    # ---------- INTRADAY MEAN REVERSION (live-style check on last session) ----------
    # Uses the same Focus + MR names; during market hours this is the real-time alert layer
    imr_candidates = []
    if not focus.empty:
        imr_candidates.extend(focus["symbol"].tolist())
    if not mr_list.empty:
        imr_candidates.extend(mr_list["symbol"].tolist())
    imr_candidates = list(dict.fromkeys(imr_candidates))  # unique, preserve order

    if imr_candidates and run_orh:
        imr_df = scan_intraday_mr(imr_candidates, daily_atrs=daily_atrs, max_risk_atr=0.40)
        print("\n" + "=" * 72)
        print("2c. INTRADAY MEAN REVERSION CHECK  (hammer/doji + ≤40% ATR risk)")
        print("=" * 72)
        if imr_df.empty:
            print("  No data / no candidates")
        else:
            print(imr_df.to_string(index=False))
            passed_imr = imr_df[imr_df["passed"] == True]
            if not passed_imr.empty:
                print("\n  ★ INTRADAY REVERSAL ALERTS:")
                for _, r in passed_imr.iterrows():
                    print(f"     {r['label']}  risk={r['risk_ATR']:.2f} ATR")

    # ---------- ORH EVALUATION ----------
    orh_df = pd.DataFrame()
    if run_orh and not focus.empty:
        focus_syms = focus["symbol"].tolist()
        orh_df = scan_orh_for_list(focus_syms, daily_atrs=daily_atrs, avg_volumes=avg_vols)
        if not orh_df.empty:
            print("\n" + "=" * 72)
            print("3. TRUE ORH CHECK (5-min data on Focus List)")
            print("=" * 72)
            print(orh_df.to_string(index=False))
            focus = focus.merge(orh_df[["symbol", "orh_passed", "m30_re", "dayvol%", "notes"]], on="symbol", how="left")

    # ---------- ACTIONABLE ----------
    if "orh_passed" in focus.columns:
        actionable = focus[(focus["daily_ok"] == True) & (focus["orh_passed"] == True)]
    else:
        actionable = focus[focus["daily_ok"] == True]

    print("\n" + "=" * 72)
    print("4. ACTIONABLE / INTRADAY ALERTS  (full rules + ORH → ready for entry)")
    print("=" * 72)
    if actionable.empty:
        print("  None – no names currently satisfy full entry rules.")
        print("  Wait for clean M30 Re-ORH + high RVOL on Focus List names.")
    else:
        for _, r in actionable.iterrows():
            print(f"  ★ {r['symbol']:6s}  Score={r['score']:.3f}  VCP={r['VCP']:.2f}  "
                  f"ATR_ext={r['ATR_ext']:.1f}x  RVOL={r['RVOL']:.1f}")
            if "notes" in r and pd.notna(r.get("notes")):
                print(f"      {r['notes']}")

    # Save clean summary
    with open(LIVE_ALERT_FILE, "w") as f:
        f.write(f"Jeff Sun Daily Routine – {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"Max open: {MAX_OPEN_POSITIONS} | Max new/day: {MAX_DAILY_NEW_TRADES}\n\n")

        f.write("STALK LIST\n")
        f.write("-" * 40 + "\n")
        for _, r in stalk.iterrows():
            f.write(f"  {r['symbol']:6s}  {r['score']:.3f}  VCP={r['VCP']:.2f}  ext={r['ATR_ext']:.1f}x\n")

        f.write("\nFOCUS LIST\n")
        f.write("-" * 40 + "\n")
        for _, r in focus.iterrows():
            f.write(f"  {r['symbol']:6s}  {r['score']:.3f}  VCP={r['VCP']:.2f}  daily_ok={r['daily_ok']}\n")

        f.write("\nACTIONABLE (ORH ready)\n")
        f.write("-" * 40 + "\n")
        if actionable.empty:
            f.write("  None today\n")
        else:
            for _, r in actionable.iterrows():
                f.write(f"  ★ {r['symbol']:6s}  Score={r['score']:.3f}\n")

    print(f"\nSaved → {LIVE_ALERT_FILE}")
    print("\nFull rules: see RULES.md")
    return focus


if __name__ == "__main__":
    scan_universe(top_n=15, run_orh=True)
