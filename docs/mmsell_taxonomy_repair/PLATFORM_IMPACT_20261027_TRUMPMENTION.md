# Platform Change Review — MARKET_TAXONOMY `trumpmention_2026_10_27`

**Owner decision** (Calvin, 2026-10-27, session `01D3m7Af5FnpLDG5eDQ6JJRL`): add the Trump
word-mention markets to the WIN-set book now, restarting it (`mmsell-win-set-2`). This record is
the platform half of that change. Evidence: `docs/MMSELL_WIN_SET_EXPANSION_RESEARCH.md` §2.

```
PLATFORM CHANGE: MARKET_TAXONOMY football_coverage_2026_10_12 → trumpmention_2026_10_27
FINGERPRINT:     9b3b8873f61ddf2d2d82461e4eecc82b078aca7284fb19c42dfa03e090d05ae5
                 (platform_commands.taxonomy_fingerprint() on this branch; 389 rows)
SEMANTIC EFFECT: three edits, one change:
                 1. SERIES_TYPES + ("KXTRUMPMENTION", "mention", DISCRETE). By longest prefix this
                    also classifies KXTRUMPMENTIONB.
                 2. registry/series_manifest.json: KXTRUMPMENTION graduated (the first series
                    admitted after the PR #338 seed); KXTRUMPMENTIONB held in_review so it does
                    not inherit the graduation by prefix.
                 3. regimes.SUBJECT_SPLIT_SERIES + KXTRUMPMENTION (contestkey=split counts each
                    word as its own contest, as KXTRUMPSAY). Exact-name list: B is not split.
                 The fingerprint hashes only (1); (2) and (3) are stated here because they ship
                 in the same deploy and the same boundary.
REAL MONEY:      yes. The global MMSELL_LIVE_MIN_TIER=graduated bar admits KXTRUMPMENTION to
                 Kmmsell10 live (3-lot: mention is in its slow cell). Owner-approved 2026-10-27
                 ("approving those markets for real money also lets the old book trade them").
PRECONDITION:    football_coverage_2026_10_12 ACTIVE first (owner-confirmed 2026-10-27;
                 `fbtx-cutover-20261027` failed on the ops Railway token, nothing set). This
                 revision's fingerprint includes the football rows, so it registers after #13.
BOUNDARY:        UNKNOWN until the change deploys; CUTOVER records the first serving boot.
```

## Dispositions (15 active experiments pinned to MARKET_TAXONOMY)

| experiment | state | class | action | reason |
|---|---|---|---|---|
| `mmsell-price-ceiling-cell-size` | LIVE_CANARY, live | **I2** | **NEW_EPOCH** | `Kmmsell10` (no `universe=` key) is refused KXTRUMPMENTION at the global tier bar today and admitted after, at 3 contracts (`mention` ∈ `SLOW_CELL_TYPES`); its contest key goes per word. A changed live candidate population. |
| `mmsell-type-tight` | PAPER | **I2** | **NEW_EPOCH** | Today unclassified, which no type allowlist admits. After, `Tmmsell2` (`mtype=mention`), `Tmmsell6` (mtype includes mention) and `Tmmsell5` (`mode=discrete`) gain KXTRUMPMENTION and KXTRUMPMENTIONB. |
| `mmsell-price-ceiling-size-split` | LIVE_CANARY, stood down | **I2** | **NEW_EPOCH** | Paper `Jmmsell10` (`contestcap=1,contestkey=split`, no universe filter) already trades KXTRUMPMENTION; its contest goes from one per appearance to one per word. |
| `mmsell-win-set` | LIVE_CANARY, live | I0 | NO_ACTION | `onlyx=` the seven exact series; excludes both. Its successor is registered after this revision. |
| `mmsell-price-ceiling` | LIVE_CANARY, stood down | I0 | NO_ACTION | No type, mode, `bigtypes`, universe or contest key on `Cmmsell10`/`mmsell9`/`mmsell10`; live and twin inactive. |
| `mmsell-price-ceiling-capacity` | LIVE_CANARY, stood down | I0 | NO_ACTION | `Dmmsell10` carries no taxonomy key; live and twin inactive. |
| `mmsell-price-ceiling-contest-cap` | LIVE_CANARY, stood down | I0 | NO_ACTION | `Fmmsell10` uses the shipped (event) contest key, not split. |
| `mmsell-correlation-cap` | PAPER | I0 | NO_ACTION | `Gmmsell0/1` carry no taxonomy keys; `Gmmsell1` uses the event key. |
| `mmsell-reviewed-universe` | PAPER | I0 | NO_ACTION | Exact `onlyx=` list excludes both. |
| `mmsell-wide-control` | PAPER | I0 | NO_ACTION | The control book has no filters; unchanged candidate stream. |
| `mmsell-rain-season` | PAPER | I0 | NO_ACTION | `onlyx=KXRAIN`. |
| `mmsell-temperature-season` | PAPER | I0 | NO_ACTION | `only=KXHIGH+KXLOW`, no substring match. |
| `mmsell-weather-bands` | PAPER | I0 | NO_ACTION | `only=KXHIGH` / `KXHIGHT`, `onlyx=KXRAIN`. |
| `liquidity-incentive-mm` | LIVE_CANARY, live | I0 | NO_ACTION | `Alimm1` uses its own classifier (`liquidity_incentive/economics.py`), not SERIES_TYPES, the registry or regimes. |
| `theta-tail-sell` | PAUSED | I0 | NO_ACTION | Reads neither SERIES_TYPES nor the manifest. |

