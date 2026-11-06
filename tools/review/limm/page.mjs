// Builds the Alimm1 review page (a claude.ai HTML artifact with a database),
// in the review-page format (.claude/skills/review-page/SKILL.md): a Decide tab
// with the open questions, item cards with Go further / Maybe / Drop, diagrams
// of the mechanism, and the evidence beside each decision.
// Store: `reviews/{itemId}` {verdict, note}; `answers/{questionId}` {choice, note}.
// Usage: node tools/review/limm/page.mjs <out.html>
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { ROUND_DATE, TZ_NOTE, BOOK, SERIES, PRIOR, GROC_STEPS, QUESTIONS, ASKED, BUILT } from './round1.mjs';

const here = new URL('.', import.meta.url);
const out = process.argv[2];
if (!out) throw new Error('usage: node page.mjs <out.html>');
const probePath = new URL(`probe-${ROUND_DATE}.json`, here);
const P = existsSync(probePath) ? JSON.parse(readFileSync(probePath, 'utf8')) : null;

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const usd = (n, sign = false) => `${sign ? (n >= 0 ? '+' : '−') : n < 0 ? '−' : ''}$${Math.abs(n).toFixed(2)}`;

// ---- diagrams ------------------------------------------------------------
// Two schematic price paths of the side we bid, over a window, with our bid as
// a dashed line. Left: a cheap NO bid. Right: a cheap YES bid.
function sidesDiagram() {
  const W = 340, H = 190, x0 = 34, x1 = 326, y = (c) => 20 + (20 - Math.min(c, 20)) * 7; // 0..20c -> 160..20 (higher prices clip to the top)
  const panel = (title, path, bid, fills, caption, word) => `
  <figure class="dia">
    <svg viewBox="0 0 ${W} ${H + 34}" role="img" aria-label="${esc(title)}: ${esc(caption)}">
      <text x="${x0}" y="12" class="t-h">${esc(title)}</text>
      ${[0, 10, 20].map((c) => `<line x1="${x0}" x2="${x1}" y1="${y(c)}" y2="${y(c)}" class="gl"/><text x="${x0 - 6}" y="${y(c) + 4}" class="t-ax" text-anchor="end">${c}c</text>`).join('')}
      <line x1="${x0}" x2="${x1}" y1="${y(bid)}" y2="${y(bid)}" class="bid"/>
      <text x="${x0 + 6}" y="${y(bid) - 5}" class="t-bid">our bid ${bid}c</text>
      ${word ? `<line x1="${word}" x2="${word}" y1="${y(20)}" y2="${y(0)}" class="said"/><text x="${word + 4}" y="${y(20) + 10}" class="t-said">word said</text>` : ''}
      <line x1="${x1 - 70}" x2="${x1 - 70}" y1="${y(20)}" y2="${y(0)}" class="aside"/>
      <text x="${x1 - 66}" y="${y(20) + 10}" class="t-ax">step aside</text>
      <polyline points="${path.map(([t, c]) => `${x0 + t * (x1 - x0)},${y(c)}`).join(' ')}" class="path"/>
      ${fills.map(([t, c]) => `<circle cx="${x0 + t * (x1 - x0)}" cy="${y(c)}" r="5" class="${x0 + t * (x1 - x0) > x1 - 70 ? 'dodged' : 'fill'}"/>`).join('')}
      <text x="${x0}" y="${H + 2}" class="t-ax">window opens</text><text x="${x1}" y="${H + 2}" class="t-ax" text-anchor="end">closes</text>
    </svg>
    <figcaption>${esc(caption)}</figcaption>
  </figure>`;
  // NO side on a likely word: NO ~10c, word said at 45% of the window, NO dumps to 0 through our bid.
  const no = panel('Cheap NO (likely word)', [[0, 12], [0.2, 11], [0.44, 10], [0.45, 3], [0.47, 0], [1, 0]], 8, [[0.452, 8]], 'Said at any moment: holders dump NO through our bid in seconds. A full fill at a loss. This is how the YouTube bids were swept.', x0 + 0.45 * (x1 - x0));
  // YES side on an unlikely word: YES ~6c drifts down; never said; holders sell late.
  const yes = panel('Cheap YES (unlikely word)', [[0, 6], [0.3, 6], [0.6, 5.5], [0.8, 5], [0.86, 4], [0.93, 2], [1, 0]], 4, [[0.86, 4]], 'If the word is said, YES jumps to ~99c and nobody sells to us. Fills come late, as hope fades. The hollow dot is a fill we avoid by stepping aside 48 h before close.', 0);
  return `<div class="dias">${no}${yes}</div>`;
}

