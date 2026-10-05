# The series rules audit — retiring the 138-row review debt

**Started 2026-09-06.** Code: `scripts/series_rules_audit.py` (ops-runnable),
`scripts/series_registry_review.py` (the backlog it works through). Tests:
`tests/test_series_rules_audit.py`. Registry: `docs/SERIES_REGISTRY.md`.

## What is being audited, and why it is owed

`kalshi_bot/registry/series_manifest.json` graduated 138 series carrying
`rules_reviewed_at: null`. PR #338 seeded them mechanically — every series with ≥20 settled
markets of our own AND a market-type classification. **That bar proves we have DATA about a
contract. It never proved anyone read how it settles**, and the two are independent:
`KXNFLSPREAD` cleared it with 1,486 settled markets and −$151.26.

The audit asks one question per series: **does the settlement mode recorded in `SERIES_TYPES`
match what Kalshi's own rulebook says?**

## The three answers, and why the third one matters most

| verdict | meaning | consequence |
|---|---|---|
| `CONFIRMS` | the evidence names the mode we recorded | the review is discharged; the row may take a `rules_reviewed_at` |
| `CONTRADICTS` | the evidence names a **different** mode | a real finding — the series has been traded under the wrong settlement model, and every book selecting on `mode=` has been picking it up or missing it wrongly |
| `INSUFFICIENT` | the evidence does not decide | the row **stays unreviewed** |

`INSUFFICIENT` being reachable is the entire reason this is safe to automate. A tool that
always produced a verdict would launder a machine's guess into a human's signature on
`rules_reviewed_at` — the precise failure the two-part graduation bar exists to prevent. Only
`CONFIRMS` rows reach `--emit-patch`; a contradiction needs the taxonomy fixed before anything
is signed, and an insufficient row has nothing to sign.

**The audit changes no state.** It emits evidence. A human opens the PR that records the
verdicts, and merging that PR is the review — the manifest still moves only by diff.

## The evidence rule is borrowed, not reinvented

`scripts/mmsell_taxonomy_audit.py` already derives a settlement mode from Kalshi's
`settlement_source` field and its rules text, using patterns hand-tuned against the real corpus
and carrying their own failure history — an early draft read a bare "at 8:10 PM EDT" as
`scheduled` and so proposed `scheduled` for MLB player props, because that is a game *start*
time. Re-deriving those patterns would mean re-making those mistakes, so this imports them.

The **question** differs: that script *proposes* a mode for an unclassified prefix; this one
*verifies* a mode already recorded. Only a verification can tell a correct taxonomy entry from
an incorrect one.

**Evidence is counted over DISTINCT RULE DOCUMENTS, never over markets.** Settlement semantics
are a property of the series: one rule document answers for every market under the prefix, but
it answers once. Counting per market is how run `tax-6` reported "100% of 46 texts" from a
single document.

Documents that disagree with each other **refuse** a verdict — either the series has no single
settlement semantics, or the sample spans a rule change and we cannot say which applies.

## A total fetch failure is not a result

`fetch_series_text` swallows its own HTTP errors and returns an empty payload. A runner with no
route to Kalshi therefore produces 138 rows of `INSUFFICIENT` / "no rules text retrieved" —
each individually plausible, collectively an infrastructure failure, and exactly the shape a
reader skims past because every line looks reasonable.

This was observed for real on the first run: the Claude sandbox's network policy blocks
`api.elections.kalshi.com`, and the report read precisely that way. So the audit checks whether
**zero markets were retrieved for every series** and, if so, prints `!! NOT A RESULT` across the
header and **refuses to emit a patch at all** — an empty patch reads as "nothing confirmed",
which is a finding, and it is not one.

The guard deliberately does not fire on a single unreachable series among many; that is a
genuine per-series gap and masking it would hide real findings.

## Where the audit runs

**Through the ops channel only.** It needs Kalshi's public API, which the Claude sandbox cannot
reach and a GitHub Actions runner can — the same reason `mmsell_taxonomy_audit` is an ops
script.

```json
{"type": "script", "name": "series_rules_audit", "args": ["--top", "10"]}
{"type": "script", "name": "series_rules_audit", "args": ["--series", "KXNFLSPREAD"]}
{"type": "script", "name": "series_rules_audit", "args": ["--top", "40", "--emit-patch"]}
```

## Ordering the work

`scripts/series_registry_review.py --section backlog` ranks the debt by real-money exposure.

**That ranking was wrong on its first production run and has been fixed.** It asked which
*strategies* had ever placed a live order, then flagged a series if any *paper* trade in it came
from one of those books. Run against production it marked **137 of 138 series LIVE**, because
over all time 23–37 books touch a typical series and nearly every one carries some live lineage.
It was answering "did a live-lineage book paper-trade this", which is not the question — a
book's live arm and its paper arm trade different universes.

Exposure is now read directly off `live_orders` by market, over a `--live-days` window that is
deliberately separate from the settled-history window: **history wants everything we know about
a contract, exposure wants what is at risk now.**

## Status

- Tooling built, tested, and allowlisted. **Not yet run against production** — it must be merged
  first, because the ops runner executes only the default branch.
