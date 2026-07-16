# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-16 12:05 PM CDT (run #49)

**🔴 HEADLINE — mmsell3 has gone LIVE with real money (since 2026-07-13), and this loop has
never tracked live P&L. That's the single most important gap in this report.** Confirmed via a
coarse ops check: `live_orders` shows **182 filled / 67 canceled** real orders on `mmsell3`,
latest ~5 minutes before this run — genuinely active, not dormant. Per `docs/BOOK_REGISTRY.md` /
`docs/MMSELL_VARIANTS_THESIS.md` (written by another session 2026-07-15), the live book's P&L
splits sharply: **non-World-Cup +5.6¢/trade at 96.3% win** vs **World Cup soccer −9.9¢/trade at
81.7% win** — pooled looks like breakeven, hiding two very different books. Five new paper
variants (mmsell4-8) already exist to find a replacement that excludes the WC drag. **This loop's
step-1 query only ever reads `paper_trades`** — it has been reporting the paper shadow the whole
time, never the real fills. I attempted a coarse live-money check this run (`live_orders` +
`account_snapshots`); the order-count read worked, but `account_snapshots` returned no usable
cash/portfolio figures (possibly stale or unpopulated — didn't chase further this run). **New
top-priority suggestion: get real live P&L into this loop's query**, since there is now actual
capital at risk that this report has been silent on for 3 days.

**Second item: pin15's retirement is CONFIRMED deployed.** No new pin15 trades since 12:26 PM UTC
(before the PR merge) — silent for ~4.5h as of this run, consistent with a clean cutover. Closing
this out; no longer a pending item.

**Trading books (settled n / P&L / per-trade / open) — paper only, see headline for live:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell2 (paper) | 1,168 | +$30.43 | +2.6 | 18 | still family leader |
| mmsell1 (paper) | 1,792 | +$35.81 | +2.0 | 23 | |
| mmsell (control, paper) | 2,875 | +$42.93 | +1.5 | 30 | |
| mmsell3 (paper shadow) | 571 | +$9.24 | +1.6 | 15 | flat — but see headline, live is the real number now |
| **mmsell4/6/7/8** (paper, new) | 0/1/0/0 settled | ~$0 | — | 7/7/3/7 open | too new to read (built 2026-07-15); **mmsell5 has zero rows at all** — allowlist may be too narrow to find markets yet |
| theta4 (fat-tail) | 26 | +$17.55 | +67.5 | 0 | +1 trade, edge steady, n=26/80 (33%) |
| weather con (all) | 396 | −$12.59 | −3.2 | 8 | rough batch (−22.9c/trade) |
| weather_concity | 37 | −$5.20 | −14.1 | 2 | rough batch too (−22.7c/trade) — moved together this time |
| pin15 | 445 | −$19.74 | −4.4 | 0 | **RETIRED, confirmed stopped** — no new trades since 12:26 PM UTC (pre-merge) |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**FREEZE gate check (new step this run):** settled grain=0, soft=5 (freeze-eligible total 5 of the
n≥100 trigger) — not fired, still UNTESTABLE per `docs/FREEZE_THESIS.md`. Open: grain=2, soft=25.
Low-urgency background check; nothing to act on.

weather books both had a rough, similarly-sized batch this run (con −22.9¢/trade, concity
−22.7¢/trade) — moved together for once, unlike recent runs' divergence; likely a shared bad
settlement window. theta4 added one more trade at its established ~67¢/trade magnitude — steady,
not decaying, 33% to its gate.

**Gate sweep (step 3b):** theta4 **26/80** (33%, calibration was checked clean 2026-07-15, watch
continues) · mmsell4-8 gates per `docs/MMSELL_VARIANTS_THESIS.md` (n≥150, mmsell5/8 n≥100) — all
far too early (0-1 settled) · weather_concity **37/120** (31%) · FREEZE **5/100** (not fired).

**Data (last-24h / latest CDT):** crypto_spot, crypto_ladder, weather forecasts/obs/ensembles/
buckets all fresh (11:57 AM–12:04 PM ✓). xgame_matches unchanged since run #48 (4 total in 24h,
latest ~10:12 PM CDT 7/15). xgame_tapes still frozen (~20h now, same timestamp as run #47/48) —
this has now gone from "modest lull" (run #47) to a real multi-run stall; still low-urgency
(shelved book) but worth noting it hasn't self-resolved like `xgame_matches` did.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** mmsell3 is LIVE with real money and this loop has never reported live P&L — top
priority to fix. pin15's retirement confirmed deployed and stopped. mmsell4-8 variants exist to
fix the live WC-drag but are too new to read (mmsell5 = zero rows, worth watching). theta4 steady
at 33% to gate. weather books had a shared rough batch. FREEZE gate check added, not fired.
xgame_tapes stall persisting across 3 runs now.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[NEW, TOP PRIORITY · get live P&L into this loop] mmsell3 has been trading real money since
   2026-07-13 (182 filled orders, 67 canceled) — three days this report has said nothing about
   it.** The live book's P&L is known to split sharply (non-WC +5.6¢/96.3% win vs WC −9.9¢/81.7%
   win per `docs/MMSELL_VARIANTS_THESIS.md`), but that was computed by a build session, not this
   loop. Recommend a fable/build session add a live-P&L slice to step 1's query (join
   `live_orders`/`fills` to settlement outcomes, or reuse whatever query produced the
   MMSELL_VARIANTS_THESIS numbers) so this report actually covers the capital that's at risk. I
   attempted a coarse `account_snapshots` balance check this run; it returned no usable figures
   (possibly stale/unpopulated) — didn't chase further, flagging as a secondary thing to check
   when building the real fix.