function grocFlow() {
  const n = GROC_STEPS.length;
  const W = 640, bw = 138, gap = (W - n * bw) / (n - 1), H = 132;
  const box = (s, i) => {
    const x = i * (bw + gap);
    const cls = i === 1 ? 'node warn' : i === n - 1 ? 'node loop' : 'node';
    return `<g><rect x="${x}" y="22" width="${bw}" height="62" rx="6" class="${cls}"/>
      <text x="${x + 10}" y="16" class="t-ax">${esc(s.t)}</text>
      <foreignObject x="${x + 6}" y="26" width="${bw - 12}" height="56"><div class="fo">${esc(s.h)}</div></foreignObject>
      ${i < n - 1 ? `<line x1="${x + bw + 4}" x2="${x + bw + gap - 6}" y1="53" y2="53" class="arrow" marker-end="url(#ah)"/>` : ''}</g>`;
  };
  const lx = (n - 1) * (bw + gap);
  return `<div class="scroll"><svg viewBox="0 0 ${W} ${H}" class="flow" role="img" aria-label="GROC and SAFE: sent, refused 409, marked not placed, then turned down each hour as too crowded">
    <defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" class="ahead"/></marker></defs>
    ${GROC_STEPS.map(box).join('')}
    <path d="M${lx + bw - 20},84 C${lx + bw - 20},122 ${lx + 20},122 ${lx + 20},88" class="arrow" marker-end="url(#ah)"/>
    <text x="${lx + bw / 2}" y="${H - 2}" class="t-ax" text-anchor="middle">re-checked hourly</text>
  </svg></div>`;
}

// ---- probe evidence --------------------------------------------------------
function verdictChip() {
  if (!P) return '<span class="chip pend">Study running</span>';
  const cls = P.verdict === 'PROMISING' ? 'go' : P.verdict === 'AVOID' ? 'drop' : 'maybe';
  return `<span class="chip ${cls}">${esc(P.verdict)}</span>`;
}
function probeBlock() {
  if (!P) return `<p class="pn">The read-only study (<code>scripts/limm_window_probe.py</code>) is running through the ops channel. This section fills in when it returns.</p>`;
  const a = P.A, b = P.B;
  const tiles = [
    ['Qualifying markets now', String(a.qual)],
    ['Reward per market-day', usd(a.mean)],
    ['Past markets entered', String(b.entered)],
    ['Of those, filled', `${b.filled} (${b.entered ? Math.round((100 * b.filled) / b.entered) : 0}%)`],
    ['Fill P&L', usd(b.fill_usd, true)],
    ['Net, reward + fills', usd(b.net, true)],
  ];
  const bar = b.reward_usd > 0 ? Math.min(100, Math.round((100 * Math.abs(Math.min(0, b.fill_usd))) / b.reward_usd)) : 0;
  return `<div class="tiles">${tiles.map(([k, v]) => `<div class="tile"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('')}</div>
  <div class="cost"><div class="lbl">Fills cost ${bar}% of the reward <span class="key">bar = fill losses · tick = the 40% limit</span></div>
    <div class="track" role="img" aria-label="Fill losses are ${bar}% of the reward; the pre-registered limit is 40%"><span class="fillbar" style="width:${bar}%"></span><span class="base" style="left:40%"></span></div></div>
  <p class="pn">Pre-registered bar for PROMISING: at least $0.50 per market-day on 10 or more markets now, and 100+ past entries with fills costing no more than 40% of the reward. ${esc(P.note || '')}</p>`;
}
function seriesStats(t) {
  const s = P && P.A.series[t];
  if (!s) return '<p class="pn">Numbers arrive with the study.</p>';
  return `<dl class="kv"><div><dt>Markets</dt><dd>${s.mk}</dd></div><div><dt>Cheap YES</dt><dd>${s.yes_cheap}</dd></div><div><dt>Cheap NO</dt><dd>${s.no_cheap}</dd></div><div><dt>Qualify</dt><dd>${s.qual}</dd></div><div><dt>$/day, all</dt><dd>${usd(s.usd_day)}</dd></div></dl>`;
}

// ---- cards & questions ------------------------------------------------------
function seriesCard(s, k) {
  return `<article class="item" data-coll="reviews" data-id="${s.id}">
  <header><div class="no">W${k + 1}</div><div class="ttl"><h3>${esc(s.name)}</h3><div class="chips"><span class="chip mono">${esc(s.ticker)}</span></div></div></header>
  <p class="pitch">${esc(s.pitch)}</p>
  ${seriesStats(s.ticker)}
  <div class="review" role="group" aria-label="Your call on ${esc(s.name)}">
    <div class="seg"><button type="button" data-v="go" aria-pressed="false">Go further</button><button type="button" data-v="maybe" aria-pressed="false">Maybe</button><button type="button" data-v="drop" aria-pressed="false">Drop</button></div>
    <label class="note"><span>Notes</span><textarea id="n-${s.id}" rows="2" placeholder="Words to avoid, size, anything to try"></textarea></label>
    <div class="saved" aria-live="polite"></div>
  </div>
