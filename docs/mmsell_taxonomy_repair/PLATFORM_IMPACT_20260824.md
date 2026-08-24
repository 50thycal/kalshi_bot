# Platform Change Review — MMSELL settlement-taxonomy repair, 2026-08-24

```
PLATFORM CHANGE:  MARKET_TAXONOMY  coverage_2026_08_13 → settlement_repair_2026_08_24 (PROPOSED)
SEMANTIC EFFECT:  +194 series prefixes in SERIES_TYPES. Purely ADDITIVE: no existing prefix
                  changes type or mode, and KNOWN_TYPES / KNOWN_MODES are unchanged. What
                  changes is WHICH MARKETS a `mode=`/`mtype=` book admits.
CLASS:            I2 for the three arms whose eligible universe grows; I0 elsewhere, with a
                  written reason per experiment.
BOUNDARY:         UNKNOWN until the change actually deploys. `boundary_unknown=True`; the
                  measured activation instant is the worker boot that first serves the new
                  table, and `apply_new_epoch` must refuse until it is recorded.
STATUS:           NOT REGISTERED. This session cannot register it — see §7.
```

Current platform state, read 2026-08-24 (`{"type":"xos","command":"platform","id":"mmt-plat-1"}`):

| component | active revision | activated_at |
|---|---|---|
| **MARKET_TAXONOMY** | `coverage_2026_08_13` | 2026-08-13 18:09 |
| FEE_MODEL | `maker_rate_2026_08_11` | 2026-08-11 15:00 |
| *(8 others)* | — | not activated |

Active snapshot `id=1`, fingerprint `5c3720fca2fe36f0…`.

---

## 1. What actually changes

`SERIES_TYPES` goes from **164** rows to **358**. The added rows are the 194 accepted in
`REVIEW_20260824.md`. Concretely:

| | before | after |
|---|---|---|
| table rows | 164 | 358 |
| type names (`KNOWN_TYPES`) | 15 | **15 — unchanged** |
| mode names (`KNOWN_MODES`) | 3 | **3 — unchanged** |
| prefixes whose classification changes | — | **0** |

The last two lines are the load-bearing ones and are asserted in tests
(`test_the_repair_added_no_new_type_or_mode_NAME`,
`test_no_pre_existing_prefix_changed_its_classification`). Because no name is added to
`KNOWN_TYPES`/`KNOWN_MODES`, **config validation of every existing book spec is bit-for-bit
unchanged** — a `mtype=` value that is a typo today stays a typo. And because no existing
prefix moves, this is a taxonomy **expansion**, not a redefinition: no historical market is
re-labelled, and no previously-computed census is invalidated by re-interpretation.

What *is* a real semantic change: a book filtering on `mode=` or `mtype=` will, from the
activation instant, admit markets it previously could not see, because an unclassified series
is admitted by no allowlist filter.

## 2. Inventory of every reader of `SERIES_TYPES`

Enumerated from the code, not from documentation.

### 2.1 Runtime (worker) — `kalshi_bot/mmsell/market_types.py`

| reader | call site | effect of the repair |
|---|---|---|
| **book eligibility** | `mmsell/tracker.py:629` `_book_admits_series` → `classify(series)` | **MATERIAL.** This is the universe of every `mtype=`/`xmtype=`/`mode=` book. |
| **config validation** | `config.py:1681` — `mtype`/`xmtype`/`mode` values checked against `KNOWN_TYPES`/`KNOWN_MODES` | **NONE.** Neither frozenset changes. |
| **scan pre-filter** | `tracker.py:580` — an unclassified series is never pre-filtered away | **LATENT.** After the repair, 196 markets become `scheduled`/`discrete` and so become *eligible* to be skipped without an orderbook fetch when their inline mid is far outside every band. **Inert in production today**: `mmsell_prefilter_enabled` defaults to `False` and is not set in the deployed env. Recorded here so that enabling the pre-filter later is understood as landing on a different candidate stream than the one it was measured on. |
| **quote-parity diagnostic** | `tracker.py:826` — `in_play=(classify(series)[1] == IN_PLAY)` | **OBSERVATIONAL ONLY.** Feeds a decision table used to evaluate the proposed distrust rule; the repair makes it *more* accurate (fewer markets scored "blended only" because they were unknown). No trading behaviour. |

