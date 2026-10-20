# Platform Change Review — football coverage, 2026-10-12

```
PLATFORM CHANGE:  MARKET_TAXONOMY  settlement_repair_2026_08_24 → football_coverage_2026_10_12 (PROPOSED)
SEMANTIC EFFECT:  +24 NCAAF/NFL series prefixes in SERIES_TYPES, all in_play. Purely ADDITIVE: no
                  existing prefix changes classification; KNOWN_TYPES / KNOWN_MODES unchanged.
FINGERPRINT:      350e7f03ed72d68d3718dfa131aa90a5fb7cc54336c250c79df915c2ade322d1
                  (platform_commands.taxonomy_fingerprint() on this branch; 388 rows)
CLASS:            I2 / NEW_EPOCH for mmsell-type-tight (Tmmsell6's universe grows).
                  I0 / NO_ACTION, with a written reason, for every other active experiment.
BOUNDARY:         UNKNOWN until the change deploys. The CUTOVER command records the measured
                  boot instant of the first worker that serves this fingerprint.
OPERATOR:         Edit approved 2026-10-12 ("go ahead with the football taxonomy edit").
                  Activation (CUTOVER) is a separate hard stop and is NOT authorized by that.
```

## 1. What changes

24 college- and pro-football series that the bot was already paper-trading **unclassified**
(~12k paper trades 2026-08-09..10-05; `KXNCAAFSPREAD` alone 4,446) get a type and settle mode.
Each was classified from its **own live `yes_sub_title`**, read from production
`mmsell_candidate_ticks` on 2026-10-12, and Kalshi's rules text calls every one of them in-play
(`scripts/mmsell_series_rules_audit.py` 2026-10-05: 26/26 CONTRADICTS `unknown → in_play`).

| type | series |
|---|---|
| `h2h` | KXNCAAFGAME |
| `h2h_period` | KXNCAAF1H |
| `spread` | KXNCAAFSPREAD, KXNCAAF1HSPREAD, KXNCAAF2HSPREAD, KXNCAAF1QSPREAD, KXNFL1HSPREAD, KXNFL2HSPREAD, KXNFL1QSPREAD, KXNFL3QSPREAD, KXNFL4QSPREAD, KXNFLWINMARGIN |
| `total` | KXNCAAFTOTAL, KXNCAAFTEAMTOTAL, KXNCAAF1HTOTAL, KXNFLTEAMTOTAL, KXNFL1HTEAMTOTAL, KXNFL1QTOTAL, KXNFL2HTOTAL |
| `player_prop` | KXNFLFIRSTTD, KXNFLPASSTDS, KXNFLREC, KXNFLRECYDS, KXNFLRRYDS |

`KXNFLWINMARGIN` ("Baltimore by 7 to 14") is a margin band, so `spread`, not `exact_score`.

### Deliberately NOT in this revision

- **`KXNFL1HFT`** (half-time/full-time double, "Atlanta 1H / Tie game"). Its true type is
  `game_prop`, and `game_prop` is in `Kmmsell10`'s `bigtypes` (the LIVE cell-size canary rests a
  3-lot on it). Classifying it would change that experiment's paper arm assignment for this
  series. That is the cell-size experiment's decision, not a coverage edit's.
- **`KXNFL1H`**. Longest-prefix match would make the bare `KXNFL1H` row swallow `KXNFL1HFT` as an
  `h2h_period`, which is wrong. It waits with `KXNFL1HFT`.

Both stay `unclassified`, which no type or mode filter admits. Both are pinned by tests.

## 2. Every reader of `SERIES_TYPES`, and what this does to it

| reader | effect |
|---|---|
| book eligibility (`mmsell/tracker.py` `_book_admits_series`: `mtype=`/`xmtype=`/`mode=`) | **MATERIAL for `Tmmsell6` only** (§3) |
| `bigtypes` sizing (`MmSellTracker._book_contracts`) | **None.** The added types (`h2h`, `h2h_period`, `spread`, `total`, `player_prop`) are not in `Kmmsell10`'s `bigtypes=event_stat+mention+exact_score+outright+game_prop`. Pinned by test. |
| registry tier (`registry.state_of`) | The 24 move IDENTIFIED → IN_REVIEW (manifest untouched, so none is GRADUATED). Live and twin require GRADUATED (`MMSELL_LIVE_MIN_TIER=graduated`, `MMSELL_TWIN_APPLIES_LIVE_BARS=true`), so they refuse these series before and after. **No live or twin universe changes.** No production book carries a paper tier floor. |
| pre-filter (`mmsell_prefilter_trust_in_play`) | None: `unknown` and `in_play` are both not `scheduled/discrete`, so the same branch is taken. |
| quote-parity diagnostic, decision-context telemetry, livedash labels, catalog `contract_type` | Observational only. |

## 3. Affected experiments and dispositions

Production `MMSELL_VARIANTS` and `LIVE_STRATEGIES=Kmmsell10,Alimm1` read 2026-10-12 (ops
`cc-fbtx-env1`); experiment list ops `cc-fbtx-list`.