</article>`;
}
function question(q) {
  return `<fieldset class="q" data-q="${q.id}"><legend>${esc(q.q)}</legend>
  ${q.options.map((o, k) => `<label class="opt"><input type="radio" name="q-${q.id}" value="${k}" id="q-${q.id}-${k}"><span>${esc(o)}</span></label>`).join('')}
  <p class="why">${esc(q.why)}</p>
  <label class="note"><span>Notes</span><textarea id="qn-${q.id}" rows="2"></textarea></label><div class="saved" aria-live="polite"></div></fieldset>`;
}

const rows = (list, f) => list.map(f).join('');
// The operator's own question, at the top of the section it belongs to.
const asked = (k) => ASKED[k] ? `<blockquote class="asked"><span>You asked · ${esc(ASKED[k].when)}</span>${esc(ASKED[k].q)}</blockquote>` : '';
const STATE = { built: 'Built · waiting on merge', live: 'Live', blocked: 'Live · blocked', kept: 'No change' };
const builtCard = (b) => `<article class="built" data-state="${b.state === 'kept' ? 'kept' : 'built'}"><header><h3>${esc(b.title)}</h3><span class="chip ${b.state === 'blocked' ? 'maybe' : b.state === 'kept' ? '' : 'go'}">${esc(STATE[b.state])}</span></header><p>${esc(b.what)}</p><p class="pn mono">${esc(b.id)}: answered option 1 (recommended)${b.id.startsWith('r2') ? ', in chat' : ''}</p></article>`;
const html = `<title>Alimm1 Review Desk</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Sofia+Sans+Condensed:wght@600;700;800&family=Source+Sans+3:wght@400;500;600&family=JetBrains+Mono:wght@500&display=swap">
<style>
/* Layout: an order ticket. A sticky strip of tabs and a tally; the Decide tab
   first; evidence (diagrams, numbers) sits beside each decision. */
