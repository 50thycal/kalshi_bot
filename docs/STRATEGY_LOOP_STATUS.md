# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12.
As of run #56, the loop also pulls real live P&L for any LIVE book via a direct SQL query (total
$ P&L + WC/non-WC split — `mmsell_live` itself computes but never prints either). Suggestions
are **recommendations only** — the loop never acts on them; the user reviews and runs fable to
change anything. Newest snapshot replaces the one above it; the suggestion list carries over
run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-20 08:02 PM CDT (run #62)

**No new gate events this run.** mmsell4's KILL from run #61 continues to confirm itself
(cumulative per-trade dropped further, −0.06¢→−0.26¢) and mmsell6/mmsell11 remain confirmed
PROMOTE, though mmsell11's edge eased slightly this batch while mmsell6's widened.

| book | n | ¢/trade (cum) | Δ this run | note |
|---|---|---|---|---|
| mmsell3 (control) | 978 | +1.51¢ | +14 n, +0.29¢/trade batch | baseline, flat |
| **mmsell6** | 311 | **+2.18¢** | +9 n, +6.1¢/trade batch | PROMOTE confirmed, still strengthening |
| **mmsell11** | 208 | +3.24¢ | +12 n, −0.7¢/trade batch | PROMOTE confirmed, eased slightly but still 2.1x mmsell3 |
| mmsell4 | 164 | **−0.26¢** | +9 n, −3.7¢/trade batch | KILLED run #61 — confirmed further |

**xgame_tapes RESOLVED — was a genuine lull, not a broken collector.** Escalated last run after
two consecutive unchanged timestamps; pulled the collector's own log lines this run and it's
polling cleanly every ~4 minutes with `errors=0`, just finding `kal_games=0 pm_games=0` (no
live games in the window right now). No action needed — closing this out rather than carrying
it forward as a live concern.

**Live P&L (real money — mmsell3):** unchanged from run #61 — no new live settlements this
window.
| bucket | n settled | wins | total P&L | ¢/contract |
|---|---|---|---|---|
| non-WC | 204 | 187 | +$1.92 | +0.94¢ |
| World Cup | 172 | 157 | +$0.11 | +0.06¢ |
| **TOTAL** | **376** | **344** | **+$2.02** | **+0.54¢** |

**Trading books (settled n / P&L / per-trade / open) — PAPER only, separate from live above:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell6** | 311 | +$6.78 | **+2.18** | 13 | PROMOTE confirmed, strengthening |
| **mmsell11** | 208 | +$6.73 | +3.24 | 12 | PROMOTE confirmed, edge eased slightly this batch |
| mmsell10 | 99 | +$3.35 | +3.38 | 12 | 66% to its own gate (n≥150), positive batch |
| mmsell9 | 18 | +$0.96 | +5.3 | 4 | small n, positive |
| mmsell control (paper) | 3,731 | +$61.36 | +1.64 | 45 | negative batch this run (−12.75¢/trade) — watch, not alarming yet |
| mmsell2 (paper) | 1,592 | +$45.99 | +2.89 | 19 | negative batch (−7.6¢/trade) |
| mmsell1 (paper) | 2,425 | +$48.80 | +2.01 | 26 | negative batch (−5.1¢/trade) |
| mmsell3 (paper shadow) | 978 | +$14.77 | +1.51 | 13 | roughly flat batch |
| mmsell5 | 95 | −$0.86 | −0.9 | 1 | no new settlements |
| **mmsell4** | 164 | −$0.42 | **−0.26** | 12 | **KILLED (run #61)** — confirming further negative |
| mmsell7 | 44 | −$0.88 | −2.0 | 3 | negative batch, gate n≥150 (29%) |
| mmsell8 | 23 | −$1.13 | −4.9 | 5 | negative batch, gate n≥100 (23%) |
| **theta4** (fat-tail) | 37 | +$19.06 | +51.5 | **0** | no new activity this run |
| weather con (all) | 458 | −$10.54 | −2.30 | 14 | flat settled/P&L, +8 new opens |
| weather_concity | 62 | −$5.39 | −8.69 | 6 | flat settled/P&L, +4 new opens |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — quiet run: no new gate events, mmsell6/11 remain confirmed PROMOTE, mmsell4's KILL
verdict continues to confirm itself, and the whole mmsell control/1/2 cohort (not the promoted
variants) had a negative batch this run — worth watching but a single window, not a trend yet.**

Nothing crossed a gate this run. The one genuine resolution — xgame_tapes' apparent staleness —
turned out to be a real "no games right now" lull rather than a broken collector, confirmed from
its own log lines (clean polling, zero errors, zero games matched). Closing that thread.

mmsell control/mmsell1/mmsell2 (the older, unfiltered variants) all posted negative batches this
run (−12.75¢, −5.1¢, −7.6¢/trade) while mmsell3/6/11 stayed flat-to-positive — worth a glance
next run to see if this is noise or the start of something (small-n discipline: one 8-hour batch
on 3000+/1500+/2400+ n books is not enough to call a trend).

**Gate sweep (step 3b):** theta4 **37/80** (46%, no new activity) · **mmsell6
CLEARED-PROMOTE** · **mmsell11 CLEARED-PROMOTE** · **mmsell4 KILLED** (confirmed further) ·
mmsell7 gate n≥150 (29%) · mmsell8 gate n≥100 (23%) · mmsell9 gate n≥100 (18%) · mmsell10 gate
n≥150 (66%) · weather_concity **62/120** (52%, unchanged) · FREEZE **5/100** (not fired,
unchanged, 14 runs).

**Data (last-24h / latest CDT, ~8:02 PM run):** crypto_spot, crypto_ladder, weather forecasts/
obs/ensembles/buckets all fresh (7:54–8:02 PM ✓). xgame_matches still dark (expected — book
KILLED, collector-only). **xgame_tapes RESOLVED** — confirmed healthy via logs, was a real
games-lull not a failure (see headline).

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** quiet run, no gate events. mmsell6/mmsell11 still PROMOTE, mmsell4's KILL
confirming further. xgame_tapes concern resolved (healthy collector, real lull). mmsell
control/1/2 had a negative batch — watching, not yet a trend.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[mmsell6 AND mmsell11 still PROMOTE — top actionable item] mmsell6: n=311, +2.18¢/trade,
   strengthening further. mmsell11: n=208, +3.24¢/trade, edge eased slightly this batch but
   still 2.1x mmsell3's baseline.** Unchanged recommendation: a fable session should decide
   whether to promote one, both, or combine the mechanisms (mmsell6's 5-8¢ band, mmsell11's
   `htcmin=6` no-late-entry) into the live mmsell3 config.

2. **[mmsell4 · KILL verdict (run #61) confirming further] n=164, per-trade now −0.26¢ (was
   −0.06¢ at the gate) — the negative batch this run widened the gap below mmsell3, not
   narrowed it.** Recommend a fable session record the verdict in
   `docs/MMSELL_VARIANTS_THESIS.md`/`RESEARCH_JOURNAL.md` and decide whether to flip mmsell4 to
   collect-only. Not urgent — paper capital — but the gate is resolved and shouldn't sit
   unrecorded across multiple runs now.

3. **[mmsell control/1/2 · negative batch this run, watch not yet a trend] mmsell (control):
   −12.75¢/trade batch, mmsell1: −5.1¢/trade, mmsell2: −7.6¢/trade — all negative this window
   while mmsell3/6/11 stayed flat-to-positive on the same market flow.** Single 8-hour batch on
   large-n books (3000+/2400+/1500+) — small-n discipline says don't call this a trend yet, but
   worth a glance next run to see if it persists or reverses.

4. **[mmsell10 · watch, gate 66%] n=99/150, +3.38¢/trade cumulative, positive batch
   (+5.6¢/trade).** Still the "highest-value result" candidate per its own thesis if it holds
   above mmsell3 at gate. Getting close — likely resolves within 1-2 runs.

5. **[Live P&L · flat this run, no new live settlements] Total unchanged at +$2.02 (n=376).**
   Continue tracking; nothing to report until the next live fill settles.

6. **[theta4 · no new activity this run] Still 37/80 (46%) to its own n≥80 gate, no new
   settlements since run #61's batch of 7.** No action; continue tracking toward the gate.

7. **[idea-model queue · MMX/NEST] MMX's premise (extend the mmsell edge into new categories)
   should be built against whichever of mmsell6/mmsell11 gets promoted — unchanged, still the
   concrete next step once a promote decision is made.** NEST still behind theta4's n≥80 gate
   (46% there). RTPIN/BOXPIN behind unbuilt scraper infra. RATELAG behind a live Fed event.

8. **[weather_concity / con(all) · quiet, no new settles] concity −8.69¢/trade (52% to gate),
   con(all) −2.30¢/trade — both flat on settled/P&L this run, several new opens each.** Carry
   forward, not a gate event.

9. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 5 of the n≥100 trigger, unchanged
   across 14 runs now.** Standing background check, nothing to act on.

*(Changed this run: #1 mmsell6/mmsell11 — restated, mmsell6 strengthened further, mmsell11 eased
slightly but still clearly cleared. #2 mmsell4 — KILL confirming further, gap widening not
narrowing. #3 NEW — mmsell control/1/2 negative batch flagged as a watch item, explicitly not
yet called a trend. #4 mmsell10 — closer to gate (66%, was 61%). #5 live P&L — flat, no new
settlements. #6 theta4 — no new activity. #7 MMX — restated. #8 weather — restated, flat.
#9 restated/unchanged. DROPPED — the prior #8 xgame_tapes item: resolved this run via log
check, was a genuine games-lull not a broken collector, no longer worth tracking as a live
concern.)*