### 2.2 Research / ops readers — `scripts/mmsell_market_types.py`

`mmsell_market_types` (the type census), `mmsell_timing_study` (imports the table wholesale),
`mmsell_taxonomy_audit` (the census this whole workstream is gated on), and
`theta_settlement_labels` / `theta_tail_diagnosis` (import connection helpers only, **not** the
taxonomy).

These are read-only analyses. The consequence to state plainly: **a census run after this
change is not comparable, row for row, with one run before it.** That is intended — the point
of the repair is that the earlier census was scoring a seventh of its population as `unknown` —
but it means the pre-change outputs must be kept as historical records rather than overwritten.
`CENSUS_AND_MANIFEST_20260824.md` and `EVIDENCE_20260824.txt` preserve the pre-change census
and its evidence, with the runner commit SHA (`1b6d7bd5…`) and the exact invocation, so the
old numbers stay reproducible.

**No taxonomy output or spec hash already recorded elsewhere is edited by this change.** The
`MARKET_TAXONOMY:coverage_2026_08_13` revision, the snapshot it belongs to, and the
`docs/MMSELL_MARKET_TYPES.md` census remain exactly as they are, describing the table as it was.

## 3. Prefix ordering and shadowing

`classify()` is longest-prefix-wins, so table order is presentation only. Three orderings were
checked mechanically, and all three are asserted in
`tests/test_mmsell_taxonomy_repair_20260824.py::test_specific_prefixes_are_not_shadowed_by_the_broader_ones_the_repair_adds`.

**(a) A new prefix sitting under an EXISTING entry — 3 cases.** These are the dangerous ones:
a shorter new prefix must not capture an already-classified series.

| new (broader) | existing (more specific) | resolution |
|---|---|---|
| `KXINX` → price_strike/scheduled | `KXINXU` → price_strike/scheduled | `KXINXU` still wins its own tickers; same mode either way |
| `KXNASDAQ100` → price_strike/scheduled | `KXNASDAQ100U` → price_strike/scheduled | same |
| `KXCOD` → **outright**/in_play | `KXCODGAME` → **h2h**/in_play | `KXCODGAME` still wins; the **types differ**, so this is the sharpest case and is asserted explicitly |

**(b) A new prefix sitting under another NEW entry — 3 cases.** `KXEURUSD` ⊂ `KXEURUSDAW`,
`KXLEAGUESCUP1H` ⊂ `KXLEAGUESCUP1HTOTAL`, `KXNETFLIXRANKMOVIE` ⊂ `KXNETFLIXRANKMOVIERUNNERUP`.
Longest-prefix keeps each specific entry; asserted.

**(c) An existing prefix sitting under a new entry — 0 cases.** Nothing new is shadowed by a
broader incumbent.

An exhaustive sweep confirms the property that matters: **for every one of the 164 pre-existing
prefixes, the winning entry after the repair is still its own row.** Zero classifications
change.

### 3.1 Generic-prefix risk, and the one classification it cost

The repair refuses to add any prefix shorter than five characters. `KXMC` is the case that
rule cost: its evidence is unambiguous (Metacritic score read at a stated instant →
`scheduled`), and it stays `unknown` anyway, because four characters mapping to a
treatment-eligible mode would admit every future `KXMC*` series sight unseen.

Two **pre-existing** entries already carry that property: `KXRT` (rank_culture/scheduled) and
`KXUE` (econ_release/scheduled). They are **not changed here** — re-deciding a grandfathered
entry is a separate review with its own impact analysis — but they are recorded, in
`PRE_EXISTING_SHORT_PREFIXES` in the test file, so the exception is visible rather than
implied. A future `KXRT*`/`KXUE*` series would be swept into `scheduled` today.

## 4. Affected experiments and proposed dispositions

Discovery is from what the books actually read, cross-checked against Experiment OS
(`xos list`, `xos show mmsell-type-tight`, both 2026-08-24). Only one active experiment reads
this component for entry.

