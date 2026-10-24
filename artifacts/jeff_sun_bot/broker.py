"""
Alpaca PAPER mirror.

The simulator in strategy.py stays the source of truth for every decision. This module copies its actions to an
Alpaca *paper* account so you can compare real paper fills with the fill model, and have real protective stops
sitting at the broker:

  entry (intraday job)  market buy -> wait for the fill -> three GTC sell-stop orders (one per 3-stop tranche)
  partial / trim / trail (scan job)  cancel the stops, market sell the shares the simulator sold (queued for the
                        next open: the scan runs after the close), put stops back on what is left
  every run             reconcile: broker positions vs simulator positions, missing stops re-placed, account equity
                        compared; nothing else is ever auto-corrected (mismatches are reported, not "fixed")

Safety, in order of importance:
  * PAPER ONLY. The client refuses any base URL that is not paper-api.alpaca.markets. There is no live mode.
  * DRY RUN BY DEFAULT. Reads go out; writes (orders, cancels) are only logged, until ALPACA_SEND_ORDERS=1.
  * Keys come from the environment only (ALPACA_API_KEY_ID, ALPACA_API_SECRET_KEY) and are never logged or saved.
  * A broker failure never stops the simulation: it is logged on the dashboard and the job carries on.
  * Idempotent: deterministic client_order_ids, and a duplicate-id rejection is treated as "already placed".

Written from Alpaca's public v2 REST documentation. It could not be exercised against the real service from the
environment it was built in; tests run against an in-memory fake. Run `python broker.py --check` first.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

PAPER_HOST = "paper-api.alpaca.markets"
DEFAULT_BASE = f"https://{PAPER_HOST}"
TRANSPORT: Optional[Callable] = None          # tests inject a fake: (method, url, headers, body_bytes) -> (status, text)
SELL_NOTE = re.compile(r"^(?P<reason>.+?): sold (?P<n>\d+) @ (?P<px>[\d.]+)$")
MAX_LOG = 80


class BrokerError(Exception):
    pass


def _urllib_transport(method: str, url: str, headers: dict, body: Optional[bytes]) -> Tuple[int, str]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


class AlpacaPaper:
    def __init__(self, key: str, secret: str, base_url: Optional[str] = None, send_orders: bool = False,
                 transport: Optional[Callable] = None, sleep: Callable = time.sleep):
        base = (base_url or DEFAULT_BASE).rstrip("/")
        if urllib.parse.urlparse(base).netloc.lower() != PAPER_HOST:
            raise BrokerError(f"refusing {base!r}: only the Alpaca PAPER endpoint ({PAPER_HOST}) is supported")
        self.base, self.send_orders, self.sleep = base, send_orders, sleep
        self._headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "Content-Type": "application/json"}
        self._transport = transport or TRANSPORT or _urllib_transport
        self.dry_log: List[dict] = []          # every write that WOULD have been sent (dry run) or was sent
        self._tradable: Dict[str, bool] = {}

    # ------------------------------------------------------------------ plumbing
    def _call(self, method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None):
        url = self.base + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        status, text = self._transport(method, url, self._headers, json.dumps(body).encode() if body is not None else None)
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            data = {"raw": text[:200]}
        return status, data

    def _read(self, path: str, params: Optional[dict] = None):
        status, data = self._call("GET", path, params)
        if status >= 400:
            raise BrokerError(f"GET {path} -> {status}: {str(data)[:160]}")
        return data

    def _write_blocked(self, action: str, detail: dict) -> Optional[dict]:
        """In dry-run, record the write instead of sending it."""
        self.dry_log.append({"t": datetime.utcnow().isoformat(timespec="seconds"), "action": action, "detail": detail,
                             "sent": self.send_orders})
        if self.send_orders:
            return None
        return {"id": "dry-" + str(detail.get("client_order_id") or detail.get("order_id") or len(self.dry_log)),
                "status": "dry_run", "client_order_id": detail.get("client_order_id"), "filled_qty": "0",
                "filled_avg_price": None, "qty": str(detail.get("qty", "0"))}

    # ------------------------------------------------------------------ reads
    def account(self) -> dict:
        return self._read("/v2/account")

    def clock(self) -> dict:
        return self._read("/v2/clock")

    def positions(self) -> List[dict]:
        return self._read("/v2/positions")

    def position_qty(self, symbol: str) -> float:
        status, data = self._call("GET", f"/v2/positions/{urllib.parse.quote(symbol)}")
        if status == 404:
            return 0.0
        if status >= 400:
            raise BrokerError(f"GET position {symbol} -> {status}")
        return float(data.get("qty", 0))

    def tradable(self, symbol: str) -> bool:
        if symbol not in self._tradable:
            status, data = self._call("GET", f"/v2/assets/{urllib.parse.quote(symbol)}")
            if status == 404:                                  # Alpaca does not list it
                self._tradable[symbol] = False
            elif status != 200:                                # auth / rate-limit / outage: an ERROR, never "not tradable"
                raise BrokerError(f"asset lookup {symbol} -> {status}: {str(data)[:120]}")
            else:
                self._tradable[symbol] = bool(data.get("tradable")) and data.get("status", "active") == "active"
        return self._tradable[symbol]

    def open_orders(self, symbol: Optional[str] = None) -> List[dict]:
        params = {"status": "open", "limit": 200, "direction": "desc"}
        if symbol:
            params["symbols"] = symbol
        return self._read("/v2/orders", params)

    def order(self, order_id: str) -> dict:
        return self._read(f"/v2/orders/{order_id}")

    def order_by_client_id(self, client_order_id: str) -> Optional[dict]:
        status, data = self._call("GET", "/v2/orders:by_client_order_id", {"client_order_id": client_order_id})
        return data if status == 200 else None

    # ------------------------------------------------------------------ writes
    def submit(self, symbol: str, qty: int, side: str, otype: str = "market", tif: str = "day",
               stop_price: Optional[float] = None, client_order_id: Optional[str] = None) -> dict:
        body = {"symbol": symbol, "qty": str(int(qty)), "side": side, "type": otype, "time_in_force": tif}
        if stop_price is not None:
            body["stop_price"] = f"{round(float(stop_price), 2):.2f}"          # stocks >= $1 are quoted to the cent
        if client_order_id:
            body["client_order_id"] = client_order_id[:48]
        blocked = self._write_blocked("submit_order", body)
        if blocked is not None:
            return blocked
        status, data = self._call("POST", "/v2/orders", body=body)
        if status in (200, 201):
            return data
        if status == 422 and "client_order_id" in str(data).lower() and client_order_id:
            existing = self.order_by_client_id(body["client_order_id"])     # same id again = already placed: idempotent
            if existing:
                return existing
        raise BrokerError(f"order {side} {qty} {symbol} {otype} -> {status}: {str(data)[:200]}")

    def cancel(self, order_id: str) -> None:
        if self._write_blocked("cancel_order", {"order_id": order_id}) is not None:
            return
        status, data = self._call("DELETE", f"/v2/orders/{order_id}")
        if status not in (200, 204, 404, 422):               # 404/422: already filled or cancelled, which is fine
            raise BrokerError(f"cancel {order_id} -> {status}: {str(data)[:120]}")

    def wait_filled(self, order: dict, timeout: float = 20.0, interval: float = 1.0) -> dict:
        """Poll until filled / terminal, or the timeout. Returns the freshest order dict."""
        if order.get("status") == "dry_run":
            return order
        waited = 0.0
        while order.get("status") not in ("filled", "canceled", "expired", "rejected") and waited < timeout:
            self.sleep(interval)
            waited += interval
            order = self.order(order["id"])
        return order

    def wait_no_open_sells(self, symbol: str, timeout: float = 10.0) -> None:
        """Cancels are asynchronous: wait until the cancelled orders no longer hold shares."""
        if not self.send_orders:
            return
        waited = 0.0
        while waited < timeout:
            if not [o for o in self.open_orders(symbol) if o.get("side") == "sell" and "stop" in o.get("type", "")]:
                return
            self.sleep(0.5)
            waited += 0.5


def make_client_from_env(transport: Optional[Callable] = None) -> Optional[AlpacaPaper]:
    """None unless PAPER_BROKER=alpaca. Raises BrokerError for missing keys or a non-paper URL."""
    if os.environ.get("PAPER_BROKER", "").lower() != "alpaca":
        return None
    key, secret = os.environ.get("ALPACA_API_KEY_ID"), os.environ.get("ALPACA_API_SECRET_KEY")
    if not key or not secret:
        raise BrokerError("PAPER_BROKER=alpaca but ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY are not set")
    send = os.environ.get("ALPACA_SEND_ORDERS", "").lower() in ("1", "true", "yes")
    return AlpacaPaper(key, secret, os.environ.get("ALPACA_BASE_URL"), send_orders=send, transport=transport)


# ====================================================================================== the mirror
class Mirror:
    """Copies the simulator's actions to Alpaca and records what happened in state['broker']."""

    def __init__(self, client: AlpacaPaper, state: dict, say: Callable[[str, str], None]):
        self.c, self.state, self.say = client, state, say
        b = state.setdefault("broker", {})
        b.setdefault("orders", {}); b.setdefault("fills", []); b.setdefault("log", []); b.setdefault("errors", [])
        b["mode"] = "send" if client.send_orders else "dry-run"
        self.b = b
        self.seq = int(b.get("seq", 0))

    # ---- helpers
    def _note(self, action: str, detail: str) -> None:
        self.b["log"].append({"t": datetime.utcnow().isoformat(timespec="seconds"), "action": action, "detail": detail,
                              "sent": self.c.send_orders})
        self.b["log"] = self.b["log"][-MAX_LOG:]

    def _error(self, what: str, exc: Exception) -> None:
        msg = f"{what}: {type(exc).__name__}: {str(exc)[:160]}"
        self.b["errors"] = (self.b["errors"] + [{"t": datetime.utcnow().isoformat(timespec="seconds"), "msg": msg}])[-20:]
        self.say("WARN", f"Alpaca mirror: {msg}")

    def safe(self, what: str, fn: Callable, *a, **k):
        """Run a broker action; a failure is recorded and swallowed so the simulation always continues."""
        try:
            return fn(*a, **k)
        except Exception as e:                           # noqa: BLE001 - the whole point is to never propagate
            self._error(what, e)
            return None

    def _cid(self, trade, role: str) -> str:
        self.seq += 1
        self.b["seq"] = self.seq
        return f"orh-{trade.entry_date:%Y%m%d}-{trade.symbol}-{role}-{self.seq}"[:48]

    def _stop_orders(self, symbol: str) -> List[dict]:
        return [o for o in self.c.open_orders(symbol) if o.get("side") == "sell" and "stop" in o.get("type", "")]

    def _queued_sell_qty(self, symbol: str) -> int:
        return sum(int(float(o.get("qty", 0))) for o in self.c.open_orders(symbol)
                   if o.get("side") == "sell" and o.get("type") == "market")

    # ---- entry
    def on_entry(self, trade, today: str) -> None:
        sym = trade.symbol
        if not self.c.tradable(sym):
            self.b["orders"][sym] = {"status": "skipped", "why": "not tradable on Alpaca", "date": today}
            self.say("WARN", f"Alpaca mirror: {sym} is not tradable on Alpaca, position exists in the simulator only")
            return
        cid = f"orh-{trade.entry_date:%Y%m%d}-{sym}-buy"
        order = self.c.submit(sym, int(trade.size), "buy", "market", "day", client_order_id=cid)
        order = self.c.wait_filled(order)
        filled = int(float(order.get("filled_qty") or 0))
        px = float(order["filled_avg_price"]) if order.get("filled_avg_price") else None
        self._note("buy", f"{sym} x{int(trade.size)} market -> {order.get('status')}, filled qty {filled}" + (f" @ {px:.2f}" if px else ""))
        rec = {"status": order.get("status"), "buy_id": order.get("id"), "qty": int(trade.size), "filled": filled,
               "avg_price": px, "date": today, "stops": []}
        self.b["orders"][sym] = rec
        if px:
            self.b["fills"].append({
                "t": datetime.utcnow().isoformat(timespec="seconds"), "symbol": sym, "side": "buy", "qty": filled,
                "sim_price": round(trade.entry_price, 4), "broker_price": round(px, 4),
                "diff_pct": round((px / trade.entry_price - 1) * 100, 3), "modeled_slip_pct": round(trade.slip_pct * 100, 3),
            })
            self.b["fills"] = self.b["fills"][-100:]
        if filled > 0 or not self.c.send_orders:
            self.sync_protection(trade, cap=filled if self.c.send_orders else None)

    # ---- protective stops
    def sync_protection(self, trade, cap: Optional[int] = None, last_price: Optional[float] = None) -> None:
        """Make the broker's stop orders match the simulator's tranches: cancel what is there, place what should be."""
        sym = trade.symbol
        for o in self._stop_orders(sym):
            self.c.cancel(o["id"])
        self.c.wait_no_open_sells(sym)
        if cap is not None:                                     # right after an entry fill: protect exactly what filled
            have = cap
        elif self.c.send_orders:                                # otherwise: what we hold minus what is already queued to sell
            have = int(self.c.position_qty(sym)) - self._queued_sell_qty(sym)
        else:
            have = int(trade.remaining_size)
        placed = []
        last = last_price or trade.last_price or trade.entry_price
        for tr in sorted(trade.tranches, key=lambda t: -t.stop):
            qty = min(int(tr.shares), have)
            if qty < 1:
                continue
            have -= qty
            stop = round(tr.stop, 2)
            if stop >= last * 0.999:                      # the simulator would already be out: exit at the next open
                o = self.c.submit(sym, qty, "sell", "market", "day", client_order_id=self._cid(trade, "xs"))
                self._note("sell", f"{sym} x{qty} market (stop {stop} is at/above the last price {last:.2f})")
            else:
                o = self.c.submit(sym, qty, "sell", "stop", "gtc", stop_price=stop, client_order_id=self._cid(trade, "stop"))
                self._note("stop", f"{sym} x{qty} stop {stop:.2f} GTC")
            placed.append({"id": o.get("id"), "qty": qty, "stop": stop})
        rec = self.b["orders"].setdefault(sym, {})
        rec["stops"] = placed

    # ---- exits decided by the simulator at the close
    def on_sim_sells(self, trade, sells: List[Tuple[int, str]], last_price: float) -> None:
        sym = trade.symbol
        total = sum(q for q, _ in sells)
        if total < 1:
            return
        for o in self._stop_orders(sym):                  # free the shares first: Alpaca refuses to oversell
            self.c.cancel(o["id"])
        self.c.wait_no_open_sells(sym)
        have = int(self.c.position_qty(sym)) if self.c.send_orders else total
        qty = min(total, have) if self.c.send_orders else total
        if qty >= 1:
            o = self.c.submit(sym, qty, "sell", "market", "day", client_order_id=self._cid(trade, "sell"))
            self._note("sell", f"{sym} x{qty} market ({'; '.join(r for _, r in sells)})")
            self.b["orders"].setdefault(sym, {}).setdefault("exits", []).append(
                {"id": o.get("id"), "qty": qty, "why": "; ".join(r for _, r in sells)})
        if trade.remaining_size > 0:
            self.sync_protection(trade, last_price=last_price)
        else:
            self.b["orders"].setdefault(sym, {})["stops"] = []

    # ---- reconcile
    def reconcile(self, positions: Dict[str, object], sim_equity: float, ensure_stops: bool = True) -> dict:
        out = {"asof": datetime.utcnow().isoformat(timespec="seconds"), "rows": [], "ok": True, "mode": self.b["mode"]}
        acct = self.c.account()
        self.b["account"] = {"equity": float(acct.get("equity", 0)), "cash": float(acct.get("cash", 0)),
                             "buying_power": float(acct.get("buying_power", 0)), "status": acct.get("status"),
                             "blocked": bool(acct.get("trading_blocked"))}
        self.b["equity_gap_pct"] = round((self.b["account"]["equity"] / sim_equity - 1) * 100, 2) if sim_equity else None
        if not self.c.send_orders:
            out["note"] = "dry run: orders are logged, not sent, so positions are not compared"
            self.b["recon"] = out
            return out
        broker = {p["symbol"]: int(float(p["qty"])) for p in self.c.positions()}
        for sym in sorted(set(broker) | set(positions)):
            sim_qty = int(positions[sym].remaining_size) if sym in positions else 0
            b_qty = broker.get(sym, 0)
            status = "ok"
            if sim_qty != b_qty:
                queued = self._queued_sell_qty(sym)
                if b_qty - sim_qty == queued and queued > 0:
                    status = "pending exit (queued for the open)"
                elif sym not in positions:
                    status = "at broker only"
                elif b_qty == 0:
                    status = "not at broker"
                else:
                    status = "quantity differs"
            if status != "ok" and not status.startswith("pending"):
                out["ok"] = False
            if ensure_stops and sym in positions and positions[sym].tranches and status in ("ok", "pending exit (queued for the open)"):
                want = sum(int(t.shares) for t in positions[sym].tranches)
                have = sum(int(float(o.get("qty", 0))) for o in self._stop_orders(sym))
                if have != want:
                    self._note("heal", f"{sym}: broker stops cover {have} of {want} shares, re-placing")
                    self.sync_protection(positions[sym])
                    status = status + "; stops re-placed"
            out["rows"].append({"symbol": sym, "sim_qty": sim_qty, "broker_qty": b_qty, "status": status})
        if not out["ok"]:
            self.say("WARN", "Alpaca mirror: positions differ from the simulator: " +
                     ", ".join(f"{r['symbol']} sim {r['sim_qty']} / broker {r['broker_qty']}" for r in out["rows"] if r["status"] not in ("ok",) and not r["status"].startswith("pending")))
        self.b["recon"] = out
        return out

    def finish(self) -> None:
        self.b["asof"] = datetime.utcnow().isoformat(timespec="seconds")
        self.b["seq"] = self.seq