- First run: `--section backlog` on the registry review, then `series_rules_audit` in exposure
  order, contradictions read first.

## First full run — 2026-09-06

138 series audited: **118 CONFIRMS, 4 CONTRADICTS, 16 INSUFFICIENT.** Header did not read
`NOT A RESULT`; 8 markets were fetched per series, so Kalshi was genuinely reachable.

**No reviews were recorded from it**, for two reasons found in the output itself:

1. **Kalshi's `settlement_source` field fired zero times across all 138 series.** Every verdict
   rests on the rules-text regex alone, so the declared rule's cross-check — *at least one
   strong signal and no strong signal pointing elsewhere* — is vacuous. Our own database agrees:
   0 of 77 stored markets carry a settlement source. **The audit runs on one signal, not two.**
2. **`docs = 8` for every series**: dedup never collapsed anything, because rules text embeds
   per-market specifics (players, strikes, dates). "Counted over distinct documents" is really
   "counted over 8 near-identical markets" — the `tax-6` accounting error in milder form, in a
   script whose docstring claims to avoid it.

Confirming 118 series off a single regex would be exactly the *launder a machine's guess into a
human's signature* failure the design exists to prevent. The backlog stays at 138.

### The four contradictions, triaged

| series | recorded | implied | verdict |
|---|---|---|---|
| `KXUECLTOTAL` | `scheduled` | `in_play` | **real — and larger than one series** (below) |
| `KXWCMENTION` | `discrete` | `in_play` | ambiguous; matched "at any point during". Needs a human |
| `KXYTVIEWSHIGH` | `discrete` | `in_play` | **false positive** — pattern fix below |
| `KXYTVIEWSW` | `discrete` | `in_play` | **false positive** — same pattern |

### `KXUECLTOTAL` was a prefix collision, not a wrong entry

`KXUECLTOTAL` has no `SERIES_TYPES` entry. It was matching **`KXUE`** — the *unemployment*
econ-release prefix — by longest-prefix fallback. Six traded series were affected, all live
European football classified as scheduled economic releases:

| series | trades | was | now |
|---|---|---|---|
| `KXUECLTOTAL` | 133 | `econ_release`/`scheduled` | `total`/`in_play` |
| `KXUECLGAME` | 51 | " | `h2h`/`in_play` |
| `KXUELTOTAL` | 39 | " | `total`/`in_play` |
| `KXUELGAME` | 32 | " | `h2h`/`in_play` |
| `KXUEFASCSPREAD` | 14 | " | `spread`/`in_play` |
| `KXUECL1HTOTAL` | 1 | " | `total`/`in_play` |

**270 settled paper trades.** `KXUEFASC*` is the same collision, worked around once already for
the Super Cup — which is why the fix is explicit longer prefixes rather than narrowing `KXUE`,
which is a real traded series (13 trades) in its own right.

Only `KXUECLTOTAL` surfaced in the audit because **the worklist is graduated-only**. The other
five classify as `econ_release` — wrongly, but not *unclassified* — so they sit at `in_review`
and were never audited. A misclassification below the graduated tier is invisible to this tool.

### The pattern fix

`records? \d+\+` was written for player props ("records 3+ goals"). It matched
`"record 50000000+ views"` on the YouTube view-count series. It now requires the stat **noun**
(goals, points, strikeouts, yards, saves, …), which is the thing that actually implies a live
contest. Same failure as the bare clock time the table already documents.

### Still open

- `KXWCMENTION` — a human call, not a tool call.
- The single-signal weakness. The honest reframing: this is a **triage that ranks series for
  human reading**, not a verdict machine, until a second independent signal exists.

---

## Batch 1 signed — 2026-09-08, by `50cal`

> **These six signatures record the SETTLEMENT-RULES check only.** They are not a trading
> approval and they gate nothing. The full four-part review of this batch — category, rules,
> contracts-per-outcome and profitability, with its verdicts — is
> `docs/MMSELL_SERIES_APPROVAL_REVIEW.md`, and it rejects or flags four of the ten series
> reviewed here.


The first operator sign-off under the workflow `docs/MMSELL_SERIES_SCORECARD_HANDOFF.md` §"the
operator signs". Worklist: `series_registry_review --section backlog --top 10`, ranked by live
exposure. Evidence: `series_rules_audit --only <batch> --evidence`. Audit run: `CONFIRMS=6
CONTRADICTS=0 INSUFFICIENT=4`. **Zero contradictions — nothing in the batch is misclassified.**

Signed (6):

| series | audit verdict | recorded mode | basis for signing |
|---|---|---|---|
| `KXMLBSPREAD` | CONFIRMS | `in_play` | rules text agrees |
| `KXNFLTOTAL` | CONFIRMS | `in_play` | rules text agrees |
| `KXITFMATCH` | CONFIRMS | `in_play` | rules text agrees |
| `KXINXU` | CONFIRMS | `scheduled` | rules text agrees |
| **`KXRAIN`** | INSUFFICIENT | `scheduled` | **operator overrule — read below** |
| **`KXTRUMPSAY`** | INSUFFICIENT | `discrete` | **operator overrule — read below** |