### 4.1 `mmsell-type-tight` — PAPER, v1 frozen 2026-08-16, arms `Tmmsell1/2/5/6`

Its independent variable *is* the contract-structure filter, so it reads `SERIES_TYPES`
directly. Per arm:

| arm | spec | universe effect | class | required action |
|---|---|---|---|---|
| `Tmmsell1` | `mtype=price_strike` | **GROWS.** +15 `price_strike` prefixes: KXINX, KXNASDAQ100, KXCOPPERD, KXSILVERD, KXGOLDW, KXGOLDMON, KXBRENTMON, KXEURUSD, KXEURUSDAW, KXUSDJPY, KXA100WS, KXH100WS, KXB200WS, KXH200MS (all `scheduled`) and KXBNBMINMON (`discrete`) | **I2** | **NEW_EPOCH** |
| `Tmmsell2` | `mtype=mention` | **UNCHANGED.** The repair adds no `mention` prefix. | **I0** | NO_ACTION — *reason:* the arm's allowlist names exactly one type, and the added rows contain none of it, so its eligible set is identical before and after. |
| `Tmmsell5` | `mode=scheduled+discrete`, `xmtype=event_stat+politics+announcement` | **GROWS.** 31 new `scheduled` + 19 new `discrete` prefixes, less the three excluded types → **+29 prefixes / +136 candidate markets** admitted. This is the arm the 2026-08-15 live failure was about, and the arm the MMSELL 2×2 treatment is modelled on. | **I2** | **NEW_EPOCH** |
| `Tmmsell6` | `mtype=player_prop+spread+exact_score+mention+price_strike+outright+rank_culture` | **GROWS.** **+69 prefixes / +458 candidate markets**, across six of its seven named types (spread 24, price_strike 15, rank_culture 11, outright 8, player_prop 6, exact_score 5; `mention` gains none). | **I2** | **NEW_EPOCH** |

An I2 is a *sample/environment boundary*: the old epoch's evidence closes at the measured
activation instant and never pools across it. That is the correct and unavoidable cost — three
of these arms will be selecting from a materially larger population, and pooling a pre-repair
`Tmmsell5` trade with a post-repair one would average two different universes.

**An I2 may not be discharged as an I3.** The scientific contract of `mmsell-type-tight` v1 is
not changing: the hypothesis, the independent variable and the gates are untouched. The
*environment* changed. A new **epoch** is the correct disposition; a new Version is not, and
`apply_new_epoch` is the helper to use.

### 4.2 `mmsell-scheduled-settle-live` — LIVE_CANARY, v1, arms `Lmmsell8`/`Lmmsell10`

**I0 / NO_ACTION**, and the reason is worth stating precisely because the experiment's *name*
suggests otherwise. Its arms are `Lmmsell8:lo=5,hi=12,only=BTCD+ETH+ASG+HRDERBY` and
`Lmmsell10:lo=5,hi=10,maxyes=7`. Neither carries `mtype=`, `xmtype=` or `mode=`, so neither
calls `classify()` on the entry path. `only=` is a raw series-substring allowlist that does not
consult the taxonomy at all — which is exactly the defect
`docs/RESEARCH_MMSELL_UNIVERSE_DECONFOUNDING.md` identified in that book. **Its eligible
universe is byte-identical before and after this revision.**

### 4.3 Every other active experiment — I0 / NO_ACTION

`mmsell-price-ceiling` (`mmsell10`), `mmsell-variants-2026-07` (`mmsell5/6/7/8/9`),
`mmsell-wide-control`, `mmsell-anchor-vol-entry` (`mmsellA4`), `mmsell-anchor-strangle`
(`mmsellA5`), `freeze-dark-window-pin`, `theta4-fat-tail`, `theta-tail-sell` (PAUSED).

Written reason, not "probably unaffected": none of these specs contains `mtype=`, `xmtype=` or
`mode=`, so none reaches `classify()` on its entry path; and the only other worker call sites
are the pre-filter (disabled in production) and the quote-parity diagnostic (observational).
Their candidate streams are unchanged.

Retired experiments never block activation and are historical only.

## 5. Impact on existing `mode=` books, stated plainly