def sells_from_notes(new_notes: List[str]) -> List[Tuple[int, str]]:
    """Shares the simulator sold by ITS OWN rules (partials, trims, Day-3 cut, trail), not stop fills: the broker's stops handle those."""
    out = []
    for note in new_notes:
        m = SELL_NOTE.match(note)
        if m and not m.group("reason").startswith("Stop @"):
            out.append((int(m.group("n")), m.group("reason")))
    return out


# ====================================================================================== CLI
def _check() -> int:
    try:
        c = make_client_from_env()
    except BrokerError as e:
        print("not configured:", e)
        return 2
    if c is None:
        print("PAPER_BROKER is not set to 'alpaca'; nothing to check.")
        return 2
    a, clock = c.account(), c.clock()
    print(f"endpoint        : {c.base} (paper only)")
    print(f"mode            : {'SENDING paper orders' if c.send_orders else 'DRY RUN (set ALPACA_SEND_ORDERS=1 to send)'}")
    print(f"account status  : {a.get('status')}  trading_blocked={a.get('trading_blocked')}")
    print(f"equity / cash   : ${float(a.get('equity', 0)):,.0f} / ${float(a.get('cash', 0)):,.0f}   buying power ${float(a.get('buying_power', 0)):,.0f}")
    print(f"market open now : {clock.get('is_open')}   next open {clock.get('next_open')}")
    print(f"open positions  : {len(c.positions())}   open orders: {len(c.open_orders())}")
    return 0


if __name__ == "__main__":
    sys.exit(_check() if "--check" in sys.argv else (print(__doc__) or 0))
