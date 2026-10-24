"""
Static dashboard: renders paper_state.json into ONE self-contained HTML file (no CDN, no network).
Open docs/index.html locally, or serve the docs/ folder (e.g. GitHub Pages).
"""

import json
import os
from datetime import datetime

from config import (
    EARNINGS_WINDOW_DAYS, USE_REGIME_GATE,
    ATR_EXT_PARTIAL_LEVELS, LOD_ATR_MAX, MAX_ATR_EXT_50, MAX_DAILY_NEW_TRADES, MAX_OPEN_POSITIONS,
    ORH_MAX_LOD_ATR, ORH_MIN_DAYVOL_PCT, PAPER_LAST_ENTRY_ET, RISK_PER_TRADE, THREE_STOP_LEVELS,
)
import signals
from paper import closed_view, docs_dir, positions_view, risk_view, summarize


def build_payload(state: dict, demo: bool = False) -> dict:
    scan = state.get("scan", {})
    return {
        "demo": demo,
        "summary": summarize(state),
        "initial_equity": state.get("initial_equity"),
        "positions": positions_view(state),
        "closed": closed_view(state),
        "curve": state.get("equity_curve", []),
        "scan": {
            "date": scan.get("date"), "asof": scan.get("asof"), "n_universe": scan.get("n_universe"),
            "market": scan.get("market", []),
            "focus": scan.get("focus", []), "stalk": scan.get("stalk", []), "mr": scan.get("mr", []),
        },
        "intraday": state.get("intraday", {}),
        "regime": state.get("regime", {}),
        "broker": state.get("broker", {}),
        "risk": risk_view(state),
        "audit": signals.audit(state),
        "datacheck": state.get("datacheck", {}),
        "universe": state.get("universe", {}),
        "exit_lab": state.get("exit_lab", {}),
        "events": list(reversed(state.get("events", [])))[:80],
        "alerts": state.get("alerts", []),
        "last_run": state.get("last_run", {}),
        "updated": state.get("updated"),
        "rules": {
            "risk_pct": RISK_PER_TRADE * 100, "max_open": MAX_OPEN_POSITIONS, "max_new_day": MAX_DAILY_NEW_TRADES,
            "max_ext": MAX_ATR_EXT_50, "trim_levels": ATR_EXT_PARTIAL_LEVELS, "lod_atr": min(LOD_ATR_MAX, ORH_MAX_LOD_ATR),
            "dayvol_pct": ORH_MIN_DAYVOL_PCT * 100,
            "earn_days": EARNINGS_WINDOW_DAYS, "use_regime": USE_REGIME_GATE, "stops": list(THREE_STOP_LEVELS),
            "last_entry": "%02d:%02d ET" % PAPER_LAST_ENTRY_ET,
        },
    }


def render_html(state: dict, demo: bool = False) -> str:
    payload = json.dumps(build_payload(state, demo), separators=(",", ":"), allow_nan=False)
    # keep the JSON from ever closing the <script> element
    payload = payload.replace("</", "<\\/").replace("<!--", "<\\!--")
    return HTML.replace("__DATA__", payload)


