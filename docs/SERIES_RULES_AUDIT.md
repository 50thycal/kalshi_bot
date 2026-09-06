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