:root {
  --ground: #eef0ec; --ticket: #fbfcf9; --ink: #18211c; --muted: #58645c; --rule: #d3d9d2;
  --yes: #1d6f5a; --no: #b04a33; --accent: #8c5a00; --go: #23753f; --maybe: #8a6200; --drop: #a33a2c;
  --chip: #e1e6df; --track: #dde3dc; --mark: #18211c;
  --display: "Sofia Sans Condensed", "Arial Narrow", system-ui, sans-serif;
  --body: "Source Sans 3", system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: "JetBrains Mono", ui-monospace, Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --ground: #0e1311; --ticket: #151c18; --ink: #e4ebe5; --muted: #97a69c; --rule: #2a362f;
  --yes: #5cc9a7; --no: #ff8f74; --accent: #f2b54a; --go: #63d28c; --maybe: #f0c35a; --drop: #ff8a7a;
  --chip: #202a24; --track: #222d27; --mark: #e4ebe5; color-scheme: dark } }
:root[data-theme="dark"] {
  --ground: #0e1311; --ticket: #151c18; --ink: #e4ebe5; --muted: #97a69c; --rule: #2a362f;
  --yes: #5cc9a7; --no: #ff8f74; --accent: #f2b54a; --go: #63d28c; --maybe: #f0c35a; --drop: #ff8a7a;
  --chip: #202a24; --track: #222d27; --mark: #e4ebe5; color-scheme: dark }
* { box-sizing: border-box; }
body { background: var(--ground); color: var(--ink); font: 16px/1.5 var(--body); padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); padding-block: 0 48px; }
body::before { content: ""; position: fixed; inset: 0 0 auto; height: env(safe-area-inset-top, 0px); z-index: 10; background: var(--ground); }
main { max-width: 1080px; margin: 0 auto; display: grid; gap: 26px; }
h1, h2, h3 { font-family: var(--display); text-wrap: balance; margin: 0; }
h1 { font-size: clamp(32px, 7vw, 48px); line-height: 1; letter-spacing: 0.01em; }
h2 { font-size: 28px; }
h3 { font-size: 21px; line-height: 1.15; }
.intro { display: grid; gap: 10px; padding-top: 24px; max-width: 68ch; }
.eyebrow { font: 500 12px/1 var(--mono); letter-spacing: 0.1em; text-transform: uppercase; color: var(--accent); }
.intro p, .lead { margin: 0; color: var(--muted); max-width: 68ch; }
.store { font-size: 13px; color: var(--muted); }
.bar-sticky { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 5; background: var(--ground); padding-block: 10px; border-bottom: 1px solid var(--rule); display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; }
.tabs { display: flex; gap: 4px; flex-wrap: wrap; }
.tabs button, .seg button { font: 600 14px/1 var(--body); border: 1px solid var(--rule); background: var(--ticket); color: var(--ink); padding: 9px 12px; border-radius: 6px; cursor: pointer; }
.tabs button[aria-pressed="true"] { background: var(--ink); color: var(--ticket); border-color: var(--ink); }
.tally { font: 500 13px/1.3 var(--mono); color: var(--muted); font-variant-numeric: tabular-nums; }
.tally b { color: var(--go); font-weight: 500; }
button:focus-visible, textarea:focus-visible, input:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
section { display: grid; gap: 14px; }
.hidden-tab { display: none; }
.panel { background: var(--ticket); border: 1px solid var(--rule); border-radius: 10px; padding: 14px; display: grid; gap: 10px; min-width: 0; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 320px), 1fr)); gap: 14px; }
.item { background: var(--ticket); border: 1px solid var(--rule); border-radius: 10px; padding: 14px; display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.item[data-v="go"] { border-color: var(--go); box-shadow: inset 4px 0 0 var(--go); }
.item[data-v="maybe"] { border-color: var(--maybe); }
.item[data-v="drop"] { opacity: 0.62; }
.item header { display: flex; gap: 12px; align-items: flex-start; }
.no { font: 500 13px/1 var(--mono); color: var(--accent); border: 1.5px solid var(--accent); border-radius: 5px; padding: 5px 6px; min-width: 36px; text-align: center; }
.ttl { display: grid; gap: 6px; min-width: 0; }
.chips { display: flex; flex-wrap: wrap; gap: 4px; }
.chip { font: 600 11px/1 var(--body); letter-spacing: 0.05em; text-transform: uppercase; background: var(--chip); color: var(--muted); padding: 4px 7px; border-radius: 4px; }
.chip.mono { font-family: var(--mono); font-weight: 500; letter-spacing: 0; text-transform: none; }
.chip.go { background: var(--go); color: var(--ticket); } .chip.maybe { background: var(--maybe); color: var(--ticket); }
.chip.drop { background: var(--drop); color: var(--ticket); } .chip.pend { color: var(--accent); }
.pitch { margin: 0; font-weight: 500; }
.pn, .why { margin: 0; font-size: 14px; color: var(--muted); }
.kv { margin: 0; display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 6px; }
.kv div { min-width: 0; } .kv dt { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
.kv dd { margin: 0; font: 500 15px var(--mono); font-variant-numeric: tabular-nums; }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; }
.tile { border: 1px solid var(--rule); border-radius: 8px; padding: 10px; display: grid; gap: 2px; background: var(--ground); }
.tile span { font-size: 12px; color: var(--muted); } .tile b { font: 500 20px var(--mono); font-variant-numeric: tabular-nums; }
.cost { display: grid; gap: 6px; }
.lbl { font: 500 12px var(--mono); color: var(--muted); display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px; }
.key { font-weight: 400; }
.track { position: relative; height: 10px; background: var(--track); border-radius: 3px; }
.fillbar { position: absolute; inset: 0 auto 0 0; background: var(--no); border-radius: 3px; }
.base { position: absolute; top: -3px; bottom: -3px; width: 2px; background: var(--mark); }
.dias { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 300px), 1fr)); gap: 14px; }
.dia { margin: 0; display: grid; gap: 6px; min-width: 0; }
.dia svg, .flow { width: 100%; height: auto; display: block; }
.dia figcaption { font-size: 14px; color: var(--muted); }
svg .gl { stroke: var(--rule); stroke-width: 1; }
svg .bid { stroke: var(--accent); stroke-width: 2; stroke-dasharray: 6 4; }
svg .said { stroke: var(--no); stroke-width: 1.5; }
svg .aside { stroke: var(--muted); stroke-width: 1; stroke-dasharray: 2 3; }
svg .path { fill: none; stroke: var(--ink); stroke-width: 2.5; stroke-linejoin: round; }
svg .fill { fill: var(--no); stroke: var(--ticket); stroke-width: 2; }
svg .dodged { fill: var(--ticket); stroke: var(--no); stroke-width: 2; }
svg text { fill: var(--muted); font: 500 11px var(--mono); }
svg .t-h { fill: var(--ink); font: 700 15px var(--display); }
svg .t-bid { fill: var(--accent); } svg .t-said { fill: var(--no); }
svg .node { fill: var(--ticket); stroke: var(--rule); stroke-width: 1.5; }
svg .node.warn { stroke: var(--no); } svg .node.loop { stroke: var(--accent); }
svg .arrow { fill: none; stroke: var(--muted); stroke-width: 1.5; } svg .ahead { fill: var(--muted); }
.fo { font: 600 13px/1.25 var(--body); color: var(--ink); }
.scroll { overflow-x: auto; }
.flow { min-width: 560px; }
.steps { list-style: none; padding: 0; margin: 0; display: grid; gap: 8px; }
.steps li { display: grid; grid-template-columns: 118px 1fr; gap: 10px; }
.steps time { font: 500 13px/1.5 var(--mono); color: var(--accent); }
.steps p { margin: 2px 0 0; color: var(--muted); font-size: 15px; }
table { border-collapse: collapse; width: 100%; font-size: 15px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--rule); vertical-align: top; }
th { font-size: 12px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
td.n { font: 500 14px var(--mono); font-variant-numeric: tabular-nums; white-space: nowrap; }
.yes { color: var(--yes); font-weight: 600; } .noside { color: var(--no); font-weight: 600; }
.qs { display: grid; gap: 12px; }
.q { background: var(--ticket); border: 1px solid var(--rule); border-radius: 10px; padding: 12px 14px; display: grid; gap: 6px; margin: 0; min-width: 0; }
.q legend { font-weight: 600; padding: 0; float: left; width: 100%; margin-bottom: 6px; font-size: 17px; }
.opt { display: flex; gap: 8px; align-items: flex-start; padding: 4px 0; cursor: pointer; }
.opt input { margin-top: 5px; accent-color: var(--accent); }
.review { display: grid; gap: 8px; margin-top: auto; padding-top: 6px; }
.seg { display: flex; flex-wrap: wrap; gap: 6px; }
.seg button[data-v="go"][aria-pressed="true"] { background: var(--go); border-color: var(--go); color: var(--ticket); }
.seg button[data-v="maybe"][aria-pressed="true"] { background: var(--maybe); border-color: var(--maybe); color: var(--ticket); }
.seg button[data-v="drop"][aria-pressed="true"] { background: var(--drop); border-color: var(--drop); color: var(--ticket); }
.note { display: grid; gap: 4px; }
.note span { font-size: 12px; color: var(--muted); }
textarea { font: 15px/1.4 var(--body); color: var(--ink); background: var(--ground); border: 1px solid var(--rule); border-radius: 6px; padding: 8px; width: 100%; resize: vertical; }
.saved { font-size: 12px; color: var(--muted); min-height: 1.2em; }
.prior { margin: 0; display: grid; gap: 6px; }
.prior div { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.3fr); gap: 10px; border-bottom: 1px dashed var(--rule); padding-bottom: 6px; }
.prior dt { color: var(--muted); font-size: 14px; } .prior dd { margin: 0; } .prior small { display: block; color: var(--muted); font-size: 12px; }
code { font: 13px var(--mono); }
.asked { margin: 0; padding: 10px 12px; border-left: 3px solid var(--accent); background: var(--ticket); border-radius: 0 8px 8px 0; font-size: 16px; font-style: italic; max-width: 72ch; }
.asked span { display: block; font: 500 11px/1.4 var(--mono); font-style: normal; letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); margin-bottom: 2px; }
.built { background: var(--ticket); border: 1px solid var(--rule); border-radius: 10px; padding: 14px; display: grid; gap: 8px; min-width: 0; }
.built[data-state="built"] { border-color: var(--go); box-shadow: inset 4px 0 0 var(--go); }
.built header { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.built p { margin: 0; }
.mono { font-family: var(--mono); font-size: 12px; }
@media (max-width: 600px) { .kv { grid-template-columns: repeat(3, minmax(0, 1fr)); } .steps li { grid-template-columns: 1fr; gap: 0; } .prior div { grid-template-columns: 1fr; gap: 0; } }
@media (prefers-reduced-motion: no-preference) { .item { transition: border-color .2s, opacity .2s; } }
</style>

<main>
  <header class="intro">
    <div class="eyebrow">Alimm1 · liquidity rewards · round 1 · ${ROUND_DATE}</div>
    <h1>Alimm1 Review Desk</h1>
    <p>Round 1 is answered and built: the <b>Built</b> tab shows what each answer changed. Your answers stay editable on <b>Decide</b>, and each section opens with the question you asked. ${esc(TZ_NOTE)}</p>
    <p class="store" id="store">Connecting to the review store…</p>
  </header>

  <nav class="bar-sticky" aria-label="Sections">
    <div class="tabs" role="group" aria-label="Tab">
      <button type="button" data-tab="built" aria-pressed="true">Built</button>
      <button type="button" data-tab="decide" aria-pressed="false">Decide</button>
      <button type="button" data-tab="windows" aria-pressed="false">Window markets</button>
      <button type="button" data-tab="groc" aria-pressed="false">GROC &amp; SAFE</button>
      <button type="button" data-tab="book" aria-pressed="false">The book</button>
    </div>
    <div class="tally" id="tally">0 of ${QUESTIONS.length} answered</div>
  </nav>

  <section id="tab-built" aria-labelledby="h-built">
    <h2 id="h-built">Built from your answers</h2>
    ${asked('built')}
    <p class="lead">All seven answers took the recommended option, and you marked the weekly series Go further. Round 1’s four changes are live since Nov 5, 11:37 PM CT (PR #743); three needed no change. Your round-2 answer (block window markets per word) is live since 6:57 AM CT (PR #749), and the window trial placed its first three bids.</p>
    <div class="grid">${rows(BUILT, builtCard)}</div>
  </section>

  <section id="tab-decide" class="hidden-tab" aria-labelledby="h-decide">
    <h2 id="h-decide">Decide</h2>
    ${asked('decide')}
    <p class="lead">One new question (round 2) on top, then round 1’s seven, already answered. The recommended option is first. Each carries the reason in one line; the tabs above carry the detail.</p>
    <div class="qs">${rows(QUESTIONS, question)}
      <fieldset class="q" data-q="r1-general"><legend>Anything else?</legend><label class="note"><span>Notes</span><textarea id="qn-r1-general" rows="3"></textarea></label><div class="saved" aria-live="polite"></div></fieldset>
    </div>
  </section>

  <section id="tab-windows" class="hidden-tab" aria-labelledby="h-win">
    <h2 id="h-win">Window markets ${verdictChip()}</h2>
    ${asked('windows')}
    <p class="lead">“Trump says X before a date.” Each word resolves YES the moment it is said, so there is no set time to step aside first. That is why they were dropped in October. Which side we bid changes everything.</p>
    <div class="panel">${sidesDiagram()}</div>
    <div class="panel"><h3>What the study found</h3>${probeBlock()}</div>
    <div class="panel"><h3>What we knew before</h3><dl class="prior">${rows(PRIOR, (p) => `<div><dt>${esc(p.k)}</dt><dd>${esc(p.v)}<small>${esc(p.src)}</small></dd></div>`)}</dl></div>
    <h3>The three series</h3>
    <div class="grid">${SERIES.map(seriesCard).join('')}</div>
  </section>

  <section id="tab-groc" class="hidden-tab" aria-labelledby="h-groc">
    <h2 id="h-groc">Why GROC and SAFE weren’t retried</h2>
    ${asked('groc')}
    <p class="lead">Kalshi refused both at the tail of its Thursday maintenance. The bot was free to try again, but every hourly check since has turned them down as too crowded to earn anything. FREE, on the same event, still qualifies and is resting.</p>
    <div class="panel">${grocFlow()}</div>
    <ol class="steps panel">${rows(GROC_STEPS, (s) => `<li><time>${esc(s.t)}</time><div><b>${esc(s.h)}</b><p>${esc(s.d)}</p></div></li>`)}</ol>
  </section>

  <section id="tab-book" class="hidden-tab" aria-labelledby="h-book">
    <h2 id="h-book">The book</h2>
    ${asked('book')}
    <p class="lead">As of ${esc(BOOK.asOf)}. Net so far is ${usd(BOOK.netSoFar)} (settled fills plus ${usd(BOOK.rewardsConfirmed)} of confirmed rewards), or ${usd(BOOK.netIfLeftoversZero)} if the YouTube leftovers go to zero.</p>
    <div class="panel scroll"><h3>Resting bids</h3><table><thead><tr><th>Market</th><th>Side</th><th>Price × size</th><th>At risk</th></tr></thead><tbody>${rows(BOOK.resting, (r) => `<tr><td>${esc(r.what)}</td><td class="${r.side === 'YES' ? 'yes' : 'noside'}">${r.side}</td><td class="n">${r.price}c × ${r.qty}</td><td class="n">${usd(r.usd)}</td></tr>`)}</tbody></table></div>
    <div class="panel scroll"><h3>Held</h3><table><thead><tr><th>Market</th><th>Side</th><th>Bought</th><th>Exit</th></tr></thead><tbody>${rows(BOOK.held, (r) => `<tr><td>${esc(r.what)}</td><td class="${r.side === 'YES' ? 'yes' : 'noside'}">${r.side}</td><td class="n">${r.qty} at ${r.price}c</td><td>${esc(r.exit)}</td></tr>`)}</tbody></table></div>
    <div class="panel scroll"><h3>Payouts expected</h3><table><thead><tr><th>When</th><th>From</th><th>Size</th></tr></thead><tbody>${rows(BOOK.expected, (r) => `<tr><td class="n">${esc(r.when)}</td><td>${esc(r.what)}</td><td class="n">${esc(r.usd)}</td></tr>`)}</tbody></table>
      <p class="pn">Estimates from the bot’s reward model. A payout counts once you send the Rewards screenshot.</p></div>
  </section>
</main>

<script>
(() => {
  const QIDS = ${JSON.stringify(QUESTIONS.map((q) => q.id))};
  const state = { reviews: {}, answers: {} };
  const COLLS = ['reviews', 'answers'];
  let db = null;
  const LS = 'alimm1-review-r1';
  const lsGet = () => { try { return JSON.parse(localStorage.getItem(LS) || '{}'); } catch { return {}; } };
  const lsSet = (v) => { try { localStorage.setItem(LS, JSON.stringify(v)); } catch {} };
  let tab = 'built';

  function paintItem(el) {
    const r = state[el.dataset.coll][el.dataset.id] || {};
    el.dataset.v = r.verdict || '';
    el.querySelectorAll('button[data-v]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.v === r.verdict)));
    const ta = el.querySelector('textarea');
    if (ta && document.activeElement !== ta && typeof r.note === 'string') ta.value = r.note;
  }
  function paintQ(id) {
    const a = state.answers[id] || {};
    document.querySelectorAll('input[name="q-' + id + '"]').forEach((i) => { i.checked = String(a.choice) === i.value; });
    const ta = document.getElementById('qn-' + id);
    if (ta && document.activeElement !== ta && typeof a.note === 'string') ta.value = a.note;
  }
  function paintTally() {
    const n = QIDS.filter((id) => state.answers[id] && state.answers[id].choice !== undefined && state.answers[id].choice !== null).length;
    const m = Object.values(state.reviews).filter((r) => r && r.verdict).length;
    document.getElementById('tally').innerHTML = '<b>' + n + ' of ' + QIDS.length + ' answered</b> · ' + m + ' of ${SERIES.length} series marked';
  }
  function paintTab() {
    document.querySelectorAll('[data-tab]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.tab === tab)));
    document.querySelectorAll('main > section').forEach((s) => s.classList.toggle('hidden-tab', s.id !== 'tab-' + tab));
  }
  function paintAll() {
    document.querySelectorAll('[data-coll]').forEach(paintItem);
    document.querySelectorAll('.q').forEach((f) => paintQ(f.dataset.q));
    paintTally();
    paintTab();
  }

  const flash = (el, msg) => { if (el) el.textContent = msg; };
  const busy = {};
  async function save(coll, id, body, statusEl) {
    state[coll][id] = Object.assign({}, state[coll][id], body);
    paintAll();
    if (!db) { const all = lsGet(); all[coll] = all[coll] || {}; all[coll][id] = Object.assign({}, state[coll][id], { pending: true }); lsSet(all); flash(statusEl, 'Saved in this browser only.'); return; }
    if (busy[coll + id]) { busy[coll + id].again = true; return; }
    busy[coll + id] = { again: false };
    try {
      do { busy[coll + id].again = false; await db.collection(coll).doc(id).set(Object.assign({}, state[coll][id], { at: new Date().toISOString() })); } while (busy[coll + id].again);
      flash(statusEl, 'Saved.');
    } catch (e) {
      flash(statusEl, e && (e.code === 'not_granted' || e.code === 'permission_denied') ? 'You can read this page but not save to it.' : 'Couldn’t save. Check your connection and try again.');
    } finally { delete busy[coll + id]; }
  }

  document.querySelectorAll('[data-coll]').forEach((el) => {
    const id = el.dataset.id, coll = el.dataset.coll, st = el.querySelector('.saved'), ta = el.querySelector('textarea');
    el.querySelectorAll('button[data-v]').forEach((b) => b.addEventListener('click', () => {
      const cur = (state[coll][id] || {}).verdict;
      save(coll, id, { verdict: cur === b.dataset.v ? null : b.dataset.v, note: ta.value }, st);
    }));
    let t;
    ta.addEventListener('input', () => { clearTimeout(t); flash(st, 'Typing…'); t = setTimeout(() => save(coll, id, { note: ta.value }, st), 900); });
    ta.addEventListener('blur', () => { clearTimeout(t); if ((state[coll][id] || {}).note !== ta.value && ta.value !== '') save(coll, id, { note: ta.value }, st); });
  });
  document.querySelectorAll('.q').forEach((f) => {
    const id = f.dataset.q, st = f.querySelector('.saved'), ta = document.getElementById('qn-' + id);
    f.querySelectorAll('input[type=radio]').forEach((i) => i.addEventListener('change', () => save('answers', id, { choice: Number(i.value), note: ta.value }, st)));
    let t;
    ta.addEventListener('input', () => { clearTimeout(t); flash(st, 'Typing…'); t = setTimeout(() => save('answers', id, { note: ta.value }, st), 900); });
    ta.addEventListener('blur', () => { clearTimeout(t); if ((state.answers[id] || {}).note !== ta.value && ta.value !== '') save('answers', id, { note: ta.value }, st); });
  });
  document.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => {
    tab = b.dataset.tab;
    try { sessionStorage.setItem(LS + '-tab2', tab); } catch {}
    paintTab();
  }));
  try { tab = sessionStorage.getItem(LS + '-tab2') || 'built'; } catch {}

  const local = lsGet();
  Object.assign(state.reviews, local.reviews || {});
  Object.assign(state.answers, local.answers || {});
  paintAll();
  const note = document.getElementById('store');
  const use = window.claude && window.claude.use;
  if (!use) { note.textContent = 'Open this page on claude.ai, signed in, to save your answers for the next session. Answers made here stay in this browser.'; return; }
  const timer = setTimeout(() => { note.textContent = 'The review store isn’t answering yet. Answers are kept in this browser until it does.'; }, 10000);
  use('db').then((d) => {
    if (!d) { clearTimeout(timer); note.textContent = 'Saving isn’t available in this view. Answers stay in this browser.'; return; }
    db = d;
    note.textContent = 'Your answers save to this page for the next session to read.';
    const left = lsGet();
    for (const coll of COLLS) for (const [id, v] of Object.entries(left[coll] || {})) {
      if (!v || !v.pending) continue;
      const body = Object.assign({}, v, { at: new Date().toISOString() });
      delete body.pending;
      db.collection(coll).doc(id).set(body).then(() => { const now = lsGet(); if (now[coll]) { delete now[coll][id]; lsSet(now); } }).catch(() => {});
    }
    for (const coll of COLLS) {
      db.collection(coll).onSnapshot((snap) => {
        clearTimeout(timer);
        snap.docs.forEach((doc) => { const v = doc.data(); if (v) state[coll][doc.id] = v; });
        paintAll();
      }, () => { clearTimeout(timer); note.textContent = 'Lost the connection to the review store. Reload to try again.'; });
    }
  });
})();
</script>
`;
writeFileSync(out, html);
console.log(`Wrote ${out} (${Math.round(html.length / 1024)} KB, ${QUESTIONS.length} questions, ${SERIES.length} series, probe ${P ? P.verdict : 'pending'})`);