def write_dashboard(state: dict, demo: bool = False, path: str = None) -> str:
    path = path or os.path.join(docs_dir(), "index.html")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_html(state, demo))
    return path


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>ORH Desk · Paper Trading</title>
<style>
:root{
  --bg:#f6f7f9; --card:#ffffff; --ink:#14181f; --muted:#5b6573; --line:#e3e6eb;
  --up:#0b7a43; --down:#c22a2a; --accent:#2457d6; --warn-bg:#fff4d6; --warn-ink:#6b4a00; --chip:#eef1f6;
}
@media (prefers-color-scheme: dark){
  :root{ --bg:#0e1116; --card:#171b22; --ink:#e9edf3; --muted:#9aa5b4; --line:#262c36;
         --up:#3ccf84; --down:#ff6b6b; --accent:#7aa2ff; --warn-bg:#3a2f0b; --warn-ink:#ffd98a; --chip:#222834; }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  padding:env(safe-area-inset-top,0) 0 env(safe-area-inset-bottom,0)}
.wrap{max-width:1100px;margin:0 auto;padding:16px}
header{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline;justify-content:space-between;margin-bottom:12px}
h1{font-size:20px;margin:0}
h2{font-size:15px;margin:0 0 10px;letter-spacing:.2px}
.sub{color:var(--muted);font-size:13px}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;background:var(--chip);font-size:12px;color:var(--muted)}
.pill.ok{color:var(--up)} .pill.bad{color:var(--down)}
.banner{background:var(--warn-bg);color:var(--warn-ink);border-radius:10px;padding:10px 12px;margin:0 0 12px;font-size:14px}
.banner.demo{background:var(--accent);color:#fff;font-weight:600}
.grid{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(138px,1fr));margin-bottom:12px}
.kpi,.card{background:var(--card);border:1px solid var(--line);border-radius:12px}
.kpi{padding:10px 12px}
.kpi .l{color:var(--muted);font-size:12px}
.kpi .v{font-size:21px;font-weight:650;font-variant-numeric:tabular-nums}
.card{padding:14px;margin-bottom:12px}
.up{color:var(--up)} .down{color:var(--down)} .muted{color:var(--muted)}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:14px}
th,td{padding:7px 8px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child,th.l,td.l{text-align:left}
th{color:var(--muted);font-weight:600;font-size:12px}
tr:last-child td{border-bottom:0}
.sym{font-weight:650}
.empty{color:var(--muted);padding:6px 0}
details{margin:0 0 12px}
summary{cursor:pointer;font-weight:600;padding:10px 14px;background:var(--card);border:1px solid var(--line);border-radius:12px}
details[open] summary{border-bottom-left-radius:0;border-bottom-right-radius:0}
details .body{background:var(--card);border:1px solid var(--line);border-top:0;border-radius:0 0 12px 12px;padding:12px 14px}
.log{max-height:300px;overflow:auto;font-size:13px}
.log div{padding:4px 0;border-bottom:1px solid var(--line)}
.log .t{color:var(--muted);margin-right:8px}
svg{width:100%;height:auto;display:block}
.chip{display:inline-block;background:var(--chip);border-radius:6px;padding:1px 6px;margin:0 4px 2px 0;font-size:12px}
ul.rules{margin:0;padding-left:18px}
.bar{height:8px;background:var(--chip);border-radius:4px;overflow:hidden;margin-top:3px}
.bar>i{display:block;height:100%;background:var(--accent)}
.bar.warn>i{background:var(--down)}
.meters{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(250px,1fr))}
.meter .l{display:flex;justify-content:space-between;font-size:13px;color:var(--muted)}
.meter .v{font-weight:600;color:var(--ink)}
.note{font-size:13px;color:var(--muted);margin:8px 0 0}
footer{color:var(--muted);font-size:12px;margin:18px 0 8px}
@media (max-width:600px){
  table.stack thead{display:none}
  table.stack tr{display:grid;grid-template-columns:repeat(2,1fr);gap:2px 12px;padding:10px 0;border-bottom:1px solid var(--line)}
  table.stack td{border:0;padding:2px 0;text-align:left;white-space:normal}
  table.stack td::before{content:attr(data-label);display:block;color:var(--muted);font-size:11px}
  table.stack td:first-child{grid-column:1/-1;font-size:16px}
  table.stack td:first-child::before{content:none}
}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div><h1>ORH Desk &middot; Paper Trading</h1><div class="sub" id="sub"></div></div>
    <div id="status"></div>
  </header>
  <div id="banners"></div>
  <div class="grid" id="kpis"></div>
  <div class="card"><h2>Equity curve</h2><div id="curve"></div></div>
  <div class="card"><h2>Market regime <span class="sub">&middot; gates every new entry</span></h2><div id="regime"></div></div>
  <div class="card"><h2>Risk controls</h2><div id="risk"></div></div>
  <div class="card"><h2>Evidence <span class="sub">&middot; how much do the closed trades prove?</span></h2><div id="evidence"></div></div>
  <div class="card"><h2>Open positions</h2><div class="scroll" id="positions"></div></div>
  <div class="card"><h2>Focus list <span class="sub" id="focusSub"></span></h2><div class="scroll" id="focus"></div></div>
  <details><summary>Stalk list</summary><div class="body scroll" id="stalk"></div></details>
  <details><summary>Mean-reversion list</summary><div class="body scroll" id="mr"></div></details>
  <div class="card"><h2>Closed trades</h2><div class="scroll" id="closed"></div></div>
  <details><summary>Filter audit: what rejected signals did next</summary><div class="body scroll" id="audit"></div></details>
  <details><summary>Exit-variant lab</summary><div class="body scroll" id="lab"></div></details>
  <details><summary>Alpaca paper mirror</summary><div class="body" id="broker"></div></details>
  <details><summary>Data health &amp; universe</summary><div class="body" id="health"></div></details>
  <div class="card"><h2>Activity</h2><div class="log" id="events"></div></div>
  <details><summary>Rules &amp; limits in force</summary><div class="body" id="rules"></div></details>
  <footer>Paper trading simulation. Not financial advice. Prices come from Yahoo Finance via yfinance (unofficial, can lag or have gaps). Fills are simulated, not executed.</footer>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
