# Proposed rule reviews — top 13 live-traded series (2026-10-12)

**Workstream:** WS-023 · **Status:** 5 approved by the owner on 2026-10-13 (#5 MLB total, #6 BTC, #7 MLB spread,
#8 rain, #11 WTI), recorded in `kalshi_bot/catalog/approved_reviews.json` and applied when the owner merges it;
the other 8 are proposed · **Author:** Claude Code (task-specific catalog session)

These 13 series cover 50% of the markets MMSELL has traded live (2,868 markets across 250 series; ops
`catalog-review-priority-20261012a`). Each draft quotes one recent traded market's official listing rules
and is checked against the series' earliest traded market. The machine-readable records are in
`kalshi_bot/catalog/review_drafts.json` (`*-series-draft-20261012`).

A draft is not an approval. It sets no score, qualifies nothing and changes no trading. An approved review must be
posted through the catalog's authenticated review API against the market's current catalog rules hash.

## What was and was not read

- **Read:**
  - The exact listing rules (primary and secondary text) for 26 markets: one recent and one early market per series.
  - Series settlement sources, fee schedule and event grouping.
  - Sources: ops `catalog-review-sources-20261012a` and `-b`, retrieved 2026-10-12 22:52–22:55 UTC.
- **Hashed but not read:** the contract terms PDF and the product-certification PDF for each series. Their text
  could not be extracted (encoded fonts). Anything the listings leave to "the rules" is therefore an **open question**
  below, not a guess.
- **Not verified:**
  - Which version of the terms governed older fills.
  - Whether outcomes are independent.
  - Any numeric threshold.

## The 13 series in plain words

| # | Series | Settles Yes when… | Edge cases from the listing | Open questions |
|---|---|---|---|---|
| 1 | KXMLBHR (465) | The named starter, with ≥1 plate appearance, hits the stated home runs. | Scratched, no plate appearance or bench entry → **fair price**, not 0/1. Pinch-hit at-bats don't count. | PDF: postponed/suspended games, stat corrections. |
| 2 | KXATPCHALLENGERMATCH (129) | The named player wins after a ball is played. | Never starts → **fair price**. Postponed → stays open ≤2 weeks. | Mid-match retirement not stated. How the "fair price" is computed. |
| 3 | KXITFWMATCH (126) | Same as #2. | Never starts → **$0.50 for all markets**. Retires mid-match → that player **No**. | Which source wins if ITF, Flashscore, ESPN and Fox disagree. |
| 4 | KXITFMATCH (122) | Same as #3. | Same as #3. | Same as #3. |
| 5 | KXMLBTOTAL (117) | Combined runs exceed the half-run line. | Closes early once the over hits. The rule text has a typo ("score more 10.5 runs"); the intent is clear. | PDF: postponed/suspended/shortened games. The earliest sample was the All-Star Game. |
| 6 | KXBTCD (104) | The average of 60 BRTI values in the minute before 5 PM ET is **strictly above** the strike. | "$88,000 or above" is displayed for strike 87,999.99. All strikes at one time share one number. | PDF: index outage and missing data. |
| 7 | KXMLBSPREAD (104) | The named team wins by more than the half-run line. | A loss is No. | PDF: postponed/suspended games. |
| 8 | KXRAIN (71) | The named station records **> 0.00 in**. | Trace = 0. **Missing data = 0, so a data gap settles No.** Only the named station counts. | Calendar day or local-standard-time climate day? |
| 9 | KXWCGOAL (47) | The player scores ≥1 goal, including extra time. | Never enters → **pre-game fair price**. | Penalty shootouts. The tournament is over: is approval still needed? |
| 10 | KXMLBKS (42) | The starting pitcher reaches the strikeout line. | Not starting or relief → **fair price**. **The participation test changed:** "at least one pitch" (August) → "at least one batter faced" (October). | Date of the change, before applying one template to older fills. |
| 11 | KXWTI (41) | The named **ICE** WTI contract's daily settlement (2 dp) is above the strike. | Front month rolls 2 business days before the last trading day. No settlement published → **latest available settlement**. | PDF: holidays. Observation instant versus the ICE settlement window. |
| 12 | KXWTACHALLENGERMATCH (40) | Same as #2. | Same as #2. | Same as #2. |
| 13 | KXNFLTOTAL (39) | Combined points exceed the half-point line. | Closes early once the over hits. **Maker fees apply** (`quadratic_with_maker_fees`); no other series here charges them. | Does overtime count? Postponement? Do the economics use the real maker fee? |

The number after each series is its count of live-traded markets (ops `catalog-review-priority-20261012a`).

## Findings that matter beyond the reviews

1. **Non-binary settlements are common.** Fair-price or $0.50 settlement exists in 7 of the 13 series: MLB home
   runs and strikeouts, all four tennis series, World Cup scorer. The strict binary ledger is built to block any
   non-0/1 settlement. Whether the 23 live markets currently blocked as `unsupported_or_conflicting_settlement` are
   these cases is not verified here.
2. **Tennis tours differ.** Challenger tours use a "fair price" when a match never starts; ITF uses $0.50 and makes a
   mid-match retiree No. One shared tennis template would be wrong.
3. **Rules changed while we traded.** The KXMLBKS participation wording changed during the trading period, and MLB
   prop strike metadata changed too. This is concrete evidence that current listing text cannot stand in for the
   terms at fill time.
4. **Correlated exposure is wide.** Every MLB series on one game shares that game; all BTC strikes at one time share
   one number; every WTI strike on a date shares one settlement. Each draft records a candidate exposure key; none is
   verified.

## Approval status (2026-10-13)

The owner approved #5, #6, #7, #8 and #11. Each approval names the recent and the early sample
market. It covers every market in that series whose rule wording matches theirs: strikes, dates
and names may differ (`DEC-035`). Merging the file is the sign-off; the catalog applies it
at start.

Of the other eight, seven open questions are rare contingencies; the owner is content to approve
on the main rule. NFL overtime (#13) changes the total in roughly one game in twenty, so it waits
for the contract PDF. PDFs can now be fetched as exact bytes (`--pdf-base64`) and read in full.

## Contract PDF answers (2026-10-13)

The contract-terms PDFs were fetched as exact bytes (ops `catalog-pdf-terms-20261013a`) and read
with a full PDF reader. Each matches the SHA-256 the draft recorded. These are the **current**
terms. Several were modified after our earliest fills (for example BTC on 29 Sep, rain on 1 Oct,
baseball props on 8 Sep), so they do not prove which terms governed older fills.

| Series | Open question | What the terms say |
|---|---|---|
| KXNFLTOTAL | Does overtime count? | **Yes.** "Overtime counts for all markets unless stated otherwise." |
| KXNFLTOTAL | Postponement, suspension | Suspended before 55 minutes and not resumed within 48 h → **last fair price**. At 55+ minutes, or declared final → settles on what happened. Postponed within 48 h → stays open. |
| KXMLBTOTAL, KXMLBSPREAD | Postponed or suspended games | Not resumed within 48 h → **last fair price** (non-binary). Shortened but declared official → the official result. Extra innings count. Post-game stat corrections are ignored. |
| KXMLBHR, KXMLBKS | Postponements, stat corrections | Not official and not resumed within 48 h → last fair price. Stats are final at game end. A player removed after appearing settles on stats if they had ≥1 plate appearance (batters) or faced ≥1 batter (pitchers). |
| KXMLBKS | Date of the participation change | The current terms (modified **8 Sep 2026**) say "faced at least one batter", matching the October listings. The change is therefore most likely around 8 Sep. Unproven. |
| KXBTCD | Index outage | "If no data is available … the market resolves to **No**." |
| KXWTI | Holidays and missing settlements | If no price is published and none is expected within a week → the **most recently available** price. |
| KXRAIN | What counts | **Total precipitation, including melted snow, sleet and hail.** Trace and missing daily values = 0. No reliable data by expiration → **last fair price**. |
| Tennis (4 series) | Retirement, sources | A player who withdraws or forfeits resolves **No**. Sources: the governing body, AP, ESPN, WSJ and Fox Sports, with no ranking among them. The listing's own no-start rules (fair price, or $0.50 on ITF) sit on top of this. |
| KXWCGOAL | Shootouts, own goals | Penalty-shootout goals and own goals **do not count**. Extra time counts. One clause ("cup finals … only count regulation goals for standard totals") conflicts and is noted, but the listing says extra time counts. |

What this means:
- Every open question now has an answer from the terms. NFL overtime counts.
- Fair-price settlement on postponement exists in **MLB total and spread too**, not only in props and tennis.
- Rain covers all precipitation, not just rain.

## How to approve

Approval is your decision, series by series. Reply with what you accept, reject or want changed, for example
"approve 5, 6, 7, 11; tennis: wait for PDF".

An accepted draft is recorded in `kalshi_bot/catalog/approved_reviews.json`, and the owner's merge applies it
(`DEC-035`). It binds only while the sample markets still carry the exact rules that were read. A series review still leaves each market's own
review and the historical-terms question open, so confidence stays unset until the remaining calibration steps are done.
