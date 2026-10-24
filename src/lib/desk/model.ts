export const START_EQUITY = 100_000;
export const MAX_OPEN = 5;
export const MAX_NEW_DAY = 3;
export const DEFAULT_RISK = 0.002;

export type ListKind = "stalk" | "focus" | "reversion" | "intraday";

export type ScanName = {
  symbol: string;
  price: number;
  adr: number;
  rvol: number;
  vcp: number;
  atrExt: number;
  vars: number;
  score: number;
  mr: number;
  atr: number;
  lists: ListKind[];
  note: string;
};

export type DeskAlert = {
  id: string;
  at: string;
  symbol: string;
  title: string;
  detail: string;
  read: boolean;
};

export type Trade = {
  id: string;
  symbol: string;
  setup: string;
  entry: number;
  stop: number;
  initialStop: number;
  shares: number;
  initialShares: number; // never changes: R is measured on the original size and original stop
  stopsHit: number; // 0-3, the 3-stop tranches already sold
  remaining: number;
  riskPct: number;
  opened: string;
  notes: string;
  partials: string[];
  status: "open" | "closed";
  exit?: number;
  exitReason?: string;
  realized: number;
};

export const NAMES: ScanName[] = [
  { symbol: "MBGL", price: 18.52, adr: 0, rvol: 0, vcp: 0, atrExt: 0, vars: 0, score: 0, mr: 0, atr: 0.5, lists: ["stalk"], note: "20 Oct 4:33 PM scan, partial. Named in the run title only. Not scored." },
  { symbol: "BTG", price: 5.24, adr: 0, rvol: 0, vcp: 0, atrExt: 0, vars: 0, score: 0, mr: 0, atr: 0.2, lists: ["stalk"], note: "20 Oct 4:33 PM scan, partial. Named in the run title only. Not scored." },
  { symbol: "AAPU", price: 0, adr: 0, rvol: 0, vcp: 0, atrExt: 0, vars: 0, score: 0, mr: 0, atr: 1, lists: ["stalk"], note: "Leveraged AAPL. This is the name he would scan and trade, not AAPL. Not scored in today's partial run." },
  { symbol: "CRMG", price: 0, adr: 0, rvol: 0, vcp: 0, atrExt: 0, vars: 0, score: 0, mr: 0, atr: 1, lists: ["stalk"], note: "Leveraged CRM. Higher ADR than Salesforce, so it can clear a scan that drops the $200B stock. Not scored." },
  { symbol: "VEEV", price: 310.36, adr: 3.3, rvol: 1.0, vcp: 0.53, atrExt: 3.9, vars: 95, score: 0.583, mr: 0.12, atr: 8.4, lists: [], note: "18 Oct scan. Not in today's automation title." },
  { symbol: "MRNA", price: 218.01, adr: 8.3, rvol: 0.93, vcp: 0.62, atrExt: 3.4, vars: 86, score: 0.582, mr: 0.18, atr: 9.1, lists: ["focus"], note: "18 Oct focus candidate. Not in today's automation title. DATA CHECK: ATR/price is 4.2% but ADR is 8.3%; those cannot both be right (ATR% should be at least ADR%). Verify ATR before sizing. Also a biotech, which Jeff excludes for gap risk." },
  { symbol: "ANF", price: 153.26, adr: 3.5, rvol: 0.8, vcp: 0.51, atrExt: 3.9, vars: 93, score: 0.539, mr: 0.1, atr: 5.2, lists: [], note: "18 Oct scan. Not in today's automation title." },
  { symbol: "URBN", price: 86.57, adr: 3.0, rvol: 1.09, vcp: 0.47, atrExt: 3.1, vars: 75, score: 0.444, mr: 0.22, atr: 2.6, lists: [], note: "18 Oct scan. Not in today's automation title." },
  { symbol: "GTLB", price: 52.76, adr: 4.8, rvol: 0.7, vcp: 0.13, atrExt: 2.0, vars: 92, score: 0.428, mr: 0.2, atr: 2.4, lists: [], note: "18 Oct scan. Not in today's automation title." },
  { symbol: "AI", price: 11.32, adr: 5.0, rvol: 1.89, vcp: 0.09, atrExt: 1.0, vars: 99, score: 0.404, mr: 0.15, atr: 0.55, lists: [], note: "18 Oct scan. Not in today's automation title." },
  { symbol: "AEO", price: 17.98, adr: 3.7, rvol: 0.62, vcp: 0.71, atrExt: 2.0, vars: 63, score: 0.401, mr: 0.28, atr: 0.72, lists: ["focus"], note: "18 Oct focus candidate. Not in today's automation title." },
  { symbol: "GVA", price: 109.82, adr: 2.8, rvol: 2.79, vcp: 0.26, atrExt: -2.4, vars: 48, score: 0.31, mr: 0.58, atr: 3.1, lists: ["reversion"], note: "Best mean-reversion score. Watch for a tight ORH bounce." },
  { symbol: "LULU", price: 97.1, adr: 3.3, rvol: 0.63, vcp: 0.45, atrExt: -3.0, vars: 44, score: 0.28, mr: 0.52, atr: 3.4, lists: ["reversion"], note: "Deeper stretch under the 50-day. Needs stabilization." },
  { symbol: "NVDA", price: 214.13, adr: 2.1, rvol: 1.04, vcp: 0.29, atrExt: -1.5, vars: 52, score: 0.24, mr: 0.45, atr: 5.8, lists: ["reversion"], note: "Mild mega-cap pullback. Lower RVOL tolerance if risk is tiny." },
  { symbol: "DFEN", price: 48.02, adr: 4.8, rvol: 1.51, vcp: 0.47, atrExt: -5.6, vars: 40, score: 0.22, mr: 0.52, atr: 2.2, lists: ["reversion"], note: "Leveraged and more stretched than the ideal −1.5 to −4.5 band." },
];