| experiment | state | class | action | reason |
|---|---|---|---|---|
| `mmsell-type-tight` | PAPER | **I2** | **NEW_EPOCH** | `Tmmsell6` (`mtype=player_prop+spread+…`) gains 15 prefixes — 10 `spread`, 5 `player_prop`. `Tmmsell1` (`price_strike`), `Tmmsell2` (`mention`) and `Tmmsell5` (`mode=scheduled+discrete`) gain nothing: all 24 are `in_play` and none is `price_strike`/`mention`. One arm's universe grows, so the epoch closes; pooling across it would average two universes. |
| `mmsell-price-ceiling-cell-size` | LIVE_CANARY | I0 | NO_ACTION | `Kmmsell10` has no `mtype`/`mode`; its `bigtypes` contains none of the added types; live and twin refuse all 24 at the GRADUATED tier bar before and after. |
| `mmsell-price-ceiling` | LIVE_CANARY | I0 | NO_ACTION | No `mtype`/`xmtype`/`mode`/`bigtypes`; live/twin tier bar unchanged as above. |
| `mmsell-price-ceiling-capacity` | LIVE_CANARY | I0 | NO_ACTION | Same. |
| `mmsell-price-ceiling-contest-cap` | LIVE_CANARY | I0 | NO_ACTION | Same. |
| `mmsell-price-ceiling-size-split` | LIVE_CANARY | I0 | NO_ACTION | Same. |
| `mmsell-reviewed-universe` | PAPER | I0 | NO_ACTION | `Rmmsell1/2` select by `onlyx=` raw series allowlist, which never calls `classify()`. |
| `mmsell-correlation-cap` | PAPER | I0 | NO_ACTION | `Gmmsell0/1`: no taxonomy filter. |
| `mmsell-wide-control` | PAPER | I0 | NO_ACTION | No taxonomy filter. |
| `liquidity-incentive-mm` | LIVE_CANARY | I0 | NO_ACTION | `Alimm1` uses its own classifier (`liquidity_incentive/economics.py`), not `SERIES_TYPES`. |
| `theta-tail-sell` | PAUSED | I0 | NO_ACTION | Not an mmsell book; does not read `SERIES_TYPES`. |

Retired experiments never block activation.

## 4. The unregistered 2026-09-06 change rides along — stated, not hidden

Commit `d166fb26` (2026-09-06, "Stop classifying European football as an unemployment print")
added 6 `KXUE*` soccer rows without a Platform Revision. The active registered revision's
fingerprint is `d1c08d7f…` (358 rows); the deployed table has been 364 rows, fingerprint
`70ac4e6a…`, since 09-06. A revision fingerprints the **whole** table, so this one necessarily
includes those 6 rows.

What that means for the dispositions above: **nothing changes at this cutover because of them.**
The worker has served them since 09-06; the cutover boundary only separates "before football"
from "after football". The 09-06 boundary itself is real and unrecorded (it shrank `Tmmsell5`'s
`scheduled` universe and added `KXUEFASCSPREAD` to `Tmmsell6` mid-epoch). Recording it after the
fact is an integrity question for `mmsell-type-tight`'s owner, raised as an Experiment OS issue,
not something this revision may back-date.

## 5. Recomputation of historical evidence

None, and none attempted. A football market a `Tmmsell6` book never saw cannot be restated as
though it had; there is no normalizer, so I2/NEW_EPOCH rather than I1/RECOMPUTE.

## 6. Order of operations (the merge order is not advisory)

1. `REGISTER_REVISION` — `MARKET_TAXONOMY` `football_coverage_2026_10_12`, fingerprint above,
   status pending. Via `EXPERIMENT_OS_PLATFORM_COMMAND` (one envelope per worker boot).
2. `RECORD_IMPACTS` — the 11 rows of §3.
3. Merge and deploy.
4. **Hard stop — operator confirmation required.** `CUTOVER` with
   `expect_taxonomy_fingerprint` = the fingerprint above and `new_epoch_experiments=["mmsell-type-tight"]`.
   The command defers (consumes nothing) until a worker that really serves this table boots.

## 7. Safety envelope

- **No real-money exposure changes.** All 24 series stay below the GRADUATED tier bar on live and
  twin; graduating any of them is a separate manifest PR with its own review.
- **No safeguard is weakened.** Tier bar, `KXNFLSPREAD` live pause, caps and kill switch untouched.
- **No new type or mode name**, so every book spec validates exactly as before.
- **Additive only**: no previously classified prefix moves (tested).

## 8. Status (2026-10-17)

Steps 1–3 are done:

1. `REGISTER_REVISION` (`fbtx-register-20261012`): SUCCEEDED, revision pending.
2. `RECORD_IMPACTS` (`fbtx-impacts-20261012`): SUCCEEDED.
3. Merge and deploy: PR #599, merged 2026-10-13 00:04Z.

Step 4, **`CUTOVER`, has not been sent.** It is awaiting the operator's explicit go. Until then
`Tmmsell6` trades the 15 added football prefixes inside its old epoch. That is the pooling hazard
§3 exists to prevent, and it grows each day the cutover waits. The payload is unchanged:

- `revision = MARKET_TAXONOMY:football_coverage_2026_10_12`
- `expect_taxonomy_fingerprint = 350e7f03ed72d68d3718dfa131aa90a5fb7cc54336c250c79df915c2ade322d1`
- `new_epoch_experiments = ["mmsell-type-tight"]`
- `actor_role = PLATFORM_CHANGE_REVIEW`

It goes through `EXPERIMENT_OS_PLATFORM_COMMAND`, which redeploys the worker once.

**2026-10-20.** Four experiments registered after §3 have been added as I0/NO_ACTION and applied:
`mmsell-rain-season`, `mmsell-temperature-season`, `mmsell-weather-bands` and `mmsell-win-set`
(`pcr-fbtx-impacts-20261020`, impacts #44–#47). The activation gate now reads `safe`. The taxonomy
fingerprint on `b3ee7fb` is still `350e7f03…`, so the payload above is unchanged and the cutover
can run. It still needs the operator's go. The reasons for each row are in
`docs/EXPERIMENT_OS_PLATFORM_IMPACT.md`.