### The two overrules, and why INSUFFICIENT was not a finding

`INSUFFICIENT` here means the regex found no settlement-mode keyword — **not** that the rules are
ambiguous. Kalshi's `settlement_source` field is empty across all 138 series, so the audit reads
prose and its vocabulary is narrower than Kalshi's. Both series were read directly.

**`KXRAIN` → `scheduled` holds.** *"If the total precipitation at CLITTN in Trenton in Sep 7, 2026
is strictly greater than 0 inches… the official and final value used to determine this market as
reported by the Weather Company… 'Trace' amounts (T) and missing daily precipitation values are
counted as 0 inches."* A named source, a named station, a fixed calendar day, a published data
location, and an explicit tie-break. No in-play component and no discretion. It settles on a
full-day total, so it cannot resolve before the day ends — consistent with `scheduled`, and worth
knowing because a position carries all day with no early resolution.

**`KXTRUMPSAY` → `discrete` holds, with a caveat that is not about the mode.** *"If <word>, or a
plural or possessive form… is stated by Donald Trump after August 31 2026 8:30am ET and before Sep
7 2026 12:00am ET, then the market resolves to Yes… For phrases with slashes like 'Doge/Dogecoin,'
either word satisfies the criterion. Doesn't count for payout: official acts like Executive Orders
or bills signed."* A binary did-it-happen event, not a level or a threshold, so `discrete` is
right. **But the behaviour is a one-way trigger over a week-long window:** it resolves Yes the
instant the word is said and can only resolve No by expiry. mmsell sells the cheap tail — it is
short "he won't say it" — so the position can be destroyed mid-week by one sentence with no chance
to react. That is an economics property, not a misclassification.

**Open question recorded, not resolved.** The "either word satisfies the criterion" clause makes
`Zohran / Mamdani` and `UFO / UAP` single markets with two ways to resolve Yes, structurally more
likely to settle Yes than a single-word market at the same price — against a seller. Whether the
book prices that is unmeasured.

### Deliberately not signed

- `KXWTI` (INSUFFICIENT, `scheduled`) and `KXALBUMEQUIV` (INSUFFICIENT, `discrete`) — both losing
  cells, both thin, both unread. `KXALBUMEQUIV` additionally carries the unresolved inconsistency
  recorded at `docs/mmsell_taxonomy_repair/REVIEW_20260824.md` row 116: it is `discrete` while the
  near-identical `KXPUREALBUMS` is `scheduled`. That is a real open question, not missing text.
- `KXNFLSPREAD` and `KXMLBHR` — **both CONFIRMS and both signable**; the operator held them back.
  Recorded because a reader will otherwise assume the batch signed every CONFIRMS.

### A signature is about settlement, never about P&L

`KXNFLSPREAD` is CONFIRMS and is the worst cell in the family (−$151.26, edge −10.3pp over 44
contests under the corrected key). Signing it would say only "we understand how it settles."
Whether it should keep trading is the scoring threshold's decision, and that gate does not exist
yet (Phase 3, a Platform Change). Nothing in this batch bars, promotes or changes any book's
universe.

---

## Batch 2 signed — 2026-09-10, by `50cal`

Four of ten. Full four-check review, verdicts and the reasoning:
`docs/MMSELL_SERIES_APPROVAL_REVIEW.md`. Audit run: `CONFIRMS=4 CONTRADICTS=1 INSUFFICIENT=5`.

| series | audit verdict | recorded mode | concentration | signed |
|---|---|---|---|---|
| `KXWTAMATCH` | CONFIRMS | `in_play` | 1.28 avg, 2 max — clean | ✅ |
| `KXWNBASPREAD` | CONFIRMS | `in_play` | 5.70 avg, 10 max — **approved at cap** | ✅ |
| `KXNASDAQ100U` | CONFIRMS | `scheduled` | 7.77 avg, 23 max — **approved at cap** | ✅ |
| `KXNATGASD` | CONFIRMS | `scheduled` | 10.22 avg, 30 max — **approved at cap** | ✅ |

**The three cap-approved rows are approvals of the SERIES, not of its concentration.** Nothing in
the manifest records a cap, and nothing enforces one: `KXNATGASD` still averages 10.22 contracts
per gas print and peaked at 30, the largest single-outcome exposure measured anywhere. The
approval is conditional on the contest cap actually running, which today it does not — `Gmmsell2`
is registered to no arm. Do not read these four signatures as "the concentration is handled".

`KXNATGASD` also carries **no losing trade yet** across 350 trades / 9 contests, so its edge is
undefined rather than excellent. Signed on category, rules and structure; its profitability is
unproven, not proven.

### Not signed, and why

- `KXYTVIEWSW` — **blocked on a taxonomy fix, not rejected.** CONTRADICTS: recorded `discrete`,
  the rules say a threshold live *at any point* across a week. It becomes signable once
  `SERIES_TYPES` records `in_play`.
- `KXTRUTHSOCIAL` — same block plus its own rejection: recorded `mention`/`discrete` against a
  threshold on a `scheduled` weekly post count, and −$20.57 at edge −10.6 on 5 contests.
