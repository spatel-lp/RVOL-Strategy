"""
Rules-based universe with point-in-time snapshots.

The hand-picked list in data.py is today's winners, which flatters any backtest (survivorship bias). This
builds the universe from rules instead: every US-listed common stock from the Nasdaq Trader symbol
directories, filtered by price, 20-day dollar volume and ADR%, top N by dollar volume, plus the liquid
leveraged ETFs Jeff likes. It is rebuilt weekly and every snapshot is kept in state/universe/, so a future
backtest can ask "what was the universe on 2027-03-01?" and use the answer instead of hindsight.

It cannot repair the past: the history before your first snapshot stays unknown.
"""

import glob
import json
import os
import re
import time
import urllib.request
from datetime import date as _date
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config import (
    PAPER_STATE_DIR, UNIVERSE_CHUNK, UNIVERSE_LEV_MIN_DOLLAR_VOL, UNIVERSE_MIN_ADR, UNIVERSE_MIN_DOLLAR_VOL,
    UNIVERSE_MIN_PRICE, UNIVERSE_MODE, UNIVERSE_REFRESH_DAYS, UNIVERSE_SIZE,
)

NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
JUNK_NAME = re.compile(r"warrant|\bunits?\b|preferred|\bright\b|\brights\b|notes? due|depositary|trust preferred|acquisition corp", re.I)
TICKER = re.compile(r"^[A-Z]{1,5}$")


def _dir() -> str:
    return os.path.join(os.environ.get("PAPER_STATE_DIR", PAPER_STATE_DIR), "universe")


