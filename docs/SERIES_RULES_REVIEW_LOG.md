# Series rules-review log — what was read, and what was decided

`rules_reviewed_at` in `kalshi_bot/registry/series_manifest.json` is a claim that **a person
read Kalshi's settlement language for that series**. The manifest records *that* the claim was
made; it cannot record *what was read* or *why the reviewer disagreed with the audit*. This file
is that record, one section per batch.

Why it has to exist separately: `scripts/series_rules_audit.py --evidence` is the only place the
settlement text appears, it is run through the ops channel, and `ops/results/` keeps only the
newest 80 files. Without this log, an overrule is a chat message that no longer exists, and the
next reviewer cannot tell a considered decision from a rubber stamp.

**Nothing here authorizes anything.** The manifest moves only by PR; this log is the reasoning
attached to that PR.

## Batch 1 — 2026-09-08 — reviewed by Calvin

Worklist: `series_registry_review --section backlog --top 10` (the ten live cells carrying the
most exposure). Evidence: `series_rules_audit --only <the ten> --evidence`, run against code
`2225d7a`. The audit's own verdict was **6 CONFIRMS / 4 INSUFFICIENT / 0 CONTRADICTS**.

**Signed (8).** Six where the evidence and the recorded taxonomy agree, plus two overrules.

| series | recorded | audit verdict | why it was signed |
|---|---|---|---|
| `KXNFLSPREAD` | `in_play` | CONFIRMS | "wins by more than N points in the … game" — resolves on play. |
| `KXMLBSPREAD` | `in_play` | CONFIRMS | Same shape, run line. |
| `KXMLBHR` | `in_play` | CONFIRMS | Per-batter home runs in a named game; the participation clause (scratched / no plate appearance → fair market price) is the settlement detail worth knowing. |
| `KXNFLTOTAL` | `in_play` | CONFIRMS | Combined points in a named game. |
| `KXITFMATCH` | `in_play` | CONFIRMS | Match winner "after a ball has been played"; no-play → $0.50, mid-match retirement resolves against the retiring player. |
| `KXINXU` | `scheduled` | CONFIRMS | End-of-day S&P 500 value on a stated date; expires at first release of the data. |
| `KXWTI` | `scheduled` | **INSUFFICIENT — overruled** | "The **daily settlement price** for WTI crude oil (October 2026 contract) on September 04, 2026" is a scheduled exchange print. The audit found no mode *keyword*; the mechanism is unambiguous in the text. The contract-roll clause (rolls 2 business days before the front month's last trading day) is the real hazard here, and it is a *universe* question, not a settlement-mode one. |
| `KXTRUMPSAY` | `discrete` | **INSUFFICIENT — overruled** | "If \<word\> … is stated by Donald Trump after Aug 31 8:30am ET and before Sep 7 12:00am ET" — an event that either occurs at some instant in a window or does not. That is what `discrete` means. |

**Held (2).** Both on settlement-mode grounds, both left `null` and unchanged.

- **`KXALBUMEQUIV`** — recorded `discrete`, and the text reads `scheduled`: settlement is "the
  value reported by Luminate's API **as of 10:00 AM ET on the Sunday immediately following**"
  the tracking week. A weekly threshold against a scheduled data release is not a discrete
  event. This is a suspected **recorded-mode error**, not missing evidence, which is a stronger
  finding than the audit's INSUFFICIENT — the audit has no CONTRADICTS here only because the
  text carries no mode keyword for its regex to catch. Live, and −$51.13 over 311 settled
  markets. Left flagged by operator decision; correcting the taxonomy row is a separate act.
- **`KXRAIN`** — recorded `scheduled`, and that is defensible: the official value is the daily
  precipitation total from the Weather Company's CLI report, published after the day ends. What
  makes it worth a second look is that the *outcome* is revealed continuously while the market
  trades — "strictly greater than 0 inches" is settled in fact the moment it rains, hours before
  the report exists. A mode field that says `scheduled` is right about settlement and misleading
  about intraday quoting risk. Live, 1,116 settled markets, +$68.65.

Two known limits of this batch, neither introduced here:

- **`docs = 8` for every series** is not eight distinct settlement regimes; rules text embeds
  per-market specifics, so it is really "eight near-identical markets" (see
  `docs/MMSELL_SERIES_SCORECARD_HANDOFF.md`). The reviewer sees the *shape*, not a census.
- **The worklist is graduated-only**, so 265 traded series with no manifest row are invisible to
  it. Batch 1 does not close that.

Remaining backlog after this batch: **130 of 138** rows unreviewed.
