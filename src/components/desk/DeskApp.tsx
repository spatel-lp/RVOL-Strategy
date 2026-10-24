"use client";

import { useEffect, useMemo, useState } from "react";
import { Bell, BookOpen, LayoutDashboard, RotateCcw, ScanSearch, ScrollText } from "lucide-react";
import { MAX_NEW_DAY, NAMES, START_EQUITY, localDateKey, realizedR, rMultiple, stopLevels, type ListKind, type ScanName, type Trade } from "@/lib/desk/model";
import { markEquity, markPrice, unreadCount, useDesk } from "@/lib/desk/store";

type Tab = "desk" | "scan" | "alerts" | "paper" | "journal" | "rules";

const TABS: { id: Tab; label: string; icon: typeof LayoutDashboard }[] = [
  { id: "desk", label: "Desk", icon: LayoutDashboard },
  { id: "scan", label: "Stalk", icon: ScanSearch },
  { id: "alerts", label: "Buy alert", icon: Bell },
  { id: "paper", label: "Paper", icon: BookOpen },
  { id: "journal", label: "Journal", icon: ScrollText },
  { id: "rules", label: "Rules", icon: ScrollText },
];

const FILTERS: { id: ListKind | "all"; label: string }[] = [
  { id: "all", label: "All" },
  { id: "stalk", label: "Stalk" },
  { id: "focus", label: "Focus" },
  { id: "reversion", label: "Mean reversion" },
  { id: "intraday", label: "Intraday" },
];