def _read(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (paper-trading-universe)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse_symbol_directory(text: str, symbol_col: str, etf_col: str, test_col: str,
                           status_col: Optional[str] = None) -> List[str]:
    """Parse a pipe-delimited Nasdaq Trader file into plain common-stock tickers."""
    rows = [ln.split("|") for ln in text.strip().splitlines()]
    header = rows[0]
    ix = {name: i for i, name in enumerate(header)}
    name_i = ix.get("Security Name")
    out = []
    for r in rows[1:]:
        if len(r) < len(header) or r[0].startswith("File Creation"):
            continue
        if r[ix[test_col]] != "N" or r[ix[etf_col]] != "N":
            continue
        if status_col and r[ix[status_col]] not in ("N", ""):
            continue
        if name_i is not None and JUNK_NAME.search(r[name_i]):
            continue
        sym = r[ix[symbol_col]].strip()
        if TICKER.match(sym):
            out.append(sym)
    return out


def fetch_seed_symbols() -> List[str]:
    nas = parse_symbol_directory(_read(NASDAQ_LISTED), "Symbol", "ETF", "Test Issue", "Financial Status")
    oth = parse_symbol_directory(_read(OTHER_LISTED), "ACT Symbol", "ETF", "Test Issue")
    return sorted(set(nas) | set(oth))


def _download(symbols: List[str]) -> pd.DataFrame:
    import yfinance as yf
    return yf.download(symbols, period="3mo", interval="1d", group_by="ticker", auto_adjust=True,
                       threads=True, progress=False)


def screen(symbols: List[str], downloader: Callable = _download, chunk: int = UNIVERSE_CHUNK,
           pause: float = 1.0) -> pd.DataFrame:
    """Price, 20-day dollar volume and ADR% for each symbol that returned enough bars."""
    rows = []
    for k in range(0, len(symbols), chunk):
        part = symbols[k: k + chunk]
        try:
            raw = downloader(part)
        except Exception as e:                       # one bad batch must not kill the whole refresh
            print(f"  universe: batch {k // chunk} failed: {e}")
            continue
        if raw is None or raw.empty:
            continue
        for sym in part:
            try:
                d = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
                d = d.dropna(subset=["Close", "High", "Low", "Volume"]).tail(20)
                if len(d) < 15:
                    continue
                rows.append({
                    "symbol": sym, "close": float(d["Close"].iloc[-1]),
                    "dollar_vol": float((d["Close"] * d["Volume"]).mean()),
                    "adr": float(((d["High"] - d["Low"]) / d["Close"]).mean() * 100),
                })
            except Exception:
                continue
        if pause:
            time.sleep(pause)
    return pd.DataFrame(rows, columns=["symbol", "close", "dollar_vol", "adr"])


def select(df: pd.DataFrame, leveraged: set, size: int = UNIVERSE_SIZE) -> List[str]:
    if df.empty:
        return []
    stocks = df[~df["symbol"].isin(leveraged)]
    ok = stocks[(stocks["close"] >= UNIVERSE_MIN_PRICE) & (stocks["dollar_vol"] >= UNIVERSE_MIN_DOLLAR_VOL)
                & (stocks["adr"] >= UNIVERSE_MIN_ADR)]
    picked = ok.sort_values("dollar_vol", ascending=False).head(size)["symbol"].tolist()
    lev = df[df["symbol"].isin(leveraged) & (df["dollar_vol"] >= UNIVERSE_LEV_MIN_DOLLAR_VOL)]
    return picked + sorted(lev["symbol"].tolist())


def build_snapshot(today: _date, leveraged: set, seed_fn: Callable = fetch_seed_symbols,
                   downloader: Callable = _download, pause: float = 1.0) -> dict:
    seeds = seed_fn()
    scr = screen(sorted(set(seeds) | set(leveraged)), downloader=downloader, pause=pause)
    symbols = select(scr, leveraged)
    return {
        "date": today.isoformat(), "symbols": symbols, "n_seed": len(seeds), "n_screened": int(len(scr)),
        "n_pass": len(symbols),
        "params": {"size": UNIVERSE_SIZE, "min_dollar_vol": UNIVERSE_MIN_DOLLAR_VOL, "min_adr": UNIVERSE_MIN_ADR,
                   "min_price": UNIVERSE_MIN_PRICE},
    }


def save_snapshot(snap: dict) -> str:
    os.makedirs(_dir(), exist_ok=True)
    path = os.path.join(_dir(), f"universe_{snap['date']}.json")
    with open(path, "w") as f:
        json.dump(snap, f)
    return path


def snapshot_as_of(day: _date) -> Optional[dict]:
    """The newest snapshot taken on or before `day` (for point-in-time backtests)."""
    best = None
    for p in glob.glob(os.path.join(_dir(), "universe_*.json")):
        d = os.path.basename(p)[9:19]
        if d <= day.isoformat() and (best is None or d > best[0]):
            best = (d, p)
    if not best:
        return None
    with open(best[1]) as f:
        return json.load(f)


def ensure_universe(today: _date, handpicked: Callable[[int], List[str]], leveraged: set,
                    max_symbols: Optional[int] = None, force: bool = False,
                    seed_fn: Callable = fetch_seed_symbols, downloader: Callable = _download,
                    pause: float = 1.0) -> Tuple[List[str], dict]:
    """Returns (symbols, info). info = {source, date, n, note}. Never raises: falls back to the last snapshot, then the hand-picked list."""
    mode = os.environ.get("PAPER_UNIVERSE_MODE", UNIVERSE_MODE)
    cap = max_symbols or 10_000
    if mode == "handpicked":
        syms = handpicked(cap)
        return syms, {"source": "handpicked", "date": None, "n": len(syms), "note": "hand-picked list (survivorship bias)"}
    snap = snapshot_as_of(today)
    age = (today - _date.fromisoformat(snap["date"])).days if snap else None
    note = ""
    if force or snap is None or age >= UNIVERSE_REFRESH_DAYS:
        try:
            fresh = build_snapshot(today, leveraged, seed_fn=seed_fn, downloader=downloader, pause=pause)
            if fresh["n_pass"] >= 30:
                save_snapshot(fresh)
                snap, note = fresh, "refreshed"
            else:
                note = f"refresh produced only {fresh['n_pass']} names, kept previous snapshot"
        except Exception as e:
            note = f"refresh failed ({type(e).__name__}), kept previous snapshot"
    if snap is None:
        syms = handpicked(cap)
        return syms, {"source": "handpicked", "date": None, "n": len(syms),
                      "note": (note + "; " if note else "") + "no snapshot available, using hand-picked list"}
    syms = snap["symbols"][:cap] if len(snap["symbols"]) > cap else snap["symbols"]
    return syms, {"source": "rules", "date": snap["date"], "n": len(syms), "note": note or "cached snapshot"}