export const SEED_ALERTS: DeskAlert[] = [
  {
    id: "a1",
    at: "16:35",
    symbol: "AEO",
    title: "Focus candidate",
    detail: "VCP 0.71, ATR extension 2.0×. Not actionable until RVOL and ORH clear.",
    read: false,
  },
  {
    id: "a2",
    at: "16:35",
    symbol: "MRNA",
    title: "Focus candidate",
    detail: "High ADR 8.3% with contraction. Still missing a volume trigger.",
    read: false,
  },
  {
    id: "a3",
    at: "10:12",
    symbol: "GVA",
    title: "Mean reversion watch",
    detail: "−2.4× ATR from the 50-day with elevated RVOL. Intraday reversal only if risk ≤ 0.40 ATR.",
    read: false,
  },
];

// Trading-day key in New York time (the old UTC key rolled over at 8pm ET).
export function localDateKey(d = new Date()) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(d);
}
export const todayKey = localDateKey;

export function rMultiple(trade: Trade, mark: number) {
  const risk = trade.entry - trade.initialStop;
  if (risk <= 0) return 0;
  return (mark - trade.entry) / risk;
}

// Realized R for a finished trade: net P&L over dollars risked at entry. Partials are included,
// so "bank 2R, then stopped at breakeven" is a win, not a zero.
export function realizedR(trade: Trade) {
  const denom = (trade.entry - trade.initialStop) * trade.initialShares;
  return denom > 0 ? trade.realized / denom : 0;
}

// 3-stop levels: a third out at 1/3 of the way to the full stop, a third at 2/3, the last at the full stop.
// Full failure = (0.33 + 0.67 + 1.0) / 3 = -0.67R.
export function stopLevels(trade: Trade) {
  const risk = trade.entry - trade.initialStop;
  return [0.33, 0.67, 1].map((f) => trade.entry - risk * f);
}

export function sizeShares(equity: number, entry: number, stop: number, riskPct: number) {
  const perShare = entry - stop;
  if (perShare <= 0 || entry <= 0) return 0;
  const byRisk = Math.floor((equity * riskPct) / perShare);
  const byCap = Math.floor((equity * 0.28) / entry);
  return Math.max(0, Math.min(byRisk, byCap));
}
