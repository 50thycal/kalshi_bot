# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12.
As of run #56, the loop also pulls real live P&L for any LIVE book via a direct SQL query (total
$ P&L + WC/non-WC split — `mmsell_live` itself computes but never prints either). Suggestions
are **recommendations only** — the loop never acts on them; the user reviews and runs fable to
change anything. Newest snapshot replaces the one above it; the suggestion list carries over
run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-21 12:56 PM CDT (run #64)

**Quiet run — no gate crossings, no new findings.** Every book moved incrementally in its
existing direction; nothing changed status.

**mmsell6/mmsell11 — still PROMOTE, both edged up slightly:**
| book | n | ¢/trade (cum) | Δ this run |
|---|---|---|---|
| mmsell3 (control) | 1,006 | +1.66¢ | +5 n, +6.6¢/trade batch |
| **mmsell6** | 334 | +2.48¢ | +4 n, +6¢/trade batch |
| **mmsell11** | 235 | +3.66¢ | +5 n, +6.6¢/trade batch |

**mmsell4** unchanged (no new settlements) — still +0.52¢/trade cumulative, still below mmsell3,
KILL verdict from run #61 stands, still not yet recorded in the thesis docs.

**theta4** added 2 more settlements this run, another modest negative batch (−$0.52, avg
−$0.26/trade) — cumulative eased slightly to +37.1¢/trade (was +40.4¢) but remains strongly
positive. Now 41/80 (51%) to its own gate — past the halfway point. Opened its first new
position in a few runs (open: 0→1).

**Live P&L (real money — mmsell3):** unchanged for a fourth straight run — no new live
settlements.
| bucket | n settled | wins | total P&L | ¢/contract |
|---|---|---|---|---|
| non-WC | 204 | 187 | +$1.92 | +0.94¢ |
| World Cup | 172 | 157 | +$0.11 | +0.06¢ |
| **TOTAL** | **376** | **344** | **+$2.02** | **+0.54¢** |

**Trading books (settled n / P&L / per-trade / open) — PAPER only, separate from live above:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell6** | 334 | +$8.28 | **+2.48** | 17 | PROMOTE confirmed, incremental gain |
| **mmsell11** | 235 | +$8.61 | +3.66 | 17 | PROMOTE confirmed, incremental gain |
| mmsell10 | 111 | +$4.02 | +3.62 | 16 | 74% to its own gate (n≥150), positive |
| mmsell9 | 21 | +$1.14 | +5.4 | 9 | no new settlements |
| mmsell control (paper) | 3,832 | +$64.12 | +1.67 | 37 | positive batch |
| mmsell2 (paper) | 1,636 | +$48.40 | +2.96 | 19 | positive batch |
| mmsell1 (paper) | 2,486 | +$52.21 | +2.10 | 26 | positive batch |
| mmsell3 (paper shadow) | 1,006 | +$16.74 | +1.66 | 19 | positive batch |
| mmsell5 | 113 | −$0.32 | −0.28 | 0 | no new settlements |
| mmsell4 | 183 | +$0.96 | +0.52 | 18 | KILLED (run #61) — no new settlements, still unrecorded |
| mmsell7 | 45 | −$0.79 | −1.76 | 8 | no new settlements, gate n≥150 (30%) |
| mmsell8 | 23 | −$1.13 | −4.9 | 12 | no new settlements, gate n≥100 (23%) |
| **theta4** (fat-tail) | 41 | +$15.22 | +37.1 | 1 | modest negative batch, still strongly positive cumulative |
| weather con (all) | 472 | −$10.60 | −2.25 | 11 | +14 new settled, roughly flat |
| weather_concity | 68 | −$5.62 | −8.26 | 6 | +6 new settled, gate n≥120 (57%) |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — a genuinely quiet run: every gate-tracked book moved incrementally in its existing
direction, nothing crossed a threshold, live P&L is flat for a fourth straight run.**

The only thing worth a one-line note: mmsell10 (74%) and weather_concity (57%) are both getting
closer to their gates and will likely resolve within the next few runs — nothing actionable yet,
just a heads-up they're approaching.

**Gate sweep (step 3b):** theta4 **41/80** (51%) · **mmsell6 CLEARED-PROMOTE** · **mmsell11
CLEARED-PROMOTE** · **mmsell4 KILLED** (unchanged, still needs recording) · mmsell7 gate n≥150
(30%) · mmsell8 gate n≥100 (23%) · mmsell9 gate n≥100 (21%) · mmsell10 gate n≥150 (74%) ·
weather_concity **68/120** (57%) · FREEZE **5/100** (not fired, unchanged, 16 runs).

**Data (last-24h / latest CDT, ~12:56 PM run):** crypto_spot, crypto_ladder, weather forecasts/
obs/ensembles/buckets all fresh (12:41–12:55 PM ✓). xgame_matches still dark (expected — book
KILLED, collector-only). xgame_tapes still 0 rows/24h — consistent with the healthy-lull
explanation confirmed two runs ago, not re-flagging.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** quiet run, nothing crossed a gate. mmsell6/mmsell11 both edged up further.
mmsell10 (74%) and weather_concity (57%) both getting close to their gates. theta4 past the
halfway mark (51%) with a small negative batch, still strongly cumulative-positive. Live P&L
flat for a fourth run.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[mmsell6 AND mmsell11 still PROMOTE — top actionable item] mmsell6: n=334, +2.48¢/trade.
   mmsell11: n=235, +3.66¢/trade.** Both continue to edge up incrementally. Unchanged
   recommendation: a fable session should decide whether to promote one, both, or combine the
   mechanisms (mmsell6's 5-8¢ band, mmsell11's `htcmin=6` no-late-entry) into the live mmsell3
   config.

2. **[mmsell4 · KILL verdict — still not recorded, no change] n=183, +0.52¢/trade cumulative,
   still below mmsell3's +1.66¢.** No new settlements this run. Recommend a fable session record
   the verdict in `docs/MMSELL_VARIANTS_THESIS.md`/`RESEARCH_JOURNAL.md` — this has now sat
   unrecorded across 3 runs since resolving.

3. **[mmsell10 · getting close, gate 74%] n=111/150, +3.62¢/trade cumulative, positive batch.**
   Likely resolves within the next 2-3 runs. Still the "highest-value result" candidate per its
   own thesis if it holds above mmsell3 at gate.

4. **[weather_concity · getting close, gate 57%] n=68/120, −8.26¢/trade cumulative.** Also
   approaching its decision point — worth a closer look once it crosses n≥120.

5. **[theta4 · past halfway, small negative batch] n=41/80 (51%), −$0.52 this batch (2 trades),
   cumulative eased to +37.1¢/trade (still strongly positive).** No action; continue tracking
   toward the gate. Two negative batches in a row now (this run and run #63) — worth watching
   whether the tail-hit rate trend continues, though still well within expected fat-tail
   variance at this n.

6. **[Live P&L · flat for a fourth run] Total unchanged at +$2.02 (n=376) since run #61.**
   Continue tracking; nothing to report until the next live fill settles.

7. **[idea-model queue · MMX/NEST] MMX's premise (extend the mmsell edge into new categories)
   should be built against whichever of mmsell6/mmsell11 gets promoted — unchanged, still the
   concrete next step once a promote decision is made.** NEST still behind theta4's n≥80 gate
   (51% there).

8. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 5 of the n≥100 trigger, unchanged
   across 16 runs now.** Standing background check, nothing to act on.

*(Changed this run: #1 mmsell6/mmsell11 — restated, both edged up. #2 mmsell4 — restated,
unrecorded for 3 runs now. #3 mmsell10 — closer to gate (74%, was 72%). #4 weather_concity —
promoted to its own item now that it's over halfway (57%, was 52%) — previously bundled with
con(all) in a combined item. #5 theta4 — restated, second negative batch in a row noted but
still framed as expected variance. #6 live P&L — flat for a fourth run. #7 MMX/NEST — restated.
#8 restated/unchanged. weather con(all) — no longer carried as its own item since it's quiet and
not near a gate; folded into the books table only.)*