- `KXBTC`, `KXBTCD` — profitable-to-flat but the deepest ladders in the batch (25 and 19 markets
  on one print). Cap first.
- `KXETHD`, `KXAAAGASD` — losing, and their settlement text is unread.

Both taxonomy defects are filed as one Experiment OS issue against `MARKET_TAXONOMY`, owned by
**Platform Change Review**. A review batch may not correct `SERIES_TYPES` as a side effect.

---

## Batch 3 signed — 2026-09-10, by `50cal`

Five of ten. Backlog ranks 21–30 by live exposure. Audit: **`CONFIRMS=10 CONTRADICTS=0
INSUFFICIENT=0`** — the first clean sweep, and unsurprising: batch 3 is entirely sports `in_play`,
the easiest class for the rules check to read.

| series | contracts / outcome | P&L | edge | contests | own% | signed |
|---|---:|---:|---:|---:|---:|---|
| `KXWNBAGAME` | 1.21 avg, 2 max | +$19.91 | +8.0 | 94 | 76% | ✅ |
| `KXMLBGAME` | 1.29 avg, 2 max | +$18.79 | **+1.3** | 552 | 95% | ✅ |
| `KXMLSTOTAL` | 2.30 avg, 6 max | +$19.83 | +5.6 | 57 | 66% | ✅ |
| `KXATPEXACTMATCH` | 2.66 avg, 6 max | +$15.49 | +5.1 | 38 | 56% | ✅ |
| `KXLEAGUESCUPTOTAL` | 2.59 avg, 6 max | +$15.88 | +4.7 | 34 | 53% | ✅ |

These five are the first cohort signed with **low concentration as a positive finding** rather
than a caveat: none exceeds 2.66 contracts per outcome, against 8–10 for the batch-2 ladders.

`KXMLBGAME`'s edge is only **+1.3pp**, but on 552 contests at 95% own-weight that is a
well-measured small number rather than noise — the opposite situation from `KXNATGASD`, signed in
batch 2 on 9 contests with an undefined edge.

### Dropped: `KXMLBTOTAL`

Correctly classified, rules CONFIRMS, and **5,930 trades — the largest sample in any batch — to
reach an edge of +0.3pp.** At 94% own-weight that is a well-measured *nothing*, carrying 4.66
contracts per game and −$486.50 of gross losses in multi-contract games. Activity without edge.

Also rejected: `KXLEAGUESCUPSPREAD` (−$15.18, edge −5.3), `KXCS2GAME` (−$15.91, edge −7.3),
`KXMLBTB` (−$16.22, edge −13.8 but only 10 contests — suggestive, not established),
`KXCLUBFGAME` (−$17.38, edge −5.8).

### The first real evidence that concentration tracks losses

Batches 1 and 2 were nearly all multi-contract, so "the losses came from multi-contract outcomes"
was tautological. **Batch 3 has a genuine mix**, and every mixed series over-represents:

| series | % of contests multi | % of losses from multi |
|---|---:|---:|
| `KXWNBAGAME` | 21% | 49% |
| `KXCS2GAME` | 26% | 51% |
| `KXMLBGAME` | **29%** | **68%** |
| `KXCLUBFGAME` | 58% | 74% |

`KXMLBGAME` is the best-powered case: 2.3× over-representation across 552 contests.

**It is association, not proof.** The multi-contract games are plausibly also the
more-heavily-traded ones, and nothing here controls for that. But it is the first version of this
observation that is not true by construction, and it strengthens the cap case beyond what batches
1 and 2 could support.

---

## Batch 4 signed — 2026-09-10, by `50cal`

Five of ten. Backlog ranks 31–40. Audit: `CONFIRMS=8 CONTRADICTS=0 INSUFFICIENT=2`.

| series | audit | contracts / outcome | P&L | edge | contests | own% | signed |
|---|---|---:|---:|---:|---:|---:|---|
| `KXNPBGAME` | CONFIRMS | 1.29 avg, 2 max | +$14.84 | +11.1 | 52 | 63% | ✅ |
| `KXFEDMENTION` | **INSUFFICIENT** | **1.00 — none** | +$13.11 | +10.0 | 27 | 47% | ✅ **overrule** |
| `KXUCLTOTAL` | CONFIRMS | 2.66 avg, 4 max | +$12.26 | +5.2 | 29 | 49% | ✅ |
| `KXLIGAMXTOTAL` | CONFIRMS | 2.70 avg, 5 max | +$11.68 | +7.1 | 27 | 47% | ✅ |
| `KXUFCMOV` | CONFIRMS | 3.48 avg, 7 max | +$14.50 | +3.3 | 42 | 58% | ✅ at cap |

### The `KXFEDMENTION` overrule

*"If the Chair of the Federal Reserve says Volatility at his Jul 2026 post-FOMC meeting
introductory remarks and Q+A, then the market resolves to Yes."* A did-it-happen event bounded to
one press conference — `discrete` is right. INSUFFICIENT means the regex found no settlement-mode
keyword, which is the same situation as `KXTRUMPSAY` in batch 1 and the same resolution.