Regime, correlated-date caps and skip lists are unchanged: the regime is a prefix rule
(`KXTRUMP` → Politics), Politics is neither correlated (Elections) nor contest-grouped.

## Addendum (2026-10-27 03:05Z): merged before the football cutover

PR #676 was merged by the owner at 03:01:10Z, before steps 1–4 below could run (the ops token
still fails). The worker rebooted on `4245de9c` — its boot-time `kalshi_limits` event is
**03:02:20Z** (ops `m676-boot-2`). Production has served fingerprint `9b3b8873…` since then.
Consequences, measured against the code:

- **#13 can never cut over.** Its CUTOVER needs a worker serving `350e7f03…`
  (`platform_commands.check_preconditions`); no deployed worker will again. It stays
  PENDING. Same shape as #12 (`docs/EXPERIMENT_OS_PLATFORM_IMPACT.md`, "#12 cannot cut over").
- **This revision supersedes the ACTIVE taxonomy revision directly**, carrying #13's 24 football
  prefixes as well. Every #13 disposition (`PLATFORM_IMPACT_20261012.md` §4) is compatible with
  the table above: `mmsell-type-tight` is NEW_EPOCH in both; every other #13 row is I0, and the
  rows above that are I2 stay I2. Each row's reason must name both changes when recorded.
- **The boundary is 03:02:20Z, but CUTOVER stamps the boot that runs it.** Evidence between the
  two accrues in the old epochs of the three I2 experiments. Size that window at cutover (live
  KXTRUMPMENTION fills on `Kmmsell10`; `Jmmsell10` / `Tmmsell*` paper on the new series) and record
  it as an Experiment OS issue, not a re-dated boundary.
- **`mmsell-type-tight` stays evidence-blocked after this cutover**: #13's ACCEPTED, never-applied
  NEW_EPOCH still blocks it (`platform_impact.evidence_block_reasons`), and the platform transport
  has no exempt action (`exempt_impact` exists in Python only). Paper only. **OWNER DECISION**:
  add an `EXEMPT_IMPACT` platform action, or accept the block.
- **Real money:** `Kmmsell10` may trade KXTRUMPMENTION live at its 3-lot from 03:02:20Z, as
  approved. Nothing else live changed: `Nmmsell10` is `onlyx=` the seven series; `Vmmsell10` is not
  armed.

## Sequence (each send is one ops `env` act; every one needs a working ops Railway token)

Revised by the addendum: steps 1 and 5 are void.

1. ~~`CUTOVER` football #13~~ — void; #13 can no longer cut over.
2. `REGISTER_REVISION` `MARKET_TAXONOMY` `trumpmention_2026_10_27`, fingerprint above,
   `safety_class=I2`, `pr_ref` #676; the description names both the football prefixes and
   KXTRUMPMENTION.
3. `RECORD_IMPACTS` — the 15 rows above, each reason extended per the addendum.
4. **Hard stop — owner confirms activation.** `CUTOVER` with
   `expect_taxonomy_fingerprint = 9b3b8873…` and
   `new_epoch_experiments = ["mmsell-price-ceiling-cell-size", "mmsell-type-tight",
   "mmsell-price-ceiling-size-split"]`. Production already serves the fingerprint, so it runs on
   the next boot. Then the drift-window issue (addendum).
5. ~~Merge and deploy this PR~~ — done 03:01:10Z.
6. `REGISTER_PACKAGE mmsell-win-set-2-canary` (pins the new taxonomy).
7. **Hard stop — owner confirms `ARM_CANARY`** (approved_by Calvin), then the env act:
   `MMSELL_VARIANTS` replace the `Nmmsell10` spec with `Vmmsell10` (after `Kmmsell10`, plus the
   `Qmmsell10` paper spec), `LIVE_STRATEGIES` `Nmmsell10` → `Vmmsell10`,
   `MMSELL_LIVE_SHARED_MARKET_PAIRS=Kmmsell10+Vmmsell10:6:6.00`. `Nmmsell10`'s live and twin
   deployments stay open so its open positions settle on the record.
