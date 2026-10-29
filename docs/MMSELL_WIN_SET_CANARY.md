# mmsell WIN-set live canary — beside Kmmsell10, sharing markets

**Workstream:** WS-027. **Package:** `mmsell-win-set-canary`
(`kalshi_bot/experiment_os/mmsell_win_set_canary.py`). **Experiment key:** `mmsell-win-set`.
**Owner decision (2026-10-15):** run a live canary restricted to the categories that win, keep
`Kmmsell10` running, let both hold a shared market inside a registered per-market bound, and
attribute P&L per book. That approved the DESIGN. `ARM_CANARY` stays a hard stop.

Experiment OS is canonical for this experiment's state. This document is the pre-registration.

## 1. The question and the evidence

Does the mmsell price-ceiling rule, restricted to the categories that won on **both** paper and
live, earn a positive per-contract **live** return — i.e. does restricting the universe stop
paying the sports/crypto paper-to-live fill gap?

Evidence (`docs/MMSELL_LIVE_CANARY_PATTERNS.md` check 1, PR #636), not restated in XOS: WIN set
+4.67¢/contract on 277 independent paper markets; WIN − LOSE +3.53¢ (90% [+1.81, +5.14]); live
weather +3.8¢, index +4.2¢, Politics +5.5¢. Expected value is small: ~$5/month per contract of
size, ~$15/month at this 3-lot. The point is to stop paying the fill gap, not to reach $100.

## 2. The book

| item | value | why |
|---|---|---|
| live / twin / paper tags | `Nmmsell10` / `Nmmsell10_pt4` / `Pmmsell10` | fresh and prefix-safe against every production tag (ops `ws027-tags-2`); the paper tag cannot prefix-match the live tag |
| band | `lo=5,hi=10,maxyes=7` | the evidence book's (`mmsell10`) band |
| universe | `onlyx=KXFEDMENTION+KXINX+KXINXU+KXNASDAQ100U+KXRAIN+KXTRUMPSAY+KXWCMENTION`, `universe=graduated` | §3 |
| contest | `contestcap=1,contestkey=split` | the incumbent's live contest semantics |
| size | `size=3` | the owner's "~$15/month at a 3-lot" framing; the incumbent's per-market bound |
| exit | hold to settlement | structural for mmsell |

Book spec, appended to `MMSELL_VARIANTS` **after** `Kmmsell10`:
`Nmmsell10:lo=5,hi=10,maxyes=7,contestcap=1,contestkey=split,size=3,universe=graduated,onlyx=KXFEDMENTION+KXINX+KXINXU+KXNASDAQ100U+KXRAIN+KXTRUMPSAY+KXWCMENTION`

## 3. Universe: the WIN set, as live can actually trade it

`category_of` (`scripts/live_canary_patterns.py`) defines the WIN categories. Two live bars then
apply, and neither is weakened: `mmsell_live_min_tier="graduated"` and the series pauses.

| category | live-eligible series (graduated, not paused) | not traded, and why |
|---|---|---|
| weather | KXRAIN | KXHIGH/KXLOW temperature: on the global skip list (paper too) and refused live |
| index | KXINX, KXINXU, KXNASDAQ100U | — |
| Politics regime | KXTRUMPSAY | KXTRUMPSAYCOMPANY in_review; KXTRUMPSAYMONTH barred |
| mentions | KXFEDMENTION, KXWCMENTION | — |

**`KXYTVIEWSHIGH` is excluded.** `category_of` maps it to weather by the substring `HIGH`; it is
YouTube view counts. Recorded here rather than inherited. Any change to the list is a new
Version — CI fails if the manifest's graduated WIN set drifts from the package's list.

Overlap: `Kmmsell10` already trades these series — 25 markets in its first 5 days, 64% contract
fill rate (`ws027-kfill-1`). Most of this book's markets will therefore be **shared**.

## 4. Sharing a market with Kmmsell10

- `MMSELL_LIVE_SHARED_MARKET_PAIRS=Kmmsell10+Nmmsell10:6:6.00`. Empty by default; every other
  tag keeps the XOS-000040 `gate:held` behaviour byte for byte.
- For the listed pair only, `gate:held` and the any-tag in-flight check let one tag enter a
  market the other holds or rests on. Every other gate binds. A holder outside the pair, or
  contracts on the market no order of the pair explains, still refuses.
- **Combined bound:** held + still-working contracts of both tags, plus this clip, ≤ 6 contracts
  and ≤ $6.00 (`gate:shared_bound`). 6 = Kmmsell10's largest clip (3) + this book's (3); $6.00 =
  the sum of the two registered per-market bounds ($3.00 + $3.00).
- **Queue:** both may rest at the same no-bid; neither nets or cancels the other's orders.
  Kmmsell10 is visited first per market (variant order), so it keeps priority. Per-tag
  shared-market fill rate is in `live_fill_rate_pct` provenance (`shared_markets`).
- **Closeout:** reduce-only by tag — at most the closing tag's own filled quantity.
- **P&L:** per fill, per tag (`docs/LIVE_FILL_ATTRIBUTION.md`). Requires that revision ACTIVE.

## 5. Gates and envelope (frozen in the Version at registration)

- **Promotion (`paper_to_live_canary`, re-evaluated by `arm_live_canary`):** ≥ 30 settled paper
  trades on `Pmmsell10` and `pnl_cents_per_contract > 0`. A smoke bar — the evidence is check 1.
  About ten days at WIN flow.
- **Keep (`live_canary_keep`):** the incumbent's contract on this arm — sample 150 settled live
  contracts, horizon 600, stop on loss ≤ −$15, any single market losing > $3.00, twin paired gap
  beyond ±0.5¢, twin win-rate gap > 5pp; pass on live ¢/contract > 0 and win-rate gap ≤ 1pp.
- **Envelope:** 3-lot, $3.00/order and /market, combined 6 / $6.00 with Kmmsell10, open cap 40
  (the global `MMSELL_LIVE_MAX_OPEN_POSITIONS`), expected ~$28 at risk, worst case $112.80,
  canary loss budget $15. Sets only `MMSELL_LIVE_SHARED_MARKET_PAIRS`; no global knob moves.

**Reading the result.** This book usually rests *behind* Kmmsell10. It fills when flow clears
Kmmsell10's clip first — larger, possibly better-informed flow. A live result below the paper
evidence is therefore partly a queue-position effect; the per-tag shared-market fill rate and
the twin gap are what separate the two.

## 6. Arming — Phase 3 preflight (Live Ops), every line must hold

1. `METRICS_ENGINE:live_fill_attribution_2026_10` is **ACTIVE** (`xos platform`).
2. The WS-027 PR is merged with its tests; `REGISTER_PACKAGE mmsell-win-set-canary` SUCCEEDED and
   `Pmmsell10` is in `MMSELL_VARIANTS` (paper-scope append).
3. `xos show mmsell-win-set`: the promotion gate's dry run is PASS.
4. Account breakers re-read: `live_total_exposure` headroom under `MAX_TOTAL_EXPOSURE` exceeds
   this book's expected footprint; no `gate:total_exposure` / `gate:daily_loss` in 14 days.
5. **`ARM_CANARY` — the owner confirms in words that name it.** Then, in ONE env act (all three,
   or the incumbent is refused on every market this book enters first): append the `Nmmsell10`
   spec **after** `Kmmsell10` in `MMSELL_VARIANTS` (read the live value, never retype it), add
   `Nmmsell10` to `LIVE_STRATEGIES` (keep `Kmmsell10`, `Alimm1`), set
   `MMSELL_LIVE_SHARED_MARKET_PAIRS=Kmmsell10+Nmmsell10:6:6.00`.
6. Readback: twin epoch row for `Nmmsell10_pt4`; `xos enforcement`, `readiness`; after 24h no
   silent arm; the first settled shared market reconciles (Σ per-tag P&L = positions).

## 7. Owner decisions (decided 2026-10-16, both as recommended — WS-027)

- **D1 — per-book budgets.** `docs/workstreams/ACTIVE.md` parks per-book live budgets as a
  prerequisite for a second concurrent live canary. Measured: the account breakers have not
  bound in 14 days, and this book's footprint is ~$28. Recommendation: arm without them, with
  preflight line 4 as the guard; build them only if a breaker starts binding.
- **D2 — clip size.** 3-lot (as registered) or 1-lot (≈$5/month, a third of the tail). The
  Version freezes it at registration, so this is decided at merge. Recommendation: 3-lot.

## 8. v2 — the owner's waiver of the paper smoke bar (2026-10-16)

The owner directed, in session `01D3m7Af5FnpLDG5eDQ6JJRL`, that the canary arm now rather than
after ~30 settled paper trades ("don't wait 10 days … just make the new live canary trading";
"you can arm it now … as I give explicit permission"). A frozen gate cannot be edited, so the
waiver is a new Version: package `mmsell-win-set-owner-waiver`
(`mmsell_win_set_canary.apply_owner_waiver`).

- **Changes:** the promotion bar only. v2's bar passes while `Pmmsell10` paper has not lost
  more than $3.00 (one market's bound) since v2. No sample floor.
- **Unchanged, verbatim:** arm, tags, book spec, risk envelope, combined 6 / $6.00 bound, and
  the live keep gate (loss budget −$15, $3.00 per market, twin gaps). Those are what stop the
  canary; the waiver touches none of them.
- **Refuses:** outside PAPER; past v1 (runs once); and once v1's deciding metric
  (`pnl_cents_per_contract`) has been recorded at or below zero — a waiver of a sample floor
  does not overrule negative paper evidence.
- **Sequence:** `REGISTER_PACKAGE mmsell-win-set-owner-waiver` → `ARM_CANARY
  mmsell-win-set-canary` (approved_by the owner) → the §6 line 5 env act → §6 line 6 readback.

## 9. Restart with KXTRUMPMENTION — `mmsell-win-set-2` (2026-10-27)

Owner decision (session `01D3m7Af5FnpLDG5eDQ6JJRL`): add the Trump word-mention markets now and
restart the book rather than wait for its checkpoint. A live experiment's exact universe is
frozen, so this is a successor (`kalshi_bot/experiment_os/mmsell_win_set_2.py`, package
`mmsell-win-set-2-canary`) on fresh tags — paper `Qmmsell10`, live `Vmmsell10`, twin
`Vmmsell10_pt4` — with everything but the universe imported from this book: band, contest
semantics, 3-lot, envelope, keep gate and the §8 owner-waiver bar. Universe: the seven series
plus KXTRUMPMENTION, which the same change classifies, graduates and subject-splits
(`docs/mmsell_taxonomy_repair/PLATFORM_IMPACT_20261027_TRUMPMENTION.md`). KXTRUMPMENTIONB is held
in_review so it does not inherit the graduation by prefix. The pair becomes
`Kmmsell10+Vmmsell10:6:6.00`; `Nmmsell10` drains on the record.

Its record at restart (10.5 days live): 29 markets filled, 26 settled, 1 loss, +$2.31 — about
+3.0c/contract against +4.7c expected, live within $0.22 of its paper twin.

## 10. Joint restart without sports and other — `mmsell-price-ceiling-cell-size-2` + `mmsell-win-set-3` (2026-10-29)

Owner decision (session `01D3m7Af5FnpLDG5eDQ6JJRL`), from the first 72h scorecard
(`docs/mmsell/scorecards/2026-10-29.md`): restart Kmmsell10 without sports and other
categories, restart this book with it, and arm after about a day of paper.

| | new incumbent | new WIN-set book |
|---|---|---|
| package | `mmsell-cell-size-2-canary` (`successor_mmsell10_cell_size_2.py`) | `mmsell-win-set-3-canary` (`mmsell_win_set_3.py`) |
| live / twin / paper | `Xmmsell10` / `Xmmsell10_pt4` / `Smmsell10` | `Ymmsell10` / `Ymmsell10_pt4` / `Zmmsell10` |
| universe | `only=` weather, index, politics, mention substrings, `universe=graduated` | the seven series of `mmsell-win-set-2` less KXWCMENTION |
| sizing | 3 on the slow-information cell, 1 elsewhere (unchanged) | 3 (unchanged) |
| promotion bar | the predecessor's: realizable c/trade > 0 on `Smmsell10` (~1 day) | the owner-waiver bar (unchanged) |
| keep gate | −$30, $3.00 per market (unchanged) | −$15, $3.00 per market (unchanged) |

The pair `Xmmsell10+Ymmsell10:6:6.00` is registered in BOTH drift baselines, which is why the
two restart together: a book's baseline names its partner, and a tag may sit in only one pair.

**What it means in practice.** Live already trades only graduated series, and nine series in
the kept categories are graduated. So the two books now trade nearly the same markets, up to 6
contracts combined on each. Kmmsell10's sports (+$2.23) and other (−$1.32) results are given up.

**Sequence.**
1. Merge; then `REGISTER_PACKAGE` each. The experiment transport takes one envelope per boot,
   so that is two sends (paper scope).
2. Paper env act: append `Smmsell10` and `Zmmsell10` specs to `MMSELL_VARIANTS`. They are not in
   `LIVE_STRATEGIES`, so they run as paper.
3. About a day later, check that `xos show mmsell-price-ceiling-cell-size-2` reads a promotion
   PASS.
4. **Owner hard stop.** `ARM_CANARY` each (two sends, back to back), then one env act:
   - append `Xmmsell10` then `Ymmsell10` to `MMSELL_VARIANTS`, keeping every old entry
     (stood-down books are compared in full);
   - `LIVE_STRATEGIES`: `Kmmsell10,Vmmsell10` → `Xmmsell10,Ymmsell10` (`Alimm1` kept);
   - `MMSELL_LIVE_SHARED_MARKET_PAIRS=Xmmsell10+Ymmsell10:6:6.00`.
5. Readback, then the 24h silent-arm check. Delete the Kmmsell10 manual stop routine.

`Kmmsell10` and `Vmmsell10` drain on the record and keep their drift baselines. Kmmsell10's
I2 impact record (#54) stays accepted and unapplied; that experiment is stood down.