export function DeskApp() {
  const [tab, setTab] = useState<Tab>("desk");
  const [ready, setReady] = useState(false);
  const [filter, setFilter] = useState<ListKind | "all">("stalk");
  const [ticket, setTicket] = useState<ScanName | null>(null);
  const desk = useDesk();

  useEffect(() => setReady(true), []);

  const equity = ready ? markEquity(desk.cash, desk.trades) : START_EQUITY;
  const open = desk.trades.filter((t) => t.status === "open");
  const closed = desk.trades.filter((t) => t.status === "closed");

  if (!ready) {
    return <main className="min-h-screen bg-bg p-6 text-muted">Loading desk…</main>;
  }

  return (
    <main className="min-h-screen bg-bg text-fg">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div>
            <p className="font-mono text-xs tracking-widest text-brass">PAPER ONLY</p>
            <h1 className="text-xl font-semibold tracking-tight">ORH Desk</h1>
          </div>
          <dl className="flex flex-wrap gap-4 font-mono text-sm">
            <Stat label="Equity" value={money(equity)} tone={equity >= START_EQUITY ? "up" : "down"} />
            <Stat label="Open" value={String(open.length)} />
            <Stat label="Cash" value={money(desk.cash)} />
            <Stat label="Risk" value="0.20%" />
          </dl>
        </div>
        <nav className="mx-auto flex max-w-6xl gap-1 overflow-x-auto px-3 pb-2">
          {TABS.map((item) => {
            const Icon = item.icon;
            const active = tab === item.id;
            const badge = item.id === "alerts" ? unreadCount(desk.alerts) : 0;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setTab(item.id)}
                className={`flex min-h-11 items-center gap-2 rounded-md px-3 text-sm ${active ? "bg-brass text-brass-ink" : "text-muted hover:bg-raised hover:text-fg"}`}
              >
                <Icon size={16} />
                {item.label}
                {badge > 0 && (
                  <span className={`rounded-sm px-1.5 font-mono text-xs ${active ? "bg-brass-ink text-brass" : "bg-down text-fg"}`}>
                    {badge}
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </header>

      <div className="mx-auto max-w-6xl px-4 py-5">
        {tab === "desk" && <DeskHome equity={equity} open={open} onScan={() => setTab("scan")} onPaper={() => setTab("paper")} />}
        {tab === "scan" && (
          <Scanner
            filter={filter}
            setFilter={setFilter}
            onPick={(name) => {
              setTicket(name);
              setTab("paper");
            }}
          />
        )}
        {tab === "alerts" && <Alerts />}
        {tab === "paper" && <Paper ticket={ticket} clearTicket={() => setTicket(null)} />}
        {tab === "journal" && <Journal closed={closed} />}
        {tab === "rules" && <Rules />}
      </div>
    </main>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "up" | "down" }) {
  const color = tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-fg";
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className={color}>{value}</dd>
    </div>
  );
}

function DeskHome({ equity, open, onScan, onPaper }: { equity: number; open: Trade[]; onScan: () => void; onPaper: () => void }) {
  const pnl = equity - START_EQUITY;
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <section className="rounded-md border border-line bg-surface p-4 lg:col-span-2">
        <h2 className="text-lg font-semibold">How the book is organized</h2>
        <p className="mt-1 text-sm text-muted">Same order he uses. A name only moves right when the prior step is true.</p>
        <ol className="mt-4 grid gap-3 sm:grid-cols-2">
          {[
            ["1. Stalk list", "Today’s partial run named MBGL, BTG, and AAPL. He trades the leveraged ETF, so AAPL is AAPU and CRM is CRMG. Nothing here is a buy."],
            ["2. Buy alert", "Only if the hard checks pass: ORH, RVOL on a breakout, low of day inside 0.60 ATR, and not more than 4× ATR above the 50-day."],
            ["3. Paper", "You accept an alert and the desk sizes it at 0.20%. The 3-stop and the scale-out live here."],
            ["4. Broker, later", "Not connected. After the paper journal shows the R math, a paper brokerage account is the next step. Live orders come after that."],
          ].map(([title, body]) => (
            <li key={title} className="rounded-md bg-raised p-3">
              <p className="font-medium text-brass">{title}</p>
              <p className="text-sm text-muted">{body}</p>
            </li>
          ))}
        </ol>
        <div className="mt-4 flex flex-wrap gap-2">
          <button type="button" onClick={onScan} className="min-h-11 rounded-md bg-brass px-4 text-sm font-semibold text-brass-ink">Open stalk list</button>
          <button type="button" onClick={onPaper} className="min-h-11 rounded-md border border-line px-4 text-sm">Paper a name</button>
        </div>
      </section>
      <section className="rounded-md border border-line bg-surface p-4">
        <h2 className="text-lg font-semibold">Book</h2>
        <p className={`mt-2 font-mono text-2xl ${pnl >= 0 ? "text-up" : "text-down"}`}>{money(pnl)}</p>
        <p className="text-sm text-muted">Paper P&L from the starting $100,000.</p>
        <ul className="mt-3 space-y-2 text-sm">
          {open.length === 0 && <li className="text-muted">No open paper positions.</li>}
          {open.map((t) => (
            <li key={t.id} className="flex justify-between border-t border-line pt-2 font-mono">
              <span>{t.symbol}</span>
              <span className={rMultiple(t, markPrice(t.symbol) || t.entry) >= 0 ? "text-up" : "text-down"}>
                {rMultiple(t, markPrice(t.symbol) || t.entry).toFixed(2)}R
              </span>
            </li>
          ))}
        </ul>
      </section>
      <section className="rounded-md border border-line bg-surface p-4 lg:col-span-3">
        <h2 className="text-lg font-semibold">What to keep, in order</h2>
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          <Track title="Every entry" items={["Setup type", "Entry, stop, shares", "Risk % of equity", "RVOL, VCP, ATR extension"]} />
          <Track title="Every exit" items={["Reason: stop, 2R, day 3, 10-MA", "R-multiple on initial risk", "Partials taken", "One sentence of notes"]} />
          <Track title="Later, not now" items={["Expectancy by setup", "Broker paper account", "Then live orders", "Never before the journal is honest"]} />
        </div>
      </section>
    </div>
  );
}

function Track({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="rounded-md bg-raised p-3">
      <p className="font-medium">{title}</p>
      <ul className="mt-2 list-disc space-y-1 pl-4 text-sm text-muted">
        {items.map((item) => <li key={item}>{item}</li>)}
      </ul>
    </div>
  );
}

function Scanner({
  filter,
  setFilter,
  onPick,
}: {
  filter: ListKind | "all";
  setFilter: (id: ListKind | "all") => void;
  onPick: (name: ScanName) => void;
}) {
  const rows = NAMES.filter((n) => filter === "all" || n.lists.includes(filter) || (filter === "intraday" && n.lists.includes("reversion")));
  return (
    <section>
      <div className="mb-3 flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            onClick={() => setFilter(f.id)}
            className={`min-h-11 rounded-md px-3 text-sm ${filter === f.id ? "bg-brass text-brass-ink" : "bg-raised text-muted"}`}
          >
            {f.label}
          </button>
        ))}
      </div>
      <div className="overflow-x-auto rounded-md border border-line">
        <table className="w-full min-w-[720px] text-left text-sm">
          <thead className="bg-raised font-mono text-xs text-muted">
            <tr>
              {["Symbol", "Price", "Score", "VCP", "RVOL", "ADR", "ATR ext", "List", ""].map((h) => (
                <th key={h} className="px-3 py-2 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((n) => (
              <tr key={n.symbol} className="border-t border-line bg-surface">
                <td className="px-3 py-3 font-mono font-medium">{n.symbol}</td>
                <td className="px-3 py-3 font-mono">{n.price.toFixed(2)}</td>
                <td className="px-3 py-3 font-mono">{n.score.toFixed(2)}</td>
                <td className="px-3 py-3 font-mono">{n.vcp.toFixed(2)}</td>
                <td className="px-3 py-3 font-mono">{n.rvol.toFixed(2)}</td>
                <td className="px-3 py-3 font-mono">{n.adr.toFixed(1)}</td>
                <td className={`px-3 py-3 font-mono ${n.atrExt < 0 ? "text-down" : "text-fg"}`}>{n.atrExt.toFixed(1)}×</td>
                <td className="px-3 py-3 text-muted">{n.lists.join(", ")}</td>
                <td className="px-3 py-3">
                  <button type="button" onClick={() => onPick(n)} className="min-h-11 rounded-md bg-brass px-3 text-sm font-semibold text-brass-ink">
                    Paper
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-sm text-muted">Stalk is the 20 Oct 4:33 PM automation plus his leveraged vehicles. He does not enter AAPL or CRM. He enters AAPU and CRMG, which have the ADR the scan is built to keep. Scores of zero mean the run did not score them.</p>
    </section>
  );
}

function Alerts() {
  const alerts = useDesk((s) => s.alerts);
  const markRead = useDesk((s) => s.markRead);
  return (
    <ul className="space-y-3">
      {alerts.map((a) => (
        <li key={a.id} className={`rounded-md border border-line p-4 ${a.read ? "bg-bg" : "bg-surface"}`}>
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="font-medium">{a.symbol} · {a.title}</p>
            <p className="font-mono text-xs text-muted">{a.at}</p>
          </div>
          <p className="mt-1 text-sm text-muted">{a.detail}</p>
          {!a.read && (
            <button type="button" onClick={() => markRead(a.id)} className="mt-3 min-h-11 text-sm text-brass">
              Mark read
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

function Paper({ ticket, clearTicket }: { ticket: ScanName | null; clearTicket: () => void }) {
  const trades = useDesk((s) => s.trades);
  const openTrade = useDesk((s) => s.openTrade);
  const takePartial = useDesk((s) => s.takePartial);
  const hitStop = useDesk((s) => s.hitStop);
  const setStop = useDesk((s) => s.setStop);
  const closeTrade = useDesk((s) => s.closeTrade);
  const open = trades.filter((t) => t.status === "open");

  const preset = ticket ?? NAMES[7];
  const [symbol, setSymbol] = useState(preset.symbol);
  const [entry, setEntry] = useState(String(preset.price));
  const [stop, setStopPrice] = useState(String(+(preset.price - preset.atr * 0.5).toFixed(2)));
  const [setup, setSetup] = useState(preset.lists.includes("reversion") ? "Mean reversion" : "ORH / Focus");
  const [notes, setNotes] = useState(preset.note);
  const [msg, setMsg] = useState("");
  const [exitPx, setExitPx] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!ticket) return;
    setSymbol(ticket.symbol);
    setEntry(String(ticket.price));
    setStopPrice(String(+(ticket.price - Math.max(ticket.atr * 0.45, 0.2)).toFixed(2)));
    setSetup(ticket.lists.includes("reversion") ? "Mean reversion" : "ORH / Focus");
    setNotes(ticket.note);
    clearTicket();
  }, [ticket, clearTicket]);

  const name = NAMES.find((n) => n.symbol === symbol);

  return (
    <div className="grid gap-4 lg:grid-cols-5">
      <form
        className="space-y-3 rounded-md border border-line bg-surface p-4 lg:col-span-2"
        onSubmit={(e) => {
          e.preventDefault();
          const result = openTrade({
            symbol,
            setup,
            entry: Number(entry),
            stop: Number(stop),
            notes,
          });
          setMsg(result.ok ? `Opened ${symbol}.` : result.reason);
        }}
      >
        <h2 className="text-lg font-semibold">New paper trade</h2>
        <label className="block text-sm">
          Symbol
          <select value={symbol} onChange={(e) => {
            const next = NAMES.find((n) => n.symbol === e.target.value)!;
            setSymbol(next.symbol);
            setEntry(String(next.price));
            setStopPrice(String(+(next.price - Math.max(next.atr * 0.45, 0.2)).toFixed(2)));
            setNotes(next.note);
          }} className="mt-1 min-h-11 w-full rounded-md border border-line bg-bg px-3">
            {NAMES.map((n) => <option key={n.symbol}>{n.symbol}</option>)}
          </select>
        </label>
        <label className="block text-sm">
          Setup
          <input value={setup} onChange={(e) => setSetup(e.target.value)} className="mt-1 min-h-11 w-full rounded-md border border-line bg-bg px-3" />
        </label>
        <div className="grid grid-cols-2 gap-2">
          <label className="text-sm">
            Entry
            <input value={entry} onChange={(e) => setEntry(e.target.value)} inputMode="decimal" className="mt-1 min-h-11 w-full rounded-md border border-line bg-bg px-3 font-mono" />
          </label>
          <label className="text-sm">
            Stop
            <input value={stop} onChange={(e) => setStopPrice(e.target.value)} inputMode="decimal" className="mt-1 min-h-11 w-full rounded-md border border-line bg-bg px-3 font-mono" />
          </label>
        </div>
        <Checklist name={name} entry={Number(entry)} stop={Number(stop)} setup={setup} />
        {trades.filter((t) => localDateKey(new Date(t.opened)) === localDateKey()).length >= MAX_NEW_DAY && (
          <p className="text-sm text-brass">Three new names already today. His usual cap. The desk will still open another if you choose.</p>
        )}
        <label className="block text-sm">
          Note
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} className="mt-1 w-full rounded-md border border-line bg-bg px-3 py-2" />
        </label>
        <button type="submit" className="min-h-11 w-full rounded-md bg-brass font-semibold text-brass-ink">Size at 0.20% and open</button>
        {msg && <p className="text-sm text-brass">{msg}</p>}
      </form>

      <section className="space-y-3 lg:col-span-3">
        <h2 className="text-lg font-semibold">Open positions</h2>
        {open.length === 0 && <p className="text-sm text-muted">Nothing open. Paper a focus or reversion name from the scanner.</p>}
        {open.map((t) => {
          const mark = markPrice(t.symbol) || t.entry;
          const r = rMultiple(t, mark);
          return (
            <article key={t.id} className="rounded-md border border-line bg-surface p-4">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="font-mono text-lg">{t.symbol}</h3>
                <p className={`font-mono ${r >= 0 ? "text-up" : "text-down"}`}>{r.toFixed(2)}R · {t.remaining} sh</p>
              </div>
              <p className="text-sm text-muted">{t.setup} · entry {t.entry.toFixed(2)} · stop {t.stop.toFixed(2)} · mark {mark.toFixed(2)}</p>
              <p className="mt-1 font-mono text-xs text-muted">3-stop levels {stopLevels(t).map((x) => x.toFixed(2)).join(" / ")} · {t.stopsHit}/3 hit</p>
              {t.partials.length > 0 && <p className="mt-1 font-mono text-xs text-muted">{t.partials.join(" · ")}</p>}
              <div className="mt-3 flex flex-wrap gap-2">
                <button type="button" className="min-h-11 rounded-md bg-raised px-3 text-sm" onClick={() => takePartial(t.id, mark, 0.33, `Partial at ${r.toFixed(1)}R`)}>Take 1/3</button>
                <button type="button" className="min-h-11 rounded-md bg-raised px-3 text-sm" onClick={() => hitStop(t.id, Number(exitPx[t.id] ?? mark))}>Stop hit (1/3)</button>
                <button type="button" className="min-h-11 rounded-md bg-raised px-3 text-sm" onClick={() => setStop(t.id, t.entry, "Stop to breakeven")}>Stop to BE</button>
                <input
                  value={exitPx[t.id] ?? mark.toFixed(2)}
                  onChange={(e) => setExitPx({ ...exitPx, [t.id]: e.target.value })}
                  className="min-h-11 w-28 rounded-md border border-line bg-bg px-2 font-mono text-sm"
                  aria-label={`Exit price for ${t.symbol}`}
                />
                <button type="button" className="min-h-11 rounded-md bg-down px-3 text-sm text-fg" onClick={() => closeTrade(t.id, Number(exitPx[t.id] ?? mark), "Manual close")}>Close</button>
              </div>
            </article>
          );
        })}
      </section>
    </div>
  );
}

function Journal({ closed }: { closed: Trade[] }) {
  const reset = useDesk((s) => s.reset);
  const stats = useMemo(() => summarize(closed), [closed]);
  return (
    <section>
      <div className="mb-4 grid gap-3 sm:grid-cols-4">
        <Metric label="Closed" value={String(stats.n)} />
        <Metric label="Win rate" value={stats.n ? `${Math.round(stats.winRate * 100)}%` : "—"} />
        <Metric label="Avg R" value={stats.n ? stats.avgR.toFixed(2) : "—"} />
        <Metric label="Profit factor" value={stats.pf} />
      </div>
      {closed.length === 0 ? (
        <p className="text-sm text-muted">Closed paper trades land here with R measured off the original stop, not the moved stop.</p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-line">
          <table className="w-full min-w-[680px] text-left text-sm">
            <thead className="bg-raised font-mono text-xs text-muted">
              <tr>
                {["Symbol", "Setup", "Entry", "Exit", "R", "Reason", "Note"].map((h) => (
                  <th key={h} className="px-3 py-2 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {closed.map((t) => {
                const r = realizedR(t);
                return (
                  <tr key={t.id} className="border-t border-line bg-surface">
                    <td className="px-3 py-3 font-mono">{t.symbol}</td>
                    <td className="px-3 py-3">{t.setup}</td>
                    <td className="px-3 py-3 font-mono">{t.entry.toFixed(2)}</td>
                    <td className="px-3 py-3 font-mono">{(t.exit ?? 0).toFixed(2)}</td>
                    <td className={`px-3 py-3 font-mono ${r >= 0 ? "text-up" : "text-down"}`}>{r.toFixed(2)}</td>
                    <td className="px-3 py-3">{t.exitReason}</td>
                    <td className="px-3 py-3 text-muted">{t.notes}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <button type="button" onClick={reset} className="mt-4 inline-flex min-h-11 items-center gap-2 text-sm text-muted">
        <RotateCcw size={16} /> Reset paper book
      </button>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-line bg-surface p-3">
      <p className="text-xs text-muted">{label}</p>
      <p className="font-mono text-xl">{value}</p>
    </div>
  );
}

function summarize(closed: Trade[]) {
  if (!closed.length) return { n: 0, winRate: 0, avgR: 0, pf: "—" };
  const rs = closed.map(realizedR);
  const wins = rs.filter((r) => r > 0);
  const gp = closed.filter((t) => t.realized > 0).reduce((s, t) => s + t.realized, 0);
  const gl = Math.abs(closed.filter((t) => t.realized < 0).reduce((s, t) => s + t.realized, 0));
  return {
    n: closed.length,
    winRate: wins.length / rs.length,
    avgR: rs.reduce((a, b) => a + b, 0) / rs.length,
    pf: gl === 0 ? "∞" : (gp / gl).toFixed(2),
  };
}

function Checklist({ name, entry, stop, setup }: { name?: ScanName; entry: number; stop: number; setup: string }) {
  if (!name || !(entry > stop)) return null;
  const riskAtr = (entry - stop) / name.atr;
  const intraday = /intraday/i.test(setup);
  const reversion = /reversion|pullback/i.test(setup) || name.lists.includes("reversion");
  const rows: { label: string; ok: boolean; detail: string }[] = [
    { label: "VCP ≥ 0.55", ok: name.vcp >= 0.55, detail: name.vcp.toFixed(2) },
    { label: "RVOL", ok: reversion || name.rvol >= 1.5, detail: reversion ? "not required" : name.rvol.toFixed(2) },
    { label: "ADR prefer 5%+", ok: name.adr >= 5 || reversion, detail: `${name.adr.toFixed(1)}%` },
    {
      label: reversion ? "Stretch −1.5× to −4.5×" : "ATR ext ≤ 4×",
      ok: reversion ? name.atrExt <= -1.5 && name.atrExt >= -4.5 : name.atrExt <= 4,
      detail: `${name.atrExt.toFixed(1)}×`,
    },
    { label: "VARS ≥ 65", ok: reversion || name.vars >= 65, detail: name.vars.toFixed(0) },
    {
      label: intraday ? "Risk ≤ 0.40 ATR" : "Risk ≤ 0.60 ATR",
      ok: riskAtr <= (intraday ? 0.4 : 0.6) && riskAtr > 0,
      detail: `${riskAtr.toFixed(2)}× ATR`,
    },
  ];
  return (
    <ul className="space-y-1 rounded-md bg-bg p-2 font-mono text-xs">
      {rows.map((row) => (
        <li key={row.label} className="flex justify-between gap-3">
          <span className={row.ok ? "text-up" : "text-down"}>{row.ok ? "Pass" : "Fail"} · {row.label}</span>
          <span className="text-muted">{row.detail}</span>
        </li>
      ))}
    </ul>
  );
}

function Rules() {
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">Full process. A name is actionable only when the entry block passes. R is always the original entry minus the original stop.</p>
      <div className="grid gap-4 lg:grid-cols-2">
        <RuleCard title="1. Stalk list" items={[
          "Stage 2 and above the 20-day average.",
          "ADR at least about 2.8%.",
          "Score at least 0.40, or decent VARS.",
          "Purpose: watch for tightening. Not an entry.",
        ]} />
        <RuleCard title="2. Focus list" items={[
          "Stage 2, above the 10-day and the 20-day. Do not focus a name already more than 4× ATR above the 50-day.",
          "VCP first. Every lasting expansion he cares about was preceded by contraction.",
          "VARS confirming strength, and the name outperforming its market or sector.",
          "Higher ADR is better. About 5%+ improves the R math. Leveraged ETFs are the higher-ADR version of the same idea.",
          "A pocket-pivot day (strong up day on heightened volume) is a plus, not a trigger by itself.",
          "Prepare the level. Focus is not yet an order.",
        ]} />
        <RuleCard title="3. Momentum entry (ORH)" items={[
          "RVOL is required on breakouts and flags. He often wants above 1.5×, and much more on the reclaim bar. Some tight ideas have used about 20% of a full day’s volume inside the first 30 minutes.",
          "He usually waits the first 30 minutes unless RVOL is extreme. Later in the day the RVOL bar is higher: 40% of the day’s volume at two hours is not the same as 40% at 30 minutes.",
          "Preferred trigger is a reclaim of the opening-range high.",
          "Hard skip if the low of day is already more than 60% of ATR from the entry.",
          "Hard skip if ATR extension from the 50-day is already above 4×.",
          "Hard skip against a declining 200-day average.",
          "Hard skip into an immediate gap-resistance zone. A pre-market gap above the prior day’s high needs extra caution.",
          "Do not chase a missed move, and do not grind through resistance on light volume.",
        ]} />
        <RuleCard title="4. Risk, size, and the 3-stop" items={[
          "Risk 0.15% to 0.33% of equity. This desk uses 0.20%.",
          "Shares = (equity × risk %) / (entry − stop). No single name above about 28% of equity.",
          "Enter the full size, then defend it with three stops so a full failure is about −0.67R, not −1R.",
          "Stop 1: no confirmation in the first 1–2 hours. Sell about 33%. About 67% remains.",
          "Stop 2: breaks the intraday support you used. Sell another 33%. About 33% remains.",
          "Stop 3: breaks the real low-of-day or major support. Exit the last third.",
          "If two stops hit and price then reclaims the 30-minute high, that is a judgment call. His question to himself is whether to add back or stay with the remaining third.",
          "He usually opens no more than 3 new positions in a session. On 15 Oct 2026 he had 3 and was considering a 4th on a reversal day. This desk warns at 3 and does not block. Total open names can run past 8.",
        ]} />
        <RuleCard title="5. After the fill" items={[
          "T is the entry day. T+1: if it is acting right, you may add and raise the stop. If it cannot be held overnight without a cushion, reduce it.",
          "T+3 is the confirmation day, weekends excluded. If it is not working, reduce or exit. If it is leading and above breakeven, it can be added to.",
          "After T+3 the position is durable: trail, and do not tinker while it holds the 10-day average.",
          "A close under the 10-day is the exit signal. Next morning, use the first 5-minute low to tighten a breakeven stop.",
          "Sell some into strength. Scale about 20–30% at 4× ATR above the 50-day, another 20–30% at 6×, about 20% at 8×, and trail what is left past 10×.",
          "Indexes often use the 6–7× zone for the partial. A gap straight to about 10× usually fails to follow through.",
          "R is always measured from the original stop.",
          "The math he aims at with a win rate near 35% is an average loss near −0.67R and an average win near +4R. One good trade covers a long string of small losses.",
        ]} />
        <RuleCard title="6. Mean reversion (secondary)" items={[
          "Separate from the momentum focus list.",
          "Stretched about −1.5× to −4.5× ATR under the 50-day.",
          "RVOL is not required the way it is for a breakout. A tight ORH is still the entry he prefers.",
          "Same 3-stop and the same T+3 management once you are in.",
        ]} />
        <RuleCard title="7. Daily routine" items={[
          "After the close he runs many screens, about 14: CANSLIM-style growth, high ADR, extended bases, strongest movers, high short float, IPOs, liquid ETFs, plus mean reversion, mega-caps, and compression.",
          "Results go to a watchlist, then a tighter focus list. He marks VCP, VARS, and ATR extension, and he writes the size before the open.",
          "About 30 minutes before the open: news, focus alerts, pre-market gappers with RVOL, which sectors are leading, and the orders he is willing to place.",
          "Also note breadth and whether the sector itself is extended. He has asked whether XLK above 5× ATR from its 50-day should reduce risk.",
        ]} />
        <RuleCard title="8. Intraday reversal" items={[
          "Enter during the session, before the daily hammer is confirmed.",
          "Developing candle: lower wick at least about 55% of the range, body at most about 35%, small upper wick.",
          "Preferred entry is the opening-range high of that print.",
          "Stop at the session low. Distance must be at most 0.40 × ATR. Wider than that, skip.",
          "Label: Focus TICKER (Intraday Reversal).",
          "Best in the first one to two hours. The candle can still fail later.",
          "Best used on names already on the mean-reversion or focus list.",
        ]} />
        <RuleCard title="9. What the journal must store" items={[
          "Setup type, entry, original stop, shares, risk %.",
          "RVOL, VCP, ATR extension, and VARS at the decision.",
          "Each partial, and the exit reason: stop, 2R, day 3, 10-day, or manual.",
          "R-multiple and one sentence on why you took it.",
          "Do not connect a live broker until average R by setup is visible.",
        ]} />
      </div>
    </div>
  );
}

function RuleCard({ title, items }: { title: string; items: string[] }) {
  return (
    <article className="rounded-md border border-line bg-surface p-4">
      <h2 className="text-lg font-semibold">{title}</h2>
      <ul className="mt-3 list-disc space-y-2 pl-4 text-sm text-muted">
        {items.map((item) => <li key={item}>{item}</li>)}
      </ul>
    </article>
  );
}

function money(n: number) {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}