It is also the only series in the batch with **no concentration at all**: 1.00 contracts per
outcome under the corrected key, because it is a `SUBJECT_SPLIT_SERIES` — each Fed word is its own
market and its own outcome.

> **A number in the batch-4 run was wrong and is corrected here.** The concentration report gave
> `KXFEDMENTION` a cross-series figure of **15.00**; the honest value is **`date?`**. Its event
> token is the bare month `26JUL`, and the detector required digits after the month, so an
> unreadable column printed a number instead. Fixed in the same PR. 15.00 would have read as the
> most cross-correlated series in any batch; nothing shares its occasion.

### Held, not rejected: `KXBRENTW`

+$13.07 and zero losing trades — and **4 contests**. No edge figure exists; the raw "+100" is the
break-even denominator collapsing, the same artifact as `KXNATGASD` in batch 2. At 12% own-weight
it is almost entirely prior, and it carries 5.00 contracts per print. There is no evidence here
yet in either direction, which is why it is held rather than approved or rejected.

### Not ruled on: `KXMLBKS`

+$13.06 at edge **+1.2** on 146 contests, 83% own-weight — real but tiny, with 9 contracts on one
game at the top end. Put to the operator as a genuine coin-flip; they did not rule, so the row
stays unsigned.

### Rejected

- `KXATPMATCH` — **the best-measured negative found so far.** 367 contests at 92% own-weight
  landing at −1.6. Low concentration (1.27/outcome) does not save it; this is an established small
  negative, not noise.
- `KXWTACHALLENGERMATCH` — −$10.96, edge −5.4, 87 contests.
- `KXRT` — INSUFFICIENT but readable: *"a Tomatometer score of above 62 on Sep 7, 2026 at 10:00 AM
  ET… determined the Monday after wide release"*, a named source at a named instant, so
  `scheduled` is correct. Rejected on the numbers: −$11.04, 17 contests, a 13-strike ladder on one
  score.

### Running total

**20 of 138 rows reviewed.** The signed set is now dominated by low-concentration sports and
mention markets; every deep ladder reviewed so far is either rejected, held, or approved with an
explicit cap caveat that nothing yet enforces.

---

## Batch 5 signed — 2026-09-10, by `50cal`

Four of ten. Backlog ranks 41–50. Audit: `CONFIRMS=9 CONTRADICTS=1 INSUFFICIENT=0`.

| series | audit | contracts / outcome | P&L | edge | contests | own% | signed |
|---|---|---:|---:|---:|---:|---:|---|
| `KXARGPREMDIVTOTAL` | CONFIRMS | 1.70 avg, 4 max | +$10.71 | +5.6 | 33 | 52% | ✅ |
| `KXWTASETWINNER` | CONFIRMS | 1.26 avg, 3 max | +$7.52 | +4.1 | 66 | 69% | ✅ |
| `KXMLSGAME` | CONFIRMS | 2.00 avg, 3 max | +$10.93 | +3.8 | 71 | 70% | ✅ |
| `KXUCLGAME` | CONFIRMS | 2.07 avg, 3 max | +$8.11 | +7.4 | 27 | 47% | ✅ |

All four are low-concentration (1.26–2.07 contracts per outcome) and CONFIRMS on rules. **24 of
138 rows now reviewed.**

### The YouTube defect is a family, not two rows — added as evidence to XOS-000030

`KXYTVIEWSHIGH` returned CONTRADICTS on **exactly** the pattern `KXYTVIEWSW` did: recorded
`event_stat`/`discrete` against *"above 8.25M Global daily views on YouTube **at any point during**
September 2026… resolves Yes if **any** daily value is above"*. A threshold live across a whole
month is `in_play`.

Three rows are now known wrong in or beside this family (`KXYTVIEWSW`, `KXYTVIEWSHIGH`, and
`KXTRUTHSOCIAL` on an adjacent error), and the two YouTube ones share an identical rules template
— so the misclassification is **systematic, not incidental**. Recorded on XOS-000030 as
`OPS_RESULT` evidence (`tax-ytviews-family-20260910`, SUCCEEDED) with the recommendation that
whoever fixes it **enumerate every `KXYTVIEWS*` series** rather than correcting the two rows the
issue was opened on. Transport cleared.

### Held, too thin to judge

- `KXCOPPERD` — **7 contests**, zero losses, so no edge figure exists; the raw "+100" is the same
  collapsed denominator as `KXNATGASD` and `KXBRENTW`. 19% own-weight.
- `KXLIGAMXSCORE` — **13 contests** carrying a 15-strike ladder. Positive, but 30% own-weight.

### Marginal, not ruled on

`KXWNBATOTAL` (+1.2 on 63 contests, 9 per outcome) and `KXMLBHIT` (+1.5 on 42 contests, **19 on
one game**). Same shape as `KXMLBKS` from batch 4 — a real but tiny edge attached to a deep
ladder. All three remain unsigned.

### Rejected

- `KXTESTMATCH` — −$10.92, edge −9.5 on 8 contests.
- `KXYTVIEWSHIGH` — −$7.24, edge −11.9 on 4 contests, **and** the taxonomy defect above.

### The concentration association, third batch running

