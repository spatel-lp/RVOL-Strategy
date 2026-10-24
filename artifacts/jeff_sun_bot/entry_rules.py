"""
Jeff Sun (@jfsrev) Entry Criteria – Explicit Rules

Core idea:
  Enter only when price action + volume confirm strength,
  with tightly defined risk (LoD / ATR), preferably on
  reclaim or break of the Opening Range High (ORH).

Key terms Jeff uses:
  - ORH  = Opening Range High (high of the first 5–30 min)
  - M30 Re-ORH = reclaim of ORH after the first 30 minutes
  - LoD  = Low of Day (at the time of decision)
  - RVOL = Relative Volume vs recent average
  - ATR  = Average True Range
"""

from dataclasses import dataclass
from typing import Optional
import pandas as pd


@dataclass
class EntryChecklist:
    """Result of evaluating Jeff-style entry conditions"""
    passed: bool
    score: float
    reasons: list
    stop_price: Optional[float] = None
    risk_atr_multiple: Optional[float] = None
    notes: str = ""


def evaluate_entry(
    df: pd.DataFrame,
    i: int,
    *,
    min_rvol: float = 1.6,
    max_lod_atr: float = 0.58,
    min_vcp: float = 0.55,
    min_adr: float = 3.5,
    require_stage2: bool = True,
    prefer_orh_proxy: bool = True,
) -> EntryChecklist:
    """
    Evaluate a potential entry bar against Jeff Sun style rules.

    Because we mostly work with daily bars, ORH is approximated:
      - Daily Open as proxy for opening range reference
      - Or previous day High as resistance that needs reclaim
    For true ORH you need intraday (5m/15m) data – see live_scan enhancements.
    """
    reasons = []
    score = 0.0
    row = df.iloc[i]
    prev = df.iloc[i-1] if i > 0 else row

    close = float(row["Close"])
    open_ = float(row["Open"])
    high = float(row["High"])
    low = float(row["Low"])
    atr = float(row.get("ATR", 0))
    rvol = float(row.get("RVOL", 0))
    vcp = float(row.get("VCP_score", 0))
    adr = float(row.get("ADR_pct", 0))
    sma10 = float(row.get("SMA10", 0))
    sma20 = float(row.get("SMA20", 0))
    stage2 = bool(row.get("Stage2", False))

    # ---------- 1. Stage 2 + MAs ----------
    if require_stage2 and not stage2:
        return EntryChecklist(False, 0.0, ["Failed Stage 2"])
    if close < sma10 or close < sma20:
        return EntryChecklist(False, 0.0, ["Below 10/20-MA"])
    reasons.append("Above 10/20-MA + Stage 2")
    score += 0.15

    # ---------- 2. ADR filter ----------
    if adr < min_adr:
        return EntryChecklist(False, 0.0, [f"ADR% {adr:.1f} < {min_adr}"])
    reasons.append(f"ADR% {adr:.1f}")
    score += 0.10

    # ---------- 3. VCP / contraction ----------
    if vcp < min_vcp:
        return EntryChecklist(False, 0.0, [f"VCP score {vcp:.2f} < {min_vcp}"])
    reasons.append(f"VCP {vcp:.2f}")
    score += 0.20

    # ---------- 4. RVOL confirmation ----------
    if rvol < min_rvol:
        return EntryChecklist(False, 0.0, [f"RVOL {rvol:.2f} < {min_rvol}"])
    reasons.append(f"RVOL {rvol:.2f}")
    score += 0.20

    # ---------- 5. Opening Range / ORH proxy ----------
    # Daily approximation:
    #   - Prefer closes that reclaim the Open (bullish day structure)
    #   - Or break above prior day high (range expansion)
    orh_proxy_ok = False
    if prefer_orh_proxy:
        # Reclaim of today's open
        if close > open_ and (close - open_) / atr > 0.15:
            orh_proxy_ok = True
            reasons.append("Reclaimed Open (ORH proxy)")
            score += 0.12
        # Break of prior high
        elif high > prev["High"] and close > prev["High"]:
            orh_proxy_ok = True
            reasons.append("Broke prior day High")
            score += 0.12
        else:
            # No daily ORH proxy: penalised AND rejected below (passed requires orh_proxy_ok)
            reasons.append("No clean ORH/Open reclaim (penalty)")
            score -= 0.05
    else:
        orh_proxy_ok = True

    # ---------- 6. LoD / ATR risk rule (hard) ----------
    # Stop is placed near the session low or recent structure low
    stop = min(low, df["Low"].iloc[max(0, i-2):i+1].min())
    # Add small buffer
    stop = stop * 0.998

    if atr <= 0:
        return EntryChecklist(False, 0.0, ["Invalid ATR"])

    risk_distance = close - stop
    lod_atr_mult = risk_distance / atr

    if lod_atr_mult > max_lod_atr:
        return EntryChecklist(
            False, score,
            reasons + [f"LoD/ATR {lod_atr_mult:.2f} > {max_lod_atr} (hard rule fail)"]
        )
    reasons.append(f"LoD/ATR {lod_atr_mult:.2f} ≤ {max_lod_atr}")
    score += 0.18

    # ---------- 7. Extra quality ----------
    if rvol >= 2.5:
        score += 0.05
        reasons.append("Very high RVOL")
    if vcp >= 0.70:
        score += 0.05
        reasons.append("Strong VCP")

    score = max(0.0, min(1.0, score))
    passed = score >= 0.55 and orh_proxy_ok

    return EntryChecklist(
        passed=passed,
        score=score,
        reasons=reasons,
        stop_price=stop,
        risk_atr_multiple=lod_atr_mult,
        notes=" | ".join(reasons)
    )


def print_entry_rules():
    print("""
JEFF SUN STYLE – ENTRY CHECKLIST
================================

1. Structure
   • Stage 2 uptrend
   • Price above 10-MA and 20-MA

2. Volatility
   • ADR% preferably ≥ 3.5–4%

3. Contraction (VCP)
   • Recent range + volume contraction (VCP score ≥ ~0.55)

4. Volume Confirmation
   • RVOL ≥ 1.6 (ideally much higher on the breakout bar)

5. Opening Range / ORH (preferred)
   • Ideal: reclaim of Opening Range High after first 30 min (M30 Re-ORH)
   • Daily proxy: close above Open + expansion, or break of prior day high
   • Avoid low-RVOL grind through resistance

6. Risk Definition – HARD RULE
   • Stop near LoD / recent structure low
   • Distance from entry to stop ≤ ~0.55–0.60 × ATR
   • Jeff: “I do not enter if LoD already exceeds 60% ATR”

7. Position Sizing
   • Risk only 0.15–0.33% of equity
   • Shares = (Equity × Risk%) / (Entry – Stop)

Execution preference
   • High RVOL + clean ORH reclaim + tight LoD risk
   • Skip if the move is already extended on low volume
""")
