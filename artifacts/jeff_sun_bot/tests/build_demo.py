"""Builds demo/dashboard_demo.html from the SYNTHETIC test market (clearly bannered as demo data), with a fake Alpaca."""
import importlib.util, os, sys
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(here)); sys.path.insert(0, here)
spec = importlib.util.spec_from_file_location("ta", os.path.join(here, "test_automation.py"))
ta = importlib.util.module_from_spec(spec); spec.loader.exec_module(ta)

import broker
from fake_alpaca import FakeAlpaca


class Px(dict):                                       # the fake broker's last price = last synthetic intraday close
    def __missing__(self, sym):
        f = ta.INTRA.get(sym)
        return float(f["Close"].iloc[-1]) if f is not None and len(f) else 100.0


fake = FakeAlpaca(); fake.prices = Px(); fake.fill_slip = 0.0007; broker.TRANSPORT = fake
os.environ.update(PAPER_BROKER="alpaca", ALPACA_API_KEY_ID="KEY", ALPACA_API_SECRET_KEY="SECRET", ALPACA_SEND_ORDERS="1")

star, entry_px = ta.test_all()                         # scan, ORH entry, entry day, days 1-4 (all mirrored to the fake broker)
st = ta.load()
px = st["positions"][0]["last_price"]
ta.TAIL[star]["2026-10-30"] = (px, px, entry_px * 0.97, entry_px * 0.98)
fake.prices[star] = entry_px * 0.98
fake.trade_to(star, entry_px * 0.97)                   # the broker's breakeven stops trigger intraday, as the simulator's do
ta.STATE["end"] = "2026-10-30"
ta.run("scan", "2026-10-30T16:30:00-04:00")
st = ta.load()
for r in st["scan"]["focus"]:
    ta.INTRA[r["symbol"]] = ta.five_min("2026-11-02", r["close"], "reclaim" if r["symbol"] == st["scan"]["focus"][1]["symbol"] else "weak", 15)
ta.STATE["end"] = "2026-10-30"
ta.run("intraday", "2026-11-02T10:45:00-05:00")
st = ta.load()
out = os.path.join(os.path.dirname(here), "demo", "dashboard_demo.html")
import dashboard
dashboard.write_dashboard(st, demo=True, path=out)
print("demo written:", out, "| open:", len(st["positions"]), "closed:", len(st["closed"]), "equity:", round(st["equity"]),
      "| broker recon ok:", st["broker"]["recon"].get("ok"), "| regime:", st["regime"].get("label"))