Every mixed-contest series over-represents losses in multi-contract outcomes: `KXWTASETWINNER`
23% of contests → 62% of losses; `KXARGPREMDIVTOTAL` 48% → 100%; `KXMLSGAME` 72% → 92%.
Consistent with batches 3 and 4, and still association rather than proof.

---

## Batch 6 signed — 2026-10-05, by `50cal`

Three of ten. Audit: `CONFIRMS=9 CONTRADICTS=0 INSUFFICIENT=1`. **27 of 138 rows now reviewed.**

**How the worklist was picked.** The backlog ranks by *current* live exposure, so a literal
"ranks 51–60" no longer points at the rows it did on 2026-09-10. Batch 6 is the ten
highest-ranked backlog rows (`series_registry_review --section backlog`, 2026-10-05) that no
earlier batch ruled on; they sit at today's ranks 9–25, the gaps being batch 1–5 rows still
unsigned. Measured all-time, mmsell paper, twins excluded, corrected (subject-split) contest key
(ops `cc-b6-rules`, `cc-b6-conc`, `cc-b6-pnl`).

| series | audit | contracts / outcome | P&L | edge | contests | own% | decision |
|---|---|---:|---:|---:|---:|---:|---|
| `KXATPSETWINNER` | CONFIRMS | 1.49 avg, 4 max | +$12.98 | +6.7 | 78 | 67% | ✅ signed |
| `KXLALIGATOTAL` | CONFIRMS | 2.62 avg, 6 max | +$12.16 | +3.7 | 52 | 58% | ✅ signed |
| `KXT20MATCH` | CONFIRMS | 1.23 avg, 2 max | +$11.71 | +4.4 | 102 | 73% | ✅ signed |
| `KXMLBTEAMTOTAL` | CONFIRMS | 3.44 avg, 10 max | +$22.06 | **+1.1** | 197 | 84% | not ruled |
| `KXBRENTD` | CONFIRMS | 4.08 avg, 7 max | +$14.93 | +5.8 | 12 | 24% | held |
| `KXLALIGASCORE` | CONFIRMS | 6.53 avg, 16 max | +$12.92 | +3.8 | 17 | 31% | held |
| `KXINX` | CONFIRMS | 5.62 avg, 17 max | +$10.44 | +4.9 | 8 | 17% | held |
| `KXAAAGASW` | **INSUFFICIENT** | 5.83 avg, 14 max | +$10.30 | +3.7 | 12 | 24% | held |
| `KXMLBHRR` | CONFIRMS | 5.20 avg, **32 max** | −$10.98 | −2.7 | 25 | 40% | rejected |
| `KXLOLGAME` | CONFIRMS | 1.28 avg, 2 max | −$10.36 | −2.7 | 134 | 78% | rejected |

The three signed rows are the same shape as rows already signed — `KXATPSETWINNER` is the ATP
sibling of `KXWTASETWINNER`, `KXLALIGATOTAL` of `KXLIGAMXTOTAL` / `KXMLSTOTAL` — and all three
are low-concentration. `KXT20MATCH` shares its game with at most one other traded series
(cross-series 1.00, max 1). It is not the format batch 5 rejected: `KXTESTMATCH` is five-day Test cricket with draws on
8 contests; a T20 match is one day and decisive.

### Not ruled: `KXMLBTEAMTOTAL`

The largest P&L in the batch, and the thinnest edge: **+1.1pp on 197 contests at 84% own-weight**
is a well-measured small number, carrying 3.44 contracts per game and −$112.77 of gross loss in
multi-contract games against $0.00 in single ones. Same shape as `KXMLBKS`, `KXWNBATOTAL` and
`KXMLBHIT`; the operator left it unsigned with them. The MLB regular season has ended, so nothing
new will accrue until spring.

### Held, too thin to judge

`KXBRENTD` (12 contests), `KXLALIGASCORE` (17), `KXINX` (8) and `KXAAAGASW` (12) — all positive,
all under a third own-weight, all ladders of 4–17 contracts per outcome. Same treatment as
`KXBRENTW` (batch 4) and `KXCOPPERD` / `KXLIGAMXSCORE` (batch 5).

- **`KXAAAGASW` — the INSUFFICIENT is overruled on the rules, not the numbers.** *"If average
  regular gas prices for United States are strictly greater than $4.4920 on Sep 28, 2026 according
  to AAA"* — a named source at a named date, so `scheduled` is right; the regex found no keyword,
  the same situation as `KXFEDMENTION`. It is held on its 12 contests, and because its daily
  sibling `KXAAAGASD` (rejected, batch 2) is negative on the same underlying.
- **`KXINX` is a bucket ladder, not a strike ladder.** Its markets are mutually exclusive ranges
  (*"between 7950 and 7974.9999"*) with two open tails, so at most one leg of a print can resolve
  Yes. For a book selling the cheap tail that is much less concentrated than 5.62/outcome reads —
  but on 8 contests it is not evidence either way.

### Rejected

- `KXMLBHRR` — −$10.98, edge −2.7, and **32 contracts on one game**: the deepest single outcome
  in any batch, past `KXNATGASD`'s 30. 97% of its gross loss is in multi-contract games.
