"""
In-memory stand-in for Alpaca's paper REST API, used only by the tests. It enforces the behaviours the mirror
has to respect (and that Alpaca documents): unique client_order_id, no selling shares already held by open
sell orders, asynchronous-looking cancels, market orders that fill at the current price.
"""
import json, itertools
from urllib.parse import urlparse, parse_qs


class FakeAlpaca:
    def __init__(self, equity=100_000.0):
        self.equity, self.cash = equity, equity
        self.prices = {}                 # symbol -> last price
        self.tradable = {}               # symbol -> bool (default True)
        self.orders, self.positions = {}, {}    # id -> order ; symbol -> {"qty","avg"}
        self.ids = itertools.count(1)
        self.calls = []                  # (method, path, body) of every request, for assertions
        self.fill_slip = 0.0             # market orders fill this fraction worse than last
        self.auth_ok = True

    # ---- helpers
    def held_by_open_sells(self, sym):
        return sum(int(o["qty"]) for o in self.orders.values()
                   if o["symbol"] == sym and o["side"] == "sell" and o["status"] in ("new", "accepted"))

    def _fill(self, o, price):
        qty = int(o["qty"]); sym = o["symbol"]
        pos = self.positions.setdefault(sym, {"qty": 0, "avg": 0.0})
        if o["side"] == "buy":
            pos["avg"] = (pos["avg"] * pos["qty"] + price * qty) / (pos["qty"] + qty); pos["qty"] += qty; self.cash -= qty * price
        else:
            pos["qty"] -= qty; self.cash += qty * price
        if pos["qty"] == 0:
            self.positions.pop(sym, None)
        o.update(status="filled", filled_qty=str(qty), filled_avg_price=f"{price:.4f}")

    def trade_to(self, sym, low):
        """Price trades down to `low`: any sell-stop at or above it triggers."""
        for o in list(self.orders.values()):
            if o["symbol"] == sym and o["type"] == "stop" and o["status"] in ("new", "accepted") and float(o["stop_price"]) >= low:
                self._fill(o, min(float(o["stop_price"]), self.prices[sym]))

    def open_market_day(self):
        for o in list(self.orders.values()):
            if o["type"] == "market" and o["status"] == "accepted":
                self._fill(o, self.prices[o["symbol"]])

    # ---- the transport the client calls
    def __call__(self, method, url, headers, body):
        u = urlparse(url); path = u.path; q = {k: v[0] for k, v in parse_qs(u.query).items()}
        data = json.loads(body) if body else None
        self.calls.append((method, path, data))
        if not self.auth_ok or headers.get("APCA-API-KEY-ID") != "KEY":
            return 401, json.dumps({"message": "unauthorized"})
        j = lambda status, obj: (status, json.dumps(obj))
        if method == "GET" and path == "/v2/account":
            return j(200, {"status": "ACTIVE", "equity": str(self.equity), "cash": str(self.cash), "buying_power": str(self.cash * 2), "trading_blocked": False})
        if method == "GET" and path == "/v2/clock":
            return j(200, {"is_open": True, "next_open": "2026-10-26T09:30:00-04:00"})
        if method == "GET" and path.startswith("/v2/assets/"):
            sym = path.split("/")[-1]
            return j(200, {"symbol": sym, "tradable": self.tradable.get(sym, True), "status": "active"})
        if method == "GET" and path == "/v2/positions":
            return j(200, [{"symbol": s, "qty": str(p["qty"]), "avg_entry_price": str(p["avg"])} for s, p in self.positions.items()])
        if method == "GET" and path.startswith("/v2/positions/"):
            sym = path.split("/")[-1]
            return j(200, {"symbol": sym, "qty": str(self.positions[sym]["qty"])}) if sym in self.positions else j(404, {"message": "position does not exist"})
        if method == "GET" and path == "/v2/orders":
            rows = [o for o in self.orders.values() if o["status"] in ("new", "accepted")]
            if "symbols" in q:
                rows = [o for o in rows if o["symbol"] in q["symbols"].split(",")]
            return j(200, rows)
        if method == "GET" and path == "/v2/orders:by_client_order_id":
            hit = [o for o in self.orders.values() if o["client_order_id"] == q.get("client_order_id")]
            return j(200, hit[0]) if hit else j(404, {"message": "order not found"})
        if method == "GET" and path.startswith("/v2/orders/"):
            oid = path.split("/")[-1]
            return j(200, self.orders[oid]) if oid in self.orders else j(404, {"message": "order not found"})
        if method == "DELETE" and path.startswith("/v2/orders/"):
            oid = path.split("/")[-1]
            if oid not in self.orders:
                return j(404, {"message": "order not found"})
            if self.orders[oid]["status"] in ("new", "accepted"):
                self.orders[oid]["status"] = "canceled"
            return 204, ""
        if method == "POST" and path == "/v2/orders":
            if any(o["client_order_id"] == data.get("client_order_id") for o in self.orders.values()):
                return j(422, {"code": 40010001, "message": "client_order_id must be unique"})
            sym, qty, side = data["symbol"], int(data["qty"]), data["side"]
            if side == "sell":
                avail = self.positions.get(sym, {"qty": 0})["qty"] - self.held_by_open_sells(sym)
                if qty > avail:
                    return j(403, {"code": 40310000, "message": f"insufficient qty available for order (requested: {qty}, available: {avail})"})
            o = {"id": f"ord{next(self.ids)}", "client_order_id": data.get("client_order_id"), "symbol": sym, "qty": str(qty),
                 "side": side, "type": data["type"], "time_in_force": data["time_in_force"], "stop_price": data.get("stop_price"),
                 "status": "accepted", "filled_qty": "0", "filled_avg_price": None}
            self.orders[o["id"]] = o
            if o["type"] == "market" and getattr(self, "market_open", True):
                px = self.prices[sym] * (1 + self.fill_slip if side == "buy" else 1 - self.fill_slip)
                self._fill(o, px)
            return j(200, o)
        return j(404, {"message": f"unhandled {method} {path}"})
