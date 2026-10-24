"""
Jeff Sun style strategy – full enhanced version
Includes: ATR extension filter, VARS, leveraged preference, ORH+RVOL+LoD
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Set
from enum import Enum
import pandas as pd
import numpy as np

from config import *
from data import fundamental_growth_proxy
from entry_rules import evaluate_entry
from filters import entry_filter_failures
from risk import (
    check_caps, check_correlation, daily_loss_halt, drawdown_state, leverage_of, risk_mult_for_symbol,
)


class TradeStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass
class Tranche:
    shares: int
    stop: float


@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    stop_price: float                  # full (final) stop, = 1R below entry
    size: float
    risk_amount: float
    equity_at_entry: float
    entry_idx: int = 0                 # bar index of entry in the symbol's frame
    initial_risk_per_share: float = 0.0  # entry - original stop; NEVER changes (R is measured on it)
    status: TradeStatus = TradeStatus.OPEN
    exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    pnl: float = 0.0
    r_multiple: float = 0.0
    partials_taken: int = 0
    remaining_size: float = -1.0       # -1 = "not set yet": starts equal to size, then survives save/load
    day_count: int = 0                 # TRADING days since entry (bars), not calendar days
    notes: List[str] = field(default_factory=list)
    realized_pnl: float = 0.0          # net of commission + slippage
    is_leveraged: bool = False
    tranches: List[Tranche] = field(default_factory=list)
    two_r_done: bool = False
    day3_done: bool = False
    ext_levels_hit: Set[float] = field(default_factory=set)
    # live / paper-trading bookkeeping
    entry_time: str = ""               # label of the 5m bar the paper entry was made on (ET, ISO)
    last_check: str = ""               # last 5m bar label whose lows were checked against the stops
    last_price: float = 0.0            # most recent price seen (for mark-to-market)
    source: str = "backtest"           # "backtest" | "paper-orh"
    # round 3: portfolio clusters, excursions, fills
    sector: str = ""
    industry: str = ""
    leverage: float = 1.0              # effective leverage of the instrument (1 = plain stock)
    mfe_r: float = 0.0                 # max favourable excursion, in R of the ORIGINAL risk
    mae_r: float = 0.0                 # max adverse excursion (<= 0), in R
    high_water: float = 0.0            # highest close since entry (ATR-trail variant)
    slip_pct: float = 0.0              # slippage modelled for this trade (0 = config default)

    def __post_init__(self):
        if self.remaining_size < 0:
            self.remaining_size = self.size
        if self.initial_risk_per_share <= 0:
            self.initial_risk_per_share = self.entry_price - self.stop_price


class JeffSunStrategy:
    def __init__(self, equity: float = INITIAL_EQUITY, risk_pct: float = None,
                 exit_variant: Optional[str] = None, day3_be: Optional[bool] = None):
        self.exit_variant = exit_variant or EXIT_VARIANT
        self.day3_be = DAY3_BREAKEVEN if day3_be is None else day3_be
        self.peak_equity = equity
        self.day_start_equity = equity
        self.last_block = ""               # why the most recent entry attempt was refused
        # symbol -> pd.Series of daily returns up to "now" (or None). The backtest and the paper runner each plug in
        # their own source; without one the correlation cap simply does not run.
        self.returns_provider = None
        # Market-regime gate (regime.py): multiplier on NEW-entry risk, 0 = no new entries. Set by the caller each day.
        self.regime_mult = 1.0
        self.regime_label = ""
        self.regime_why = ""
        self.equity = equity
        self.cash = equity
        self.risk_pct = risk_pct or RISK_PER_TRADE
        self.positions: Dict[str, Trade] = {}
        self.closed_trades: List[Trade] = []
        self.equity_curve = []
        self.daily_new_trades = 0
        self.current_date = None

    # ------------------------------------------------------------------ sizing
    def risk_state(self) -> dict:
        """Drawdown brake + daily loss limit. mult scales every new position's risk; halted blocks entries."""
        mult, dd, reason = drawdown_state(self.equity, self.peak_equity)
        halted_day, day_loss = daily_loss_halt(self.equity, self.day_start_equity)
        reasons = [r for r in (reason, f"daily loss {day_loss * 100:.1f}% >= limit: no new trades" if halted_day else "") if r]
        return {"mult": 0.0 if halted_day else mult, "dd": dd, "day_loss": day_loss,
                "halted": halted_day or mult == 0.0, "reasons": reasons}

    @staticmethod
    def _slip(shares: float, price: float, atr: float, avg_vol: float) -> float:
        """Slippage grows with volatility and with the share of average daily volume we trade."""
        atr_pct = (atr / price) if (price and atr and np.isfinite(atr)) else 0.0
        part = (shares / avg_vol) if (avg_vol and np.isfinite(avg_vol) and avg_vol > 0) else 0.0
        slip = SLIP_ATR_FRAC * atr_pct + SLIP_IMPACT * part
        return float(min(max(slip, SLIP_FLOOR), SLIP_CAP)) if (atr_pct or part) else SLIPPAGE_PCT

    def calculate_position_size(self, entry: float, stop: float, risk_mult: float = 1.0) -> tuple[float, float]:
        if entry <= stop or entry <= 0:
            return 0.0, 0.0
        risk_per_share = entry - stop
        risk_dollars = self.equity * self.risk_pct * risk_mult
        shares = risk_dollars / risk_per_share
        max_shares_by_pct = (self.equity * MAX_POSITION_PCT) / entry
        shares = min(shares, max_shares_by_pct)
        max_shares_by_cash = self.cash / (entry * (1 + COMMISSION_PCT + SLIPPAGE_PCT))
        shares = min(shares, max_shares_by_cash)
        shares = max(0, int(shares))
        return shares, shares * risk_per_share

    def compute_setup_score(self, df: pd.DataFrame, i: int) -> float:
        """Enhanced score with VARS + ATR extension + leveraged bonus"""
        row = df.iloc[i]
        score = 0.0

        # VCP
        score += 0.22 * float(row.get("VCP_score", 0))

        # Momentum
        score += 0.12 * float(row.get("Mom_score", 0.5))

        # VARS (preferred) or classic RS
        vars_val = row.get("VARS", None)
        if vars_val is not None and not pd.isna(vars_val):
            vars_norm = min(max((vars_val - 50) / 50, 0), 1)
            score += 0.18 * vars_norm
        else:
            rs = row.get("RS", 100)
            score += 0.15 * min(max((rs - 70) / 50, 0), 1)

        # ADR
        adr = float(row.get("ADR_pct", 3))
        score += 0.10 * min(max((adr - 2.5) / 6.0, 0), 1)

        # RVOL
        rvol = float(row.get("RVOL", 1))
        score += 0.12 * min(max((rvol - 1.0) / 3.0, 0), 1)

        # ATR extension - reward constructive extension, heavy penalty past the hard cap
        ext = float(row.get("ATR_ext_50", 3))
        if ext < 0:
            score += 0.0
        elif ext <= MAX_ATR_EXT_50:
            score += 0.12 * (ext / MAX_ATR_EXT_50)
        else:
            score -= 0.15

        # Fundamental proxy
        funds = df.attrs.get("fundamentals", {})
        score += 0.10 * fundamental_growth_proxy(funds)

        # Leveraged ETF small bonus (Jeff prefers higher ADR vehicles)
        if df.attrs.get("is_leveraged"):
            score += 0.04

        return float(np.clip(score, 0, 1))

    # ------------------------------------------------------------------ entries
    def evaluate_candidate(self, symbol: str, df: pd.DataFrame, i: int) -> Optional[dict]:
        """Side-effect free: returns a candidate dict if every hard rule passes, else None."""
        row = df.iloc[i]
        funds = df.attrs.get("fundamentals", {})

        # Biotechs are excluded (gap risk)
        if symbol in EXCLUDED_SYMBOLS:
            return None
        industry = (funds.get("industry") or "").lower()
        if any(k in industry for k in EXCLUDED_INDUSTRY_KEYWORDS):
            return None

        # Hard extension filter (do not chase): no entry beyond 4x ATR% from the 50-MA
        ext = float(row.get("ATR_ext_50", 0))
        if not np.isfinite(ext) or ext > MAX_ATR_EXT_50:
            return None

        # No trade against a declining 200-MA (read as: price below a falling 200-MA)
        sma200 = row.get("SMA200")
        if sma200 is not None and not pd.isna(sma200) and i >= SMA200_FALL_LOOKBACK:
            prior = df["SMA200"].iloc[i - SMA200_FALL_LOOKBACK]
            if not pd.isna(prior) and sma200 < prior and float(row["Close"]) < float(sma200):
                return None

        checklist = evaluate_entry(
            df, i,
            min_rvol=MIN_RVOL,
            max_lod_atr=LOD_ATR_MAX,
            min_vcp=VCP_MIN_SCORE,
            min_adr=MIN_ADR_PCT,
            prefer_orh_proxy=True,
        )
        if not checklist.passed:
            return None

        vars_val = row.get("VARS")
        if vars_val is not None and not pd.isna(vars_val) and vars_val < MIN_VARS:
            return None

        mcap = funds.get("marketCap")
        if mcap and mcap > MAX_MARKET_CAP and not df.attrs.get("is_leveraged"):
            return None

        if entry_filter_failures(df, i):          # choppy trend (and gap resistance once enabled)
            return None

        return {
            "symbol": symbol,
            "df": df,
            "i": i,
            "ext": ext,
            "checklist": checklist,
            "rank_score": self.compute_setup_score(df, i),
        }

    def _open(self, symbol: str, entry_price: float, stop_price: float, *, sector: str, industry: str,
              is_leveraged: bool, atr: float, avg_vol: float, **trade_kwargs) -> Optional[Trade]:
        """Shared by backtest and paper entries: brakes, sizing, portfolio caps, fill model."""
        self.last_block = ""
        if len(self.positions) >= MAX_OPEN_POSITIONS:
            self.last_block = f"max {MAX_OPEN_POSITIONS} open positions"
            return None
        if self.daily_new_trades >= MAX_DAILY_NEW_TRADES:
            self.last_block = f"max {MAX_DAILY_NEW_TRADES} new trades per day"
            return None
        if symbol in self.positions:
            self.last_block = "already held"
            return None
        rs = self.risk_state()
        if rs["halted"]:
            self.last_block = "; ".join(rs["reasons"]) or "risk halted"
            return None
        gate = self.regime_mult if USE_REGIME_GATE else 1.0
        if gate <= 0:
            self.last_block = f"market regime {self.regime_label or 'risk-off'}: {self.regime_why or 'no new entries'}"
            return None
        lev = leverage_of(symbol, is_leveraged)
        mult = rs["mult"] * gate * risk_mult_for_symbol(symbol, is_leveraged)
        shares, risk_amt = self.calculate_position_size(entry_price, stop_price, mult)
        if shares < 1:
            self.last_block = "size rounds to zero shares"
            return None
        ok, why = check_caps(self.positions.values(), self.equity, symbol, sector, industry,
                             lev, shares * entry_price, risk_amt)
        if not ok:
            self.last_block = why
            return None
        if USE_CORRELATION_CAP and self.returns_provider is not None and self.positions:
            cand_r = self.returns_provider(symbol)
            if cand_r is not None:
                held = {s: self.returns_provider(s) for s in self.positions}
                ok, why = check_correlation(cand_r, {k: v for k, v in held.items() if v is not None})
                if not ok:
                    self.last_block = why
                    return None
        slip = self._slip(shares, entry_price, atr, avg_vol)
        cost = shares * entry_price * (1 + COMMISSION_PCT + slip)
        if cost > self.cash:
            self.last_block = "not enough cash"
            return None
        trade = Trade(
            symbol=symbol, entry_price=entry_price, stop_price=stop_price, size=shares, risk_amount=risk_amt,
            equity_at_entry=self.equity, is_leveraged=is_leveraged, sector=sector, industry=industry,
            leverage=lev, high_water=entry_price, slip_pct=slip, **trade_kwargs,
        )
        trade.tranches = self._build_tranches(trade)
        if mult < 1.0:
            why = list(rs["reasons"]) + ([f"regime {self.regime_label}: {self.regime_why}"] if gate < 1.0 else [])
            trade.notes.append(f"risk x{mult:g} ({'; '.join(why) or 'leveraged ETF'})")
        self.cash -= cost
        self.positions[symbol] = trade
        self.daily_new_trades += 1
        return trade

    def enter(self, cand: dict) -> Optional[Trade]:
        symbol, df, i = cand["symbol"], cand["df"], cand["i"]
        checklist = cand["checklist"]
        funds = df.attrs.get("fundamentals", {})
        row = df.iloc[i]
        trade = self._open(
            symbol, float(df["Close"].iloc[i]), checklist.stop_price,
            sector=funds.get("sector") or "", industry=funds.get("industry") or "",
            is_leveraged=bool(df.attrs.get("is_leveraged", False)),
            atr=float(row.get("ATR", 0) or 0), avg_vol=float(row.get("AvgVol50", 0) or 0),
            entry_date=df.index[i], entry_idx=i,
        )
        if trade:
            trade.notes.insert(0, f"ORH+RVOL+LoD | Ext={cand['ext']:.1f}x | {checklist.notes} | "
                                  f"RiskATR={checklist.risk_atr_multiple:.2f}")
        return trade

    def enter_intraday(self, symbol: str, price: float, stop: float, date: pd.Timestamp,
                       bar_label: str, is_leveraged: bool = False, note: str = "",
                       sector: str = "", industry: str = "", atr: float = 0.0, avg_vol: float = 0.0) -> Optional[Trade]:
        """Paper entry at a live intraday price (the ORH job). Same brakes, caps and sizing as enter()."""
        trade = self._open(
            symbol, float(price), stop, sector=sector, industry=industry, is_leveraged=is_leveraged,
            atr=atr, avg_vol=avg_vol, entry_date=pd.Timestamp(date).normalize(), entry_idx=-1,
            entry_time=bar_label, last_check=bar_label, last_price=float(price), source="paper-orh",
        )
        if trade:
            trade.notes.insert(0, note or "ORH entry")
        return trade

    def apply_intraday_stops(self, trade: Trade, bars: pd.DataFrame) -> List[str]:
        """
        Check tranche stops against 5m bars (index = bar label, ET). Only bars AFTER trade.last_check count.
        A gap through a stop fills at that bar's open. Returns human-readable events.
        """
        events: List[str] = []
        if bars is None or bars.empty or trade.status != TradeStatus.OPEN:
            return events
        new = bars[bars.index > pd.Timestamp(trade.last_check)] if trade.last_check else bars
        risk_ps = trade.initial_risk_per_share
        for label, bar in new.iterrows():
            if risk_ps > 0:
                trade.mfe_r = max(trade.mfe_r, (float(bar["High"]) - trade.entry_price) / risk_ps)
            bar_fills = []
            for tr in sorted(trade.tranches, key=lambda t: -t.stop):
                if tr.shares > 0 and float(bar["Low"]) <= tr.stop:
                    fill = min(float(bar["Open"]), tr.stop)
                    self._sell(trade, tr.shares, fill, f"Stop @ {tr.stop:.2f} ({label:%H:%M})")
                    events.append(f"{trade.symbol}: stop {tr.stop:.2f} hit, sold {tr.shares} @ {fill:.2f}")
                    tr.shares = 0
                    bar_fills.append(fill)
            trade.tranches = [t for t in trade.tranches if t.shares > 0]
            done = not trade.tranches or trade.remaining_size <= 0
            if risk_ps > 0:
                worst = min(bar_fills) if (done and bar_fills) else float(bar["Low"])
                trade.mae_r = min(trade.mae_r, (worst - trade.entry_price) / risk_ps)
            if done:
                self._finalize(trade, pd.Timestamp(label).normalize(), trade.exit_price or float(bar["Open"]), "Hit stop(s)")
                events.append(f"{trade.symbol}: position closed, R={trade.r_multiple:+.2f}")
                return events
        trade.last_price = float(bars["Close"].iloc[-1])
        # Re-check the newest (possibly still forming) bar next time; completed bars are final.
        if len(bars) >= 2:
            newest_done = bars.index[-2]
            if not trade.last_check or newest_done > pd.Timestamp(trade.last_check):
                trade.last_check = newest_done.isoformat()
        return events

    def try_enter(self, symbol: str, df: pd.DataFrame, i: int) -> Optional[Trade]:
        cand = self.evaluate_candidate(symbol, df, i)
        return self.enter(cand) if cand else None

    def _build_tranches(self, trade: Trade) -> List[Tranche]:
        """3-stop: three equal tranches with stops 1/3, 2/3 and all the way to the full stop."""
        risk = trade.initial_risk_per_share
        n = int(trade.size)
        if not USE_THREE_STOP or n < 3:
            return [Tranche(n, trade.stop_price)]
        base = n // 3
        sizes = [base, base, n - 2 * base]
        return [Tranche(sz, trade.entry_price - risk * lvl) for sz, lvl in zip(sizes, THREE_STOP_LEVELS)]

    # ------------------------------------------------------------------ management
    def manage_open_trades(self, symbol: str, df: pd.DataFrame, i: int):
        trade = self.positions.get(symbol)
        if trade is None or trade.status != TradeStatus.OPEN:
            return

        row = df.iloc[i]
        date = df.index[i]
        open_, low, close = float(row["Open"]), float(row["Low"]), float(row["Close"])
        trade.day_count = i - trade.entry_idx          # trading days, weekends/holidays excluded
        risk_ps = trade.initial_risk_per_share
        if risk_ps <= 0:
            return

        # Excursions in R of the ORIGINAL risk (how far it ran for us / against us on the way)
        trade.mfe_r = max(trade.mfe_r, (float(row["High"]) - trade.entry_price) / risk_ps)
        trade.high_water = max(trade.high_water or trade.entry_price, close)

        # 1) Stops first. Gap-aware: a gap below the stop fills at the open, not the stop.
        fills = []
        for tr in sorted(trade.tranches, key=lambda t: -t.stop):
            if tr.shares > 0 and low <= tr.stop:
                fill = min(open_, tr.stop)
                self._sell(trade, tr.shares, fill, f"Stop @ {tr.stop:.2f}")
                tr.shares = 0
                fills.append(fill)
        trade.tranches = [t for t in trade.tranches if t.shares > 0]
        closed_now = not trade.tranches or trade.remaining_size <= 0
        # Worst point: the bar's low while we still held; once fully stopped out, no worse than where we got out.
        trade.mae_r = min(trade.mae_r, ((min(fills) if closed_now and fills else low) - trade.entry_price) / risk_ps)
        if closed_now:
            self._finalize(trade, date, trade.exit_price or open_, "Hit stop(s)")
            return

        current_r = (close - trade.entry_price) / risk_ps

        # 2) +2R partial, days 0-2
        if not trade.two_r_done and trade.day_count <= 2 and current_r >= PARTIAL_1_R:
            self._reduce(trade, PARTIAL_SIZE, close, "2R partial")
            trade.two_r_done = True
            trade.partials_taken += 1

        # 3) ATR-extension trims, each level fires once
        ext = float(row.get("ATR_ext_50", 0))
        if np.isfinite(ext) and current_r > 0:
            for level in ATR_EXT_PARTIAL_LEVELS:
                if ext >= level and level not in trade.ext_levels_hit:
                    trade.ext_levels_hit.add(level)
                    self._reduce(trade, ATR_EXT_PARTIAL_SIZE, close, f"ATR-ext trim ({ext:.1f}x >= {level:g}x)")
                    trade.partials_taken += 1

        # 4) Day 3 (once): reduce and move every remaining stop to breakeven
        if trade.day_count >= 3 and not trade.day3_done:
            trade.day3_done = True
            self._reduce(trade, DAY3_SIZE_REDUCTION, close, "Day3 reduction")
            if self.day3_be:
                for tr in trade.tranches:
                    tr.stop = max(tr.stop, trade.entry_price)
                trade.stop_price = max(trade.stop_price, trade.entry_price)
                trade.notes.append("Stops -> breakeven (Day 3)")
            trade.partials_taken += 1

        # 5) Day 4+: trailing exit. Variant is set per strategy so exit_lab.py can compare them on the same signals.
        if trade.day_count >= 4 and trade.remaining_size > 0:
            exit_reason = ""
            if self.exit_variant == "ema21":
                if close < float(row["EMA21"]):
                    exit_reason = "21-EMA trail exit"
            elif self.exit_variant == "atr_trail":
                atr_now = float(row.get("ATR", 0) or 0)
                if atr_now > 0 and close < trade.high_water - ATR_TRAIL_MULT * atr_now:
                    exit_reason = f"ATR trail exit ({ATR_TRAIL_MULT:g}x)"
            elif close < float(row["SMA10"]):
                exit_reason = "10-MA trail exit"
            if exit_reason:
                self._sell(trade, trade.remaining_size, close, exit_reason)
                trade.tranches = []
                self._finalize(trade, date, close, exit_reason)

    # ------------------------------------------------------------------ fills
    def _sell(self, trade: Trade, shares: float, price: float, reason: str):
        shares = int(min(shares, trade.remaining_size))
        if shares < 1:
            return
        slip = trade.slip_pct or SLIPPAGE_PCT
        proceeds = shares * price * (1 - COMMISSION_PCT - slip)
        basis = shares * trade.entry_price * (1 + COMMISSION_PCT + slip)
        self.cash += proceeds
        trade.realized_pnl += proceeds - basis
        trade.remaining_size -= shares
        trade.exit_price = price
        trade.notes.append(f"{reason}: sold {shares} @ {price:.2f}")

    def _reduce(self, trade: Trade, fraction: float, price: float, reason: str):
        """Sell a fraction of what is left, taking shares proportionally from every tranche."""
        total = sum(t.shares for t in trade.tranches)
        sell = int(total * fraction)
        if sell < 1:
            return
        left = sell
        for tr in trade.tranches:
            take = min(tr.shares, int(sell * tr.shares / total))
            tr.shares -= take
            left -= take
        for tr in trade.tranches:                      # rounding remainder
            if left <= 0:
                break
            take = min(tr.shares, left)
            tr.shares -= take
            left -= take
        trade.tranches = [t for t in trade.tranches if t.shares > 0]
        self._sell(trade, sell, price, reason)

    def _finalize(self, trade: Trade, date: pd.Timestamp, price: float, reason: str):
        trade.exit_date = date
        trade.exit_price = price
        trade.status = TradeStatus.CLOSED
        trade.pnl = trade.realized_pnl
        # R = net P&L over the dollars risked at entry (original stop, whole position, partials included)
        denom = trade.initial_risk_per_share * trade.size
        trade.r_multiple = trade.pnl / denom if denom > 0 else 0.0
        trade.notes.append(f"Closed: {reason}")
        self.closed_trades.append(trade)
        self.positions.pop(trade.symbol, None)

    def update_equity(self, date, market_data: Dict[str, pd.DataFrame]):
        mtm = self.cash
        for sym, trade in self.positions.items():
            if sym in market_data and date in market_data[sym].index:
                price = float(market_data[sym].loc[date, "Close"])
            else:
                price = trade.entry_price
            mtm += trade.remaining_size * price
        self.equity = mtm
        self.peak_equity = max(self.peak_equity, self.equity)
        self.equity_curve.append({"date": date, "equity": self.equity})

    def end_of_day(self):
        self.daily_new_trades = 0
        self.day_start_equity = self.equity      # tomorrow's daily-loss limit is measured from tonight's close

    def summary(self) -> dict:
        if not self.closed_trades:
            return {"message": "No closed trades yet", "final_equity": self.equity}

        rs = [t.r_multiple for t in self.closed_trades]
        pnls = [t.pnl for t in self.closed_trades]
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
        win_pnls = [p for p in pnls if p > 0]
        loss_pnls = [p for p in pnls if p < 0]

        max_dd = 0.0
        if self.equity_curve:
            eq = pd.Series([e["equity"] for e in self.equity_curve])
            max_dd = float((eq / eq.cummax() - 1).min() * 100)

        return {
            "total_trades": len(self.closed_trades),
            "win_rate": len(wins) / len(rs),
            "avg_r": float(np.mean(rs)),
            "avg_win_r": float(np.mean(wins)) if wins else 0,
            "avg_loss_r": float(np.mean(losses)) if losses else 0,
            "profit_factor": abs(sum(win_pnls) / sum(loss_pnls)) if loss_pnls else np.inf,
            "final_equity": self.equity,
            "return_pct": (self.equity / INITIAL_EQUITY - 1) * 100,
            "max_drawdown_pct": max_dd,
            "open_positions": len(self.positions),
            "leveraged_trades": sum(1 for t in self.closed_trades if t.is_leveraged),
            "avg_mfe_r": float(np.mean([t.mfe_r for t in self.closed_trades])),
            "avg_mae_r": float(np.mean([t.mae_r for t in self.closed_trades])),
            "capture_ratio": (float(sum(max(t.r_multiple, 0) for t in self.closed_trades) /
                                    sum(t.mfe_r for t in self.closed_trades if t.mfe_r > 0))
                              if any(t.mfe_r > 0 for t in self.closed_trades) else None),
        }