- `KXLOLGAME` — −$10.36, edge −2.7 on 134 contests at 78% own-weight. A well-measured small
  negative despite low concentration, the `KXATPMATCH` pattern; with `KXCS2GAME` (batch 3) that
  is both esports series reviewed, both negative.

### The concentration association, fourth batch running

`KXT20MATCH` 23% of contests multi → 51% of gross loss; `KXATPSETWINNER` 35% → 43%;
`KXLOLGAME` 28% → 36%. Every mixed series over-represents again, more weakly than batch 3, and
still association rather than proof.

### What this batch does NOT do

- **It does not widen the running tapes.** `Rmmsell1` / `Rmmsell2` trade the 24-series universe
  they were armed with on 2026-09-11; `scripts/reviewed_tape_spec.py --check` now reports the
  27-vs-24 gap, which is the designed prompt, not a defect. Widening is an operator act — new
  `MMSELL_VARIANTS` value plus a recorded epoch on **both** arms (`docs/MMSELL_REVIEWED_TAPE.md`,
  "Widening the universe").
- **It adds little volume.** Over the last 30 days `mmsell10` took 14 `KXLALIGATOTAL`, 10
  `KXT20MATCH` and 5 `KXATPSETWINNER` trades, about one a day between them. The large untapped
  pools — `KXNCAAF*` and the NFL player props — are not in the manifest at all, so they never reach
  this backlog; admitting them is a classification step before any review can start.
- **It gates nothing**, exactly as for batches 1–5: every row stays `graduated`, signed or not.

---

## Batch 7 signed — 2026-10-05, by `50cal`

Five of ten. Audit: **`CONFIRMS=10 CONTRADICTS=0 INSUFFICIENT=0`**. **32 of 138 rows now
reviewed.** Worklist: the next ten backlog rows (same 2026-10-05 ranking as batch 6) that no
earlier batch ruled on. Measured as batch 6 (ops `cc-b7-rules`, `cc-b7-conc`, `cc-b6-pnl`).

| series | contracts / outcome | P&L | edge | contests | own% | decision |
|---|---:|---:|---:|---:|---:|---|
| `KXMLSSPREAD` | 1.71 avg, 4 max | +$8.99 | +7.2 | 28 | 42% | ✅ signed |
| `KXBRASILEIROGAME` | 1.81 avg, 3 max | +$7.68 | +5.9 | 37 | 49% | ✅ signed |
| `KXECULPTOTAL` | 1.54 avg, 3 max | +$7.44 | +5.8 | 24 | 39% | ✅ signed |
| `KXLIGAMXSPREAD` | 2.14 avg, 4 max | +$7.99 | +5.1 | 22 | 37% | ✅ signed |
| `KXCONMEBOLSUDTOTAL` | 1.75 avg, 3 max | +$6.75 | +6.5 | 20 | 34% | ✅ signed |
| `KXLPGATOUR` | 6.50 avg, 12 max | +$9.26 | +10.8 | 5 | 12% | held |
| `KXGOLDD` | **9.08 avg, 21 max** | +$8.04 | +1.6 | 12 | 24% | held |
| `KXITFWMATCH` | 1.18 avg, 2 max | +$9.09 | **+0.4** | 857 | 96% | rejected |
| `KXATPCHALLENGERMATCH` | 1.20 avg, 2 max | +$6.31 | **+0.2** | 1,129 | 97% | rejected |
| `KXLIGAMXGAME` | 2.43 avg, 3 max | −$6.36 | −2.2 | 61 | 62% | rejected |

**All five signatures are soccer totals, spreads and match winners**, and all five are
low-concentration (≤ 2.14 contracts per outcome). They are also thin: 20–37 contests at 34–49%
own-weight, the same range as `KXUCLGAME` / `KXLIGAMXTOTAL` (batches 4–5). `KXCONMEBOLSUDTOTAL`
is the thinnest signature in this batch.

`KXLIGAMXSPREAD` is signed while its sibling `KXLIGAMXGAME` is rejected. They are the same games
read through different contracts: the spread sells the tail of a margin, the game sells the tail of
an outright result, and only the first has been profitable on its own record.

### Rejected: two tennis tours with no edge

`KXITFWMATCH` and `KXATPCHALLENGERMATCH` are the `KXMLBTOTAL` pattern again: the two
best-measured rows in the batch (96–97% own-weight) both measure *nothing*. Together they are
5,200+ trades for +0.2 to +0.4pp. Low concentration does not rescue them. They are also the
batch's volume (~6.5 trades a day between them over the last 30 days). Rejecting them costs the
tape activity, not profit.

### Held

`KXLPGATOUR` has 5 tournaments at 12% own-weight, and the "+10.8" is mostly prior. `KXGOLDD` is a
21-strike ladder on 12 prints with an edge of +1.6: thin, deep and small, the `KXNATGASD` shape
without the excuse of zero losses.

### The concentration association, mixed this time

The two large samples still over-represent: `KXATPCHALLENGERMATCH` 20% of contests multi → 51%
of gross loss, `KXITFWMATCH` 18% → 46%. The small soccer series do not: `KXECULPTOTAL` 38% →
1%, `KXMLSSPREAD` 50% → 44%. Their losses are too few (≤ $1.81 gross) to say anything. This is
the first batch where the pattern is not uniform. It still holds wherever the sample is large.