2. **[pin15 · RESOLVED — retirement confirmed deployed] No new trades since 12:26 PM UTC 2026-07-16
   (pre-merge), silent ~4.5h as of this run.** Closing this out — no longer a pending item. Final
   numbers: n=445, −$19.74 (−4.4¢/trade cumulative).

3. **[mmsell4-8 variants · WATCH, too early to read] Built 2026-07-15 to fix mmsell3's live
   World-Cup drag (see headline).** mmsell6 has 1 settled (+$0.06, uninformative). mmsell4/7/8
   have 0 settled but 3-7 open each — accruing. **mmsell5 has zero rows at all (not even open)** —
   worth a glance next run; if it's still at zero after another run or two, the `only=TOTAL+
   SPREAD+ASG+HRDERBY` allowlist may be too narrow to find markets, which would itself be a
   finding (per its own thesis: "mmsell5 PROMOTE if per-trade > mmsell4"). Gates: n≥150
   (mmsell4/6/7), n≥100 (mmsell5/8) — all far off.

4. **[theta4 · steady, calibration checked clean 2026-07-15] n=26/80 (33%), +67.5¢/trade, +1
   trade this run at the same magnitude.** No new action — the calibration check already ran
   (0/25 tail-hits vs 6.9% modeled, safe direction but statistically untested at that n). Keep
   watching the realized hit rate as n climbs; the first tail hit is still the real test.

5. **[idea-model queue · MMX/NEST context shifted] MMX's premise (extend mmsell's edge into new
   categories) is partly now superseded by mmsell4-8, which are already doing sport/type-based
   decomposition on the LIVE book — worth checking with a fable session whether MMX is still a
   distinct idea or redundant with this work.** NEST still behind theta4's n≥80 gate (currently
   33% there, calibration clean so far). RTPIN/BOXPIN behind unbuilt scraper infra. RATELAG
   behind a live Fed event.

6. **[weather_concity / con(all) · rough shared batch] concity −14.1¢/trade cumulative (31% to
   gate), con(all) −3.2¢/trade — both took a similarly-sized hit this batch (−22.7¢ and −22.9¢
   respectively)**, unlike recent runs where they diverged. Possibly a single bad settlement
   window hitting both. Carry forward, nothing to decide yet.

7. **[xgame_tapes · stall persisting, now 3 runs] Frozen at the same timestamp since run #47
   (~20h now).** `xgame_matches` self-recovered last run; tapes hasn't. Still low-urgency
   (shelved book) but worth a look if anyone's touching xgame code, since this is no longer a
   brief lull.

8. **[FREEZE gate · NEW standing check, not fired] Settled grain+soft = 5 of the n≥100 trigger.**
   Cheap background check now run every loop pass per the skill's new step 3b instruction; will
   only be reported prominently if it fires.

*(Changed this run: #1 NEW, top priority — mmsell3 went live 2026-07-13 and this loop has never
reported live P&L; recommend fixing the query. #2 pin15 RESOLVED — retirement confirmed deployed,
closing out. #3 NEW — mmsell4-8 variants exist (built 2026-07-15) to fix the live WC-drag,
tracking their early accrual. #5 MMX — flagged for a redundancy check against the new variants.
#8 NEW — FREEZE gate is now a standing per-run check per the updated skill; not fired. #4/#6/#7
otherwise updated/unchanged.)*