(function(){
const D = JSON.parse(document.getElementById('data').textContent);
const $ = s => document.querySelector(s);
const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const usd = n => n == null ? '\u2013' : (n < 0 ? '-' : '') + '$' + Math.abs(n).toLocaleString('en-US', {maximumFractionDigits: 0});
const num = (n, d) => n == null ? '\u2013' : Number(n).toFixed(d == null ? 2 : d);
const pct = (n, d) => n == null ? '\u2013' : (n >= 0 ? '+' : '') + Number(n).toFixed(d == null ? 1 : d) + '%';
const cls = n => n > 0 ? 'up' : (n < 0 ? 'down' : '');
const rr = n => n == null ? '\u2013' : (n >= 0 ? '+' : '') + Number(n).toFixed(2) + 'R';
const S = D.summary;

// ---- header / status
const daysOld = D.scan.date ? Math.floor((Date.now() - new Date(D.scan.date + 'T12:00:00').getTime()) / 86400000) : null;
$('#sub').textContent = D.updated ? 'Updated ' + D.updated.replace('T', ' ').replace('Z', ' UTC') : 'No run yet';
$('#status').innerHTML =
  '<span class="pill ' + (daysOld != null && daysOld <= 4 ? 'ok' : 'bad') + '">Last scan: ' + esc(D.scan.date || 'never') + '</span> ' +
  '<span class="pill">Universe: ' + esc(D.scan.n_universe || 0) + '</span> ' +
  '<span class="pill">Intraday: ' + esc((D.last_run.intraday || 'never').replace('T', ' ').slice(0, 16)) + '</span>';

// ---- banners
let b = '';
if (D.demo) b += '<div class="banner demo">DEMO DATA: synthetic prices and trades, only here to show the layout. Not a real scan.</div>';
if (daysOld != null && daysOld > 4) b += '<div class="banner">The last scan is ' + daysOld + ' days old. Check that the scheduled job is still running.</div>';
if (!D.scan.date) b += '<div class="banner">No scan has run yet. Run <code>python run_daily.py scan --force</code> once, or wait for the scheduled job.</div>';
(D.alerts || []).forEach(a => { b += '<div class="banner">' + esc(a) + '</div>'; });
$('#banners').innerHTML = b;

// ---- KPIs
const k = (l, v, c) => '<div class="kpi"><div class="l">' + l + '</div><div class="v ' + (c || '') + '">' + v + '</div></div>';
$('#kpis').innerHTML =
  k('Equity', usd(S.equity)) + k('Return', pct(S.return_pct, 2), cls(S.return_pct)) +
  k('Open risk', num(S.open_risk_pct, 2) + '%') + k('Open / max', S.open_positions + ' / ' + D.rules.max_open) +
  k('Win rate', S.win_rate == null ? '\u2013' : Math.round(S.win_rate * 100) + '% <span class="sub">of ' + S.closed_trades + '</span>') +
  k('Avg R', rr(S.avg_r), cls(S.avg_r)) + k('Profit factor', S.profit_factor == null ? '\u2013' : (S.profit_factor >= 999 ? '\u221e' : num(S.profit_factor, 2))) +
  k('Max drawdown', num(S.max_drawdown_pct, 1) + '%', cls(S.max_drawdown_pct)) + k('Closed trades', S.closed_trades);

// ---- equity curve (inline SVG)
(function(){
  const c = D.curve || [];
  if (c.length < 2) { $('#curve').innerHTML = '<div class="empty">The curve starts after the second end-of-day close.</div>'; return; }
  const W = 640, H = 240, P = {l: 76, r: 10, t: 10, b: 28};
  const ys = c.map(p => p.equity), base = D.initial_equity;
  let lo = Math.min.apply(null, ys.concat([base])), hi = Math.max.apply(null, ys.concat([base]));
  if (hi === lo) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const X = i => P.l + (W - P.l - P.r) * i / (c.length - 1);
  const Y = v => P.t + (H - P.t - P.b) * (1 - (v - lo) / (hi - lo));
  const line = c.map((p, i) => (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(p.equity).toFixed(1)).join(' ');
  const area = line + ' L' + X(c.length - 1).toFixed(1) + ' ' + (H - P.b) + ' L' + P.l + ' ' + (H - P.b) + ' Z';
  const ticks = [lo + pad, (lo + hi) / 2, hi - pad].map(v => '<text x="' + (P.l - 6) + '" y="' + (Y(v) + 4).toFixed(1) +
    '" text-anchor="end" font-size="15" fill="var(--muted)">' + usd(v) + '</text>').join('');
  $('#curve').innerHTML = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Equity curve">' +
    '<line x1="' + P.l + '" x2="' + (W - P.r) + '" y1="' + Y(base).toFixed(1) + '" y2="' + Y(base).toFixed(1) + '" stroke="var(--line)" stroke-dasharray="4 4"/>' +
    '<path d="' + area + '" fill="var(--accent)" opacity=".12"/><path d="' + line + '" fill="none" stroke="var(--accent)" stroke-width="2"/>' +
    ticks + '<text x="' + P.l + '" y="' + (H - 6) + '" font-size="15" fill="var(--muted)">' + esc(c[0].date) + '</text>' +
    '<text x="' + (W - P.r) + '" y="' + (H - 6) + '" text-anchor="end" font-size="15" fill="var(--muted)">' + esc(c[c.length - 1].date) + '</text></svg>';
})();

// ---- table helper
function table(cols, rows, empty, stack) {
  if (!rows.length) return '<div class="empty">' + esc(empty) + '</div>';
  return '<table class="' + (stack ? 'stack' : '') + '"><thead><tr>' + cols.map(c => '<th class="' + (c.l ? 'l' : '') + '">' + c.h + '</th>').join('') + '</tr></thead><tbody>' +
    rows.map(r => '<tr>' + cols.map(c => '<td data-label="' + esc(c.h.replace(/&amp;/g, '&').replace(/&times;/g, '\u00d7')) + '" class="' + (c.l ? 'l ' : '') + (c.c ? c.c(r) : '') + '">' + c.f(r) + '</td>').join('') + '</tr>').join('') + '</tbody></table>';
}

// ---- open positions
$('#positions').innerHTML = table([
  {h: 'Symbol', f: r => '<span class="sym">' + esc(r.symbol) + '</span>', l: 1},
  {h: 'Industry', f: r => esc(r.industry) + (r.lev > 1 ? ' <span class="chip">' + r.lev + 'x</span>' : ''), l: 1},
  {h: 'Day', f: r => r.day}, {h: 'Last', f: r => num(r.last)},
  {h: 'R', f: r => rr(r.r_now), c: r => cls(r.r_now)},
  {h: 'P&amp;L', f: r => usd(r.pnl), c: r => cls(r.pnl)},
  {h: 'Shares', f: r => r.remaining + ' / ' + r.size},
  {h: 'Stops', f: r => r.stops.map(s => '<span class="chip">' + num(s[0]) + ' &times; ' + s[1] + '</span>').join(''), l: 1},
  {h: 'Last note', f: r => esc(r.last_note), l: 1}
], D.positions, 'No open positions.', true);

// ---- focus list with the latest ORH verdicts
const chk = {}; ((D.intraday || {}).checked || []).forEach(x => { chk[x.symbol] = x; });
$('#focusSub').textContent = D.intraday && D.intraday.asof ? '\u00b7 ORH check ' + D.intraday.asof.replace('T', ' ').slice(0, 16) + ' ET' : '';
const listCols = [
  {h: 'Symbol', f: r => '<span class="sym">' + esc(r.symbol) + '</span>' + (r.LEV ? ' <span class="chip">LEV</span>' : ''), l: 1},
  {h: 'Score', f: r => num(r.score, 2)}, {h: 'Close', f: r => num(r.close)}, {h: 'VCP', f: r => num(r.VCP, 2)},
  {h: 'Ext&times;', f: r => num(r.ATR_ext, 1)}, {h: 'ADR%', f: r => num(r['ADR%'], 1)},
  {h: 'RVOL', f: r => num(r.RVOL, 2)}, {h: 'VARS', f: r => num(r.VARS, 0)}
];
$('#focus').innerHTML = table(listCols.concat([
  {h: 'Next earnings', f: r => r.LEV ? '\u2013' : (r.earn ? esc(r.earn) : '<span class="muted">none known</span>')},
  {h: 'ORH verdict', f: r => { const x = chk[r.symbol]; return x ? (x.passed ? '<span class="up">ready</span> ' : '') + esc(x.reason) : '\u2013'; }, l: 1},
  {h: 'Data', f: r => r.flag ? '<span class="down">' + esc(r.flag) + '</span>' : '', l: 1}
]), D.scan.focus, 'Empty: nothing meets every Focus rule today.');
$('#stalk').innerHTML = table(listCols, D.scan.stalk, 'Empty.');
$('#mr').innerHTML = table(listCols.slice(0, 5).concat([{h: 'MR', f: r => num(r.MR, 2)}]), D.scan.mr, 'Empty.');

// ---- closed trades
$('#closed').innerHTML = table([
  {h: 'Symbol', f: r => '<span class="sym">' + esc(r.symbol) + '</span>', l: 1},
  {h: 'In', f: r => esc(r.entry_date)}, {h: 'Out', f: r => esc(r.exit_date)},
  {h: 'Entry', f: r => num(r.entry)}, {h: 'Exit', f: r => num(r.exit)},
  {h: 'R', f: r => rr(r.r), c: r => cls(r.r)}, {h: 'Best', f: r => rr(r.mfe)}, {h: 'Worst', f: r => rr(r.mae)}, {h: 'P&amp;L', f: r => usd(r.pnl), c: r => cls(r.pnl)},
  {h: 'Reason', f: r => esc(r.note), l: 1}
], D.closed, 'No closed trades yet.', true);

// ---- risk controls
(function(){
  const R = D.risk || {};
  const meter = (label, val, cap, text, warn) => '<div class="meter"><div class="l"><span>' + label + '</span><span class="v">' + text + '</span></div>' +
    '<div class="bar ' + (warn ? 'warn' : '') + '"><i style="width:' + Math.max(0, Math.min(100, cap ? val / cap * 100 : 0)).toFixed(0) + '%"></i></div></div>';
  let h = '<div class="meters">' +
    meter('Drawdown from peak', R.dd_pct, 10, num(R.dd_pct, 1) + '% · risk x' + num(R.mult, 2), R.dd_pct >= 3) +
    meter('Today vs daily loss limit', Math.max(0, R.day_loss_pct), 0.5, num(R.day_loss_pct, 2) + '% / 0.5%', R.day_loss_pct >= 0.5) +
    meter('Open risk', R.open_risk_pct, R.open_risk_cap_pct, num(R.open_risk_pct, 2) + '% / ' + num(R.open_risk_cap_pct, 1) + '%', R.open_risk_pct >= R.open_risk_cap_pct) +
    meter('Leveraged exposure', R.lev_exposure_pct, R.lev_cap_pct, num(R.lev_exposure_pct, 0) + '% / ' + num(R.lev_cap_pct, 0) + '%', R.lev_exposure_pct >= R.lev_cap_pct) + '</div>';
  const inds = Object.keys(R.industries || {}).map(k => '<span class="chip">' + esc(k) + ' ' + R.industries[k] + '/' + R.max_industry + '</span>').join('');
  h += '<p class="note">Positions per industry (cap ' + R.max_industry + '): ' + (inds || 'none') + '</p>';
  const cp = (R.corr_pairs || []).map(p => '<span class="chip">' + esc(p.a) + ' / ' + esc(p.b) + ' ' + num(p.corr, 2) + '</span>').join('');
  h += '<p class="note">Open positions that trade alike (60-day return correlation of 0.70 or more): ' + (cp || 'none') + '</p>';
  if (R.reasons && R.reasons.length) h += '<div class="banner" style="margin-top:10px">' + R.reasons.map(esc).join(' &middot; ') + '</div>';
  $('#risk').innerHTML = h;
})();

// ---- evidence
(function(){
  const E = S.evidence || {n: 0, target: 100};
  let h = '<div class="meter"><div class="l"><span>Closed trades</span><span class="v">' + E.n + ' / ' + E.target + '</span></div>' +
    '<div class="bar"><i style="width:' + (E.progress_pct || 0) + '%"></i></div></div>';
  if (E.ci_low == null) {
    h += '<p class="note">Fewer than 8 closed trades: not enough for a confidence range. Do not tune any rule yet.</p>';
  } else {
    h += '<p class="note">Mean <b>' + rr(E.mean_r) + '</b> per trade, 95% bootstrap range <b>' + rr(E.ci_low) + '</b> to <b>' + rr(E.ci_high) +
      '</b>. Chance the true expectancy is above zero: <b>' + Math.round(E.p_positive * 100) + '%</b>.' +
      (E.n < E.target ? ' Still below ' + E.target + ' trades, so treat this as a rough guide.' : '') + '</p>';
  }
  h += '<p class="note">Average best point reached <b>' + rr(S.avg_mfe_r) + '</b>, worst point <b>' + rr(S.avg_mae_r) + '</b>; ' +
    'share of the best point actually captured <b>' + (S.capture_ratio == null ? '\u2013' : Math.round(S.capture_ratio * 100) + '%') + '</b>.</p>';
  $('#evidence').innerHTML = h;
})();

// ---- filter audit
$('#audit').innerHTML = table([
  {h: 'Kind', f: r => esc(r.kind), l: 1}, {h: 'Reason', f: r => esc(r.reason), l: 1}, {h: 'N', f: r => r.n}, {h: 'With 5d result', f: r => r.resolved},
  {h: 'Avg 5d %', f: r => r.avg_fwd_pct == null ? '\u2013' : pct(r.avg_fwd_pct, 2), c: r => cls(r.avg_fwd_pct)},
  {h: 'Median %', f: r => r.median_fwd_pct == null ? '\u2013' : pct(r.median_fwd_pct, 2)},
  {h: 'Up %', f: r => r.win_pct == null ? '\u2013' : r.win_pct + '%'},
  {h: 'Best (ATR)', f: r => num(r.avg_mfe_atr, 1)}, {h: 'Worst (ATR)', f: r => num(r.avg_mae_atr, 1)},
  {h: 'Verdict', f: r => esc(r.verdict), l: 1}
], D.audit || [], 'No signals logged yet.') +
  '<p class="note">Close-to-close moves after the signal, with no stops or trims. A filter that rejects names which then rise is costing winners; one that rejects names which then fall is protecting you. No verdict until 30 resolved signals per reason.</p>';

// ---- exit lab
(function(){
  const L = D.exit_lab || {};
  if (!L.rows || !L.rows.length) { $('#lab').innerHTML = '<div class="empty">Not run yet. <code>python exit_lab.py</code> replays the same signals under each exit rule.</div>'; return; }
  $('#lab').innerHTML = '<p class="note">As of ' + esc(L.asof) + ' · ' + esc(L.note || '') + '</p>' + table([
    {h: 'Variant', f: r => esc(r.variant), l: 1}, {h: 'Trades', f: r => r.trades}, {h: 'Win %', f: r => Math.round(r.win_rate * 100) + '%'},
    {h: 'Avg R', f: r => rr(r.avg_r), c: r => cls(r.avg_r)}, {h: 'Profit factor', f: r => r.profit_factor >= 999 ? '\u221e' : num(r.profit_factor, 2)},
    {h: 'Return', f: r => pct(r.return_pct, 1), c: r => cls(r.return_pct)}, {h: 'Max DD', f: r => num(r.max_drawdown_pct, 1) + '%'},
    {h: 'Capture', f: r => r.capture_ratio == null ? '\u2013' : Math.round(r.capture_ratio * 100) + '%'}
  ], L.rows, '');
})();

// ---- data health + universe
(function(){
  const U = D.universe || {}, H = D.datacheck || {}, C = H.crosscheck;
  let h = '<p><b>Universe:</b> ' + esc(U.source || 'unknown') + (U.date ? ' snapshot ' + esc(U.date) : '') + ' · ' + esc(U.n || 0) + ' names · ' + esc(U.note || '') + '</p>';
  const sk = Object.keys(H.sanity || {});
  h += '<p><b>Sanity checks:</b> ' + (sk.length ? sk.map(k => esc(k) + ': ' + H.sanity[k].count + ' (' + H.sanity[k].examples.slice(0, 3).map(esc).join(', ') + ')').join(' · ') : 'all clear') + '</p>';
  h += '<p><b>Second-source cross-check (Stooq):</b> ' + (C ? C.checked + ' compared, ' + C.ok + ' within tolerance, ' + (C.mismatched || []).length + ' mismatched, ' + (C.unavailable || []).length + ' unavailable' +
    ' · last run ' + esc(H.last_crosscheck || '') : 'not run yet') + '</p>';
  (C && C.mismatched || []).forEach(m => { h += '<p class="down">' + esc(m.symbol) + ': Yahoo ' + m.yahoo + ' vs Stooq ' + m.stooq + ' (' + m.diff_pct + '% on ' + esc(m.date) + ')</p>'; });
  $('#health').innerHTML = h;
})();

// ---- market regime
(function(){
  const G = D.regime || {};
  if (!G.label) { $('#regime').innerHTML = '<div class="empty">No reading yet: the first scan sets it.</div>'; return; }
  const tone = G.label === 'risk-off' ? 'down' : (G.label === 'risk-on' ? 'up' : '');
  let h = '<p style="margin:0 0 6px"><span class="pill ' + (G.label === 'risk-off' ? 'bad' : (G.label === 'risk-on' ? 'ok' : '')) + '">' + esc(G.label) + '</span> ' +
    '<b class="' + tone + '">new-entry risk x' + num(G.mult, 2) + '</b>' + (D.rules.use_regime ? '' : ' <span class="muted">(gate is switched off in config)</span>') + '</p>';
  h += '<p class="note" style="margin-top:0">' + (G.reasons || []).map(esc).join('; ') + '</p>';
  if (G.pct20 != null) h += '<p class="note">Breadth: <b>' + num(G.pct20, 0) + '%</b> of ' + G.n + ' names above their 20-MA, <b>' + num(G.pct50, 0) +
    '%</b> above the 50-MA, net 20-day highs minus lows <b>' + pct(G.net_hl, 0) + '</b>.</p>';
  const mk = (D.scan.market || []).map(m => '<span class="chip">' + esc(m.symbol) + ' ' + num(m.close) + ' \u00b7 ' + (m.above_20 ? 'above' : 'below') + ' 20d, ' + (m.above_50 ? 'above' : 'below') + ' 50d' +
    (m.ext == null ? '' : ' \u00b7 ' + num(m.ext, 1) + 'x ATR%') + '</span>').join('');
  if (G.spy && G.spy.streak != null) h += '<p class="note">SPY up-close streak: <b>' + G.spy.streak + '</b>.</p>';
  h += '<p class="note">' + (mk || '') + '</p>';
  h += '<p class="note">Read at the last close and applied to the next day\u2019s entries. Open positions keep their stops and trails whatever the regime says.</p>';
  $('#regime').innerHTML = h;
})();

// ---- Alpaca paper mirror
(function(){
  const B = D.broker || {};
  if (!B.mode) { $('#broker').innerHTML = '<div class="empty">Not enabled. Set <code>PAPER_BROKER=alpaca</code> plus your PAPER keys (see README_AUTOMATION.md). It starts in dry-run: orders are logged here, not sent.</div>'; return; }
  const a = B.account || {};
  let h = '<p><span class="pill ' + (B.mode === 'send' ? 'ok' : '') + '">' + (B.mode === 'send' ? 'SENDING paper orders' : 'DRY RUN: logged, not sent') + '</span> ' +
    (a.status ? '<span class="pill">account ' + esc(a.status) + (a.blocked ? ' \u00b7 TRADING BLOCKED' : '') + '</span> ' : '') +
    (a.equity != null ? '<span class="pill">Alpaca equity ' + usd(a.equity) + (B.equity_gap_pct != null ? ' (' + pct(B.equity_gap_pct, 1) + ' vs simulator)' : '') + '</span>' : '') + '</p>';
  if (B.equity_gap_pct != null && Math.abs(B.equity_gap_pct) > 5) h += '<div class="banner">The Alpaca account equity differs from the simulator by more than 5%: position sizes are set by the simulator, so the two accounts will drift.</div>';
  const rec = B.recon || {};
  if (rec.note) h += '<p class="note">' + esc(rec.note) + '</p>';
  if ((rec.rows || []).length) h += '<h2 style="margin-top:10px">Positions: simulator vs broker</h2>' + table([
    {h: 'Symbol', f: r => '<span class="sym">' + esc(r.symbol) + '</span>', l: 1}, {h: 'Simulator', f: r => r.sim_qty}, {h: 'Broker', f: r => r.broker_qty},
    {h: 'Status', f: r => esc(r.status), l: 1, c: r => r.status === 'ok' ? 'up' : (r.status.startsWith('pending') ? '' : 'down')}], rec.rows, '');
  if ((B.fills || []).length) h += '<h2 style="margin-top:10px">Entry fills: real paper fill vs the fill model</h2>' + table([
    {h: 'Symbol', f: r => '<span class="sym">' + esc(r.symbol) + '</span>', l: 1}, {h: 'Qty', f: r => r.qty}, {h: 'Simulator', f: r => num(r.sim_price)},
    {h: 'Alpaca', f: r => num(r.broker_price)}, {h: 'Difference', f: r => pct(r.diff_pct, 2), c: r => r.diff_pct > 0 ? 'down' : 'up'},
    {h: 'Modelled slippage', f: r => num(r.modeled_slip_pct, 2) + '%'}], B.fills.slice().reverse().slice(0, 15), '') +
    '<p class="note">If Alpaca consistently fills worse than the modelled slippage, the model is too kind: raise <code>SLIP_ATR_FRAC</code> / <code>SLIP_IMPACT</code>.</p>';
  if ((B.log || []).length) h += '<h2 style="margin-top:10px">Order log</h2><div class="log">' + B.log.slice().reverse().slice(0, 20).map(e =>
    '<div><span class="t">' + esc((e.t || '').replace('T', ' ').slice(0, 16)) + '</span><b>' + esc(e.action) + '</b> ' + esc(e.detail) + (e.sent ? '' : ' <span class="muted">(dry run)</span>') + '</div>').join('') + '</div>';
  if ((B.errors || []).length) h += '<h2 style="margin-top:10px">Errors</h2>' + B.errors.slice().reverse().map(e => '<p class="down">' + esc(e.t.replace('T', ' ').slice(0, 16)) + ' ' + esc(e.msg) + '</p>').join('');
  $('#broker').innerHTML = h;
})();

// ---- activity + rules
$('#events').innerHTML = (D.events || []).length ? D.events.map(e =>
  '<div><span class="t">' + esc((e.t || '').replace('T', ' ').slice(0, 16)) + '</span><b>' + esc(e.kind) + '</b> ' + esc(e.msg) + '</div>').join('') : '<div class="empty">Nothing yet.</div>';
const R = D.rules;
$('#rules').innerHTML = '<ul class="rules">' +
  '<li>Risk per trade ' + num(R.risk_pct, 2) + '% of equity, sized to the full stop; max ' + R.max_open + ' open, max ' + R.max_new_day + ' new per day.</li>' +
  '<li>Entry only on a clean ORH re-claim with at least ' + num(R.dayvol_pct, 0) + '% of an average day\u2019s volume traded, low-of-day within ' + num(R.lod_atr, 2) + ' ATR, and no later than ' + esc(R.last_entry) + '.</li>' +
  '<li>No entry beyond ' + num(R.max_ext, 0) + '&times; ATR% from the 50-MA; trims at ' + R.trim_levels.join(' / ') + '&times;.</li>' +
  '<li>3-stop: thirds stop at ' + R.stops.map(x => Math.round(x * 100) + '%').join(' / ') + ' of the way to the full stop (full failure \u2248 \u22120.67R).</li>' +
  '<li>Portfolio caps: max ' + R.max_industry + ' per industry, ' + R.max_sector + ' per sector, open risk under ' + num(R.open_risk_cap_pct, 1) + '% of equity, leveraged exposure under ' + num(R.lev_cap_pct, 0) + '%, and no name that is 85%+ correlated with an open position.</li>' +
  '<li>Portfolio caps: max ' + R.max_industry + ' per industry, ' + R.max_sector + ' per sector, open risk under ' + num(R.open_risk_cap_pct, 1) + '% of equity, leveraged exposure under ' + num(R.lev_cap_pct, 0) + '%, and no name that is 85%+ correlated with an open position.</li>' +
  '<li>Scanner: no Focus name with earnings inside ' + R.earn_days + ' days. Market regime gate ' + (R.use_regime ? 'on' : 'off') + ': risk-off blocks new entries, neutral and stretched halve their risk.</li>' +
  '<li>Day 3: reduce a third and move stops to breakeven; from day 4 trail under the 10-day MA.</li></ul>';
})();
</script>
</body>
</html>
"""
