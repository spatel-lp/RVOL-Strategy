import { create } from "zustand";
import { persist } from "zustand/middleware";
import {
  DEFAULT_RISK,
  NAMES,
  SEED_ALERTS,
  START_EQUITY,
  type DeskAlert,
  type Trade,
} from "./model";

type State = {
  cash: number;
  trades: Trade[];
  alerts: DeskAlert[];
  riskPct: number;
  openTrade: (input: {
    symbol: string;
    setup: string;
    entry: number;
    stop: number;
    notes: string;
  }) => { ok: true } | { ok: false; reason: string };
  takePartial: (id: string, price: number, fraction: number, label: string) => void;
  hitStop: (id: string, price: number) => void;
  setStop: (id: string, stop: number, label: string) => void;
  closeTrade: (id: string, price: number, reason: string) => void;
  markRead: (id: string) => void;
  reset: () => void;
};

function fresh(): Pick<State, "cash" | "trades" | "alerts" | "riskPct"> {
  return {
    cash: START_EQUITY,
    trades: [],
    alerts: SEED_ALERTS,
    riskPct: DEFAULT_RISK,
  };
}

export const useDesk = create<State>()(
  persist(
    (set, get) => ({
      ...fresh(),
      openTrade: (input) => {
        const { trades, cash, riskPct } = get();
        const open = trades.filter((t) => t.status === "open");
        if (open.some((t) => t.symbol === input.symbol)) return { ok: false, reason: "Already open in this name." };
        const perShare = input.entry - input.stop;
        if (!(perShare > 0)) return { ok: false, reason: "Stop must sit below entry." };
        const equity = markEquity(cash, trades);
        const byRisk = Math.floor((equity * riskPct) / perShare);
        const byCap = Math.floor((equity * 0.28) / input.entry);
        const shares = Math.max(0, Math.min(byRisk, byCap));
        const cost = shares * input.entry;
        if (shares < 1 || cost > cash) return { ok: false, reason: "Size is zero or cash is short." };
        const trade: Trade = {
          id: `${input.symbol}-${Date.now()}`,
          symbol: input.symbol,
          setup: input.setup,
          entry: input.entry,
          stop: input.stop,
          initialStop: input.stop,
          shares,
          initialShares: shares,
          stopsHit: 0,
          remaining: shares,
          riskPct,
          opened: new Date().toISOString(),
          notes: input.notes,
          partials: [],
          status: "open",
          realized: 0,
        };
        set({ cash: cash - cost, trades: [trade, ...trades] });
        return { ok: true };
      },
      takePartial: (id, price, fraction, label) => {
        const trade = get().trades.find((t) => t.id === id && t.status === "open");
        if (!trade) return;
        const sell = Math.floor(trade.remaining * fraction);
        if (sell < 1) return;
        const realized = trade.realized + (price - trade.entry) * sell;
        set({
          cash: get().cash + sell * price,
          trades: get().trades.map((t) =>
            t.id === id
              ? { ...t, remaining: t.remaining - sell, realized, partials: [...t.partials, `${label} ${sell} @ ${price.toFixed(2)}`] }
              : t,
          ),
        });
      },
      // 3-stop: each hit sells one third of the ORIGINAL size at the price given; the third hit exits the rest.
      hitStop: (id, price) => {
        const trade = get().trades.find((t) => t.id === id && t.status === "open");
        if (!trade) return;
        const third = Math.max(1, Math.round(trade.initialShares / 3));
        const stopsHit = trade.stopsHit + 1;
        const sell = stopsHit >= 3 ? trade.remaining : Math.min(trade.remaining, third);
        const remaining = trade.remaining - sell;
        const done = remaining <= 0 || stopsHit >= 3;
        const realized = trade.realized + (price - trade.entry) * sell;
        set({
          cash: get().cash + sell * price,
          trades: get().trades.map((t) =>
            t.id === id
              ? {
                  ...t,
                  remaining,
                  realized,
                  stopsHit,
                  partials: [...t.partials, `Stop ${stopsHit} ${sell} @ ${price.toFixed(2)}`],
                  ...(done ? { status: "closed" as const, exit: price, exitReason: "3-stop", remaining: 0 } : {}),
                }
              : t,
          ),
        });
      },
      setStop: (id, stop, label) => {
        set({
          trades: get().trades.map((t) =>
            t.id === id && t.status === "open"
              ? { ...t, stop, partials: [...t.partials, label] }
              : t,
          ),
        });
      },
      closeTrade: (id, price, reason) => {
        const trade = get().trades.find((t) => t.id === id && t.status === "open");
        if (!trade) return;
        const realized = trade.realized + (price - trade.entry) * trade.remaining;
        set({
          cash: get().cash + trade.remaining * price,
          trades: get().trades.map((t) =>
            t.id === id
              ? { ...t, status: "closed", remaining: 0, exit: price, exitReason: reason, realized }
              : t,
          ),
        });
      },
      markRead: (id) => {
        set({ alerts: get().alerts.map((a) => (a.id === id ? { ...a, read: true } : a)) });
      },
      reset: () => set(fresh()),
    }),
    {
      name: "orh-desk-v1",
      version: 2,
      // v1 trades had no initialShares/stopsHit; fill them in so R and the 3-stop work on old paper books.
      migrate: (persisted: unknown) => {
        const state = persisted as { trades?: Array<Record<string, unknown>> };
        if (state?.trades) {
          state.trades = state.trades.map((t) => ({
            ...t,
            initialShares: (t.initialShares as number | undefined) ?? (t.shares as number),
            stopsHit: (t.stopsHit as number | undefined) ?? 0,
          }));
        }
        return state as never;
      },
    },
  ),
);

export function markPrice(symbol: string) {
  return NAMES.find((n) => n.symbol === symbol)?.price ?? 0;
}

export function markEquity(cash: number, trades: Trade[]) {
  const open = trades.filter((t) => t.status === "open");
  return cash + open.reduce((sum, t) => sum + t.remaining * (markPrice(t.symbol) || t.entry), 0);
}

export function unreadCount(alerts: DeskAlert[]) {
  return alerts.filter((a) => !a.read).length;
}