`Tmmsell5` is the only book in the shipped config that defines itself by settlement **mode**.
Today it trades a `scheduled+discrete` universe that silently excludes 861 markets — 14.31% of
the non-crypto 5–7¢ population — not because they settle differently but because nobody had
classified them. After the repair it sees **136** of those — the 196 newly `scheduled` or `discrete`
markets, less the 60 its own `xmtype=event_stat+politics+announcement` blocklist drops.

That is a **better** book and a **discontinuous** one. Both are true, and the epoch boundary is
what keeps the second from corrupting the first: `Tmmsell5`'s pre-repair evidence stays valid
*for the universe it was collected in*, and its post-repair evidence starts a new count.

## 6. Recomputation of historical evidence

**Impossible, and it must not be attempted.** There is no normalizer that can restate a
pre-repair `Tmmsell5` fill as though the book had seen the larger universe: the counterfactual
trades were never offered to it, so their prices, fills and outcomes do not exist. This is
precisely why the disposition is I2/NEW_EPOCH rather than I1/RECOMPUTE — an I1 would require a
**named, registered** normalizer, and no honest one exists.

Historical taxonomy outputs are therefore **preserved, not restated**: the pre-change census
(6,018 / 14.31%), its evidence corpus and the runner SHA are committed in this directory, and
`MARKET_TAXONOMY:coverage_2026_08_13` stays exactly as recorded.

## 7. What this session did NOT do, and what must happen before merge

Registering a Platform Revision is a **write** to Experiment OS. The ops channel is read-only
against Postgres by design (`DATABASE_URL_RO`), and the `EXPERIMENT_OS_ISSUE_COMMAND` envelope
vocabulary covers issues only — it has no platform-revision action. **So this session cannot
register the revision, and did not.**

The consequence is a hard sequencing constraint on this PR:

> **Merging this PR before the revision is registered and its dispositions applied would change
> `SERIES_TYPES` under three active arms with no accounted impact record.** Under `NEW_ONLY`
> those arms keep trading, but their evidence would silently span an unrecorded boundary — the
> exact failure the impact engine exists to prevent.

The order, on a writable connection, using `kalshi_bot/experiment_os/platform_impact.py`:

1. `register_platform_revision(MARKET_TAXONOMY, "settlement_repair_2026_08_24", boundary_unknown=True)`
2. `affected_experiments(revision)` — verify it discovers `mmsell-type-tight` from the pinned
   snapshots. If it discovers more than §4 lists, §4 is wrong and this review must be redone,
   not overridden.
3. `propose_impact(...)` / `accept_impact(...)` for each row in §4 — I2 for `mmsell-type-tight`,
   I0 with the written reasons above for the rest.
4. Merge and deploy.
5. `establish_activation_boundary()` at the **measured** first-serve instant, then
   `activate_platform_revision(...)` and `apply_new_epoch(...)` for `mmsell-type-tight`.
   Unknown means unknown: `apply_new_epoch` refuses until the instant is recorded, and it must
   not be back-dated to the merge commit.

Do **not** force activation past the gate. A forced activation is durably recorded and leaves
every skipped experiment gate-blocked anyway.

## 8. Safety envelope of this change

- **No live exposure is created or expanded.** `LIVE_STRATEGIES` is empty in production (read
  2026-08-24, request `mmt-env-1`), so no book trades real money; the repair changes paper
  candidate selection only.
- **No live safeguard is weakened.** `KILL_SWITCH`, the risk caps and the entry filters are
  untouched. The one direction the change could have loosened — a catch-all default sweeping
  unknown series into an eligible mode — is refused by construction and asserted by test.
- **The conservative direction was taken at every boundary.** All three boundary calls
  (`KXYTVIEWSW`, `KXYTVIEWSHIGH`, `KXBNBMINMON`) place markets in `discrete` — the **control**
  side of the proposed 2×2 — rather than in the treatment-eligible `scheduled`.
- **The 5% unclassified threshold and the +2¢ minimum useful effect are untouched.**
  `UNCLASSIFIED_BAR = 0.05` in `scripts/mmsell_taxonomy_audit.py` is unchanged by this PR.