### Same limits as batch 6

The running `Rmmsell` tapes are unchanged (24 armed, 32 signed; `reviewed_tape_spec.py --check`
reports it). Volume added is small: the five signed series took 18 `mmsell10` trades in 30 days
between them. Nothing here gates anything.

---

## Batch 8 signed — 2026-10-05, by `50cal`

Four of ten. Audit: `CONFIRMS=9 CONTRADICTS=0 INSUFFICIENT=1`. **36 of 138 rows now reviewed.**
Worklist: the next ten unruled backlog rows (same ranking as batches 6–7). Measured as before
(ops `cc-b8-rules`, `cc-b8-conc`, `cc-b6-pnl`).

| series | contracts / outcome | P&L | edge | contests | own% | decision |
|---|---:|---:|---:|---:|---:|---|
| `KXUFCFIGHT` | **1.02 avg**, 2 max | +$4.88 | +3.9 | 83 | 69% | ✅ signed |
| `KXCPLMATCH` | 1.41 avg, 2 max | +$4.35 | +4.9 | 32 | 46% | ✅ signed |
| `KXDIMAYORGAME` | 1.77 avg, 3 max | +$4.16 | +3.8 | 29 | 43% | ✅ signed |
| `KXDOTA2GAME` | 1.21 avg, 2 max | +$3.89 | +8.1 | 33 | 46% | ✅ signed |
| `KXLALIGAGAME` | 2.28 avg, 3 max | +$5.56 | +2.6 | 60 | 61% | not ruled |
| `KXUFCVICROUND` | 5.00 avg, 8 max | +$4.73 | +3.6 | 9 | 19% | held |
| `KXWTIW` | **12.38 avg, 19 max** | +$5.30 | **+0.5** | 8 | 17% | held |
| `KXSAUDIPLGAME` | 1.70 avg, 3 max | −$5.58 | −6.7 | 20 | 34% | rejected |
| `KXEFLCHAMPIONSHIPGAME` | 1.41 avg, 3 max | −$4.76 | −3.2 | 34 | 47% | rejected |
| `KXNFLPASSYDS` | 3.29 avg, 7 max | −$4.75 | −1.7 | 34 | 47% | rejected |

**`KXUFCFIGHT` is the cleanest concentration row in any batch.** It averages 1.02 contracts per
fight, and 98% of its fights hold a single market. This makes it the first series whose losses
are almost entirely single-contract ($14.66 vs $0.41). Whatever its losses are, a contest cap
would not have touched them.

**`KXDOTA2GAME` is signed against the esports record so far.** `KXCS2GAME` (batch 3) and
`KXLOLGAME` (batch 6) were both negative and rejected. The operator signed Dota 2 on its own
+8.1 over 33 games. It also prices differently from the rest of the signed set: average entry
22.4¢ against a 19.6% break-even. Most of its trades sit outside the 5–10¢ band the `Rmmsell`
tapes use, so a widened tape would carry little of it.

### Not ruled: `KXLALIGAGAME`

+$5.56 over 60 games, but only +2.6pp, with 2.28 contracts per game. That is the small-edge
moderate-ladder shape the operator has consistently left unsigned (`KXMLBKS`, `KXMLBTEAMTOTAL`).
Its sibling `KXLALIGATOTAL` was signed in batch 6.

### Held

- `KXUFCVICROUND`: 9 events at 19% own-weight, with 5 contracts per event.
- **`KXWTIW`: the INSUFFICIENT is overruled on the rules, not the numbers.** *"If the daily
  settlement price for WTI crude oil (November 2026 contract) on October 02, 2026 is below 80.00
  USD/Bbl"* names a source and an instant, so `scheduled` is right. It is held because it is a
  19-strike ladder on 8 prints at +0.5pp: 1,009 trades that say almost nothing. Its daily sibling
  `KXWTI` was rejected in batch 1 at −9.2.

### Rejected

- `KXSAUDIPLGAME`: −6.7 on 20 games.
- `KXEFLCHAMPIONSHIPGAME`: −3.2 on 34 games, despite low concentration.
- **`KXNFLPASSYDS`: −1.7, and the most cross-shared series reviewed.** An NFL game carries on
  average 12.8 other traded series beside it (max 30), so one game moves dozens of positions
  across the family. Every dollar of its loss is in multi-contract games.

### Concentration association

`KXEFLCHAMPIONSHIPGAME` 29% of games multi → 51% of loss, `KXDOTA2GAME` 21% → 55%,
`KXSAUDIPLGAME` 55% → 61%, `KXDIMAYORGAME` 70% → 84%. It over-represents again on every mixed
row with real losses. `KXUFCFIGHT` is the opposite case, with nearly no multi-contract games at all.

### Same limits

The running `Rmmsell` tapes are unchanged (24 armed, 36 signed). Volume added is small: 5
`KXDIMAYORGAME` trades in 30 days, and the other three signed series took none in that window.
Nothing here gates anything.
