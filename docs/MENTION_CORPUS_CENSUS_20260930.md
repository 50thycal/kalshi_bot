# Earnings-call mentions: bounded historical coverage census

SESSION: Research Lab / MODE: COUNTS-ONLY CENSUS / ENFORCEMENT: NEW_ONLY / AS OF: 2026-09-30

**Research verdict: HOLD; zero promotions.** Kalshi's public history and free
transcript archives are substantially more useful than the empty local tables
suggested. We recovered 5,718 finalized contracts across 415 raw event tickers,
and eight pre-decision Microsoft transcript versions. The remaining blocking
evidence is executable historical quotes with size, plus the full independent
issuer-call/source join. No predictive rule, target-label study or P&L ran.
This is a feasibility conclusion, not an Experiment OS gate result.

## Scope and frozen contract

Calvin authorized the bounded coverage census after the different-mechanism
[idea screen](IDEA_MODEL_MENTIONS_20260930_CHATGPT.md), including public Kalshi
history and other accessible archives. The screen's C0 and C1/P1/P2/P3 remain
unchanged. Its preregistration is commit
`436890e99c80ff62cf908a80c10e2f1e41f13173`, published before this acquisition.
The census-first portion of `kalshi-probe-builder` supplied the workflow; this
is not a promoted strategy probe or an ops allowlist change.

C0 requires 60 independent settled issuer-calls, 10 issuers, two calendar
quarters, 20 call dates and 300 listed word contracts. Each call needs eight
preceding complete transcripts with demonstrably available pre-decision
versions, eligible source calls ending at least seven days earlier, unambiguous
rules and valid quotes with observable size no older than 60 seconds. Decision
time stays exactly 60 minutes before the official scheduled call start.
Indicative one-lot candle research and depth-supported capacity are distinct;
no candle-only finding below is declared a C0 or profitability PASS.

The bounded session ends here because none of the inspected historical candle
schemas supplies resting size. Auditing hundreds of additional transcripts
would not remove that blocker. Missing data means HOLD, not evidence that C1
has negative expected value.

## Public market inventory

The 354 series/tier market requests completed from **12:44:59 to 12:54:20 UTC**
on September 30. Every query exhausted its cursor in one page; zero query
errors and zero truncations. This is a dated retrieval, not an atomic snapshot.
Machine-readable evidence and the offline audit are in
[research/mention_census_20260930](research/mention_census_20260930/README.md).

| Measured quantity | Count | Interpretation |
|---|---:|---|
| Exact `KXEARNINGSMENTION` series-prefix matches | 177 | Series IDs, not distinct issuers |
| Rows from historical route | 4,499 | All finalized in this retrieval |
| Rows from live route | 2,333 | 1,465 finalized; remainder not finalized |
| Tickers present in both routes | 246 | Deduplicate before counting |
| Unique contracts, all statuses | 6,586 | Outcome fields discarded |
| Unique finalized contracts | 5,718 | Raw instrument supply |
| Event tickers containing finalized contracts | 415 | Not yet deduplicated issuer-calls |
| Series containing finalized contracts | 165 | Not a 165-company claim |

Settlement timestamps span February 27, 2025 through September 29, 2026.
Settlement months are retained for audit; **they are not call dates** and are
not used to assert the quarter/date gate. The entire 5,718-contract universe
also contains count-threshold contracts such as “3+ times,” whereas C1 requires
single-occurrence contracts. Thus 5,718 is an inventory count, not the eligible
C1 denominator or an estimate of trading capacity.

The [official historical-data guide](https://docs.kalshi.com/getting_started/historical_data)
separates live and archived markets and warns that older markets may be absent
from nested event responses. The observed `market_settled_ts` cutoff was
August 1, 2026. Querying both market routes was necessary; four empty local
tables did not imply that public history was absent. The 246 duplicated tickers
also show why simply summing endpoints would overstate coverage.

Issuer aliases and duplicate call listings still require reconciliation.
Examples include ADBE/ADOBE and DE/DEER series; generic quarter event IDs coexist
with dated call IDs. No raw series or event count is silently treated as an
independent trial. The prefix search is bounded to this known family; it does
not establish exhaustive coverage of renamed contracts outside the prefix.

## Decision-time quote audit

Three fixtures were selected for source-grounded official scheduled starts,
then the first five contract tickers in lexical order were inspected within
each event. Selection did not use quotes, word recurrence or target outcomes.
The sample is diagnostic and **not a population coverage estimate**.

| Event | Official call start, UTC | Fixed decision, UTC | Listed / sampled contracts | Samples with aligned valid bid/ask candle closes | Samples with resting size |
|---|---|---|---:|---:|---:|
| MSFT July 29, 2026 | 21:30 | 20:30 | 15 / 5 | 3 | 0 |
| META July 29, 2026 | 20:30 | 19:30 | 18 / 5 | 3 | 0 |
| NVDA August 26, 2026 | 21:00 | 20:00 | 19 / 5 | 4 | 0 |
| Total | Three issuer-calls, two dates, Q3 only | — | 52 / 15 | 10 | 0 |

Each query covers only the ten minutes ending at decision time, at one-minute
resolution. “Aligned” means candle period end at or before the decision and
within 60 seconds, with non-null ordered bid/ask dollar prices. Five samples
had no such row. Even for the other ten, a period end is not a separate
exchange timestamp proving the last quote update's age. None supplies offered
quantity or a complete historical depth ladder. Trade volume and open interest
cannot fill that gap. No fills are inferred.

The historical endpoint returned `yes_bid.close` / `yes_ask.close` as decimal
dollar strings; the live endpoint returned `close_dollars`. Trade-price close,
midpoints and intraminute extrema were not substituted. The saved audit
includes raw pre-decision candles and schemas so these interpretations are
reviewable. See the official
[historical candle reference](https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks).

**A material timing trap:** MSFT's market `occurrence_datetime` was 00:00 UTC
July 30, while its official July 29 call began at 21:30 UTC. Subtracting one
hour from the market field would put the decision 90 minutes after call start.
META's July fixture even carried a December 31 placeholder. Official investor
relations scheduling is therefore necessary; ticker dates and market occurrence
fields cannot establish the decision time.

Call-time sources:
[Microsoft announcement](https://news.microsoft.com/source/2026/07/08/microsoft-announces-quarterly-earnings-release-date-68/),
[Meta investor events](https://investor.atmeta.com/investor-events/default.aspx),
[NVIDIA announcement](https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Sets-Conference-Call-for-Second-Quarter-Financial-Results/default.aspx).
NVIDIA's written CFO commentary was scheduled after this fixed decision, so it
cannot become an input to the pre-call rule merely because it is now archived.

## Transcript availability and historical versions

For the Microsoft July 29 target, all eight preceding quarterly official
earnings-page transcripts were retrieved from Internet Archive captures
preceding the fixed decision. Each body contains the matching fiscal title,
substantive transcript text and Q&A; the final archive URL and
`Memento-Datetime` were verified. Source hashes and exact URLs are retained.

| Prior fiscal quarter | Verified archived version, UTC |
|---|---|
| FY26 Q3 | July 25, 2026 00:40:38 |
| FY26 Q2 | July 15, 2026 17:06:45 |
| FY26 Q1 | July 15, 2026 17:06:45 |
| FY25 Q4 | June 23, 2026 16:18:09 |
| FY25 Q3 | July 15, 2026 17:06:45 |
| FY25 Q2 | July 15, 2026 17:06:45 |
| FY25 Q1 | May 14, 2026 09:35:22 |
| FY24 Q4 | June 15, 2026 05:11:59 |

These archive dates establish an available historical version, not its first
publication timestamp. The quarter sequence predates the target by more than
the seven-day embargo. Text presence and Q&A checks are useful feasibility
evidence, not certification of every utterance, speaker mapping or contract
word rule. No candidate words were counted. A production-quality source panel
would still validate transcript completeness and the contract's speaker scope.

Direct current Microsoft IR requests returned 403 in this runtime; the public
archive was accessible. Three initial archive timeouts succeeded on retry.
Some responses were gzip encoded and had to be decompressed before text checks.
No third-party full transcripts are republished in the repository.

Meta also exposes quarterly transcripts and separate follow-up-call transcripts.
That supports source feasibility, but eight pre-decision versions were not
audited for Meta or NVIDIA. Separate follow-up calls cannot be concatenated
without a matching settlement rule. **One issuer-target source stack is not
60 qualified calls across ten issuers.** Microsoft's July target remains
discovery-only under the original screen, because its outcomes had already been
seen there; this census does not restore it to confirmatory eligibility.

## Other data routes checked

| Route | What it adds | Why it does not clear this gate now |
|---|---|---|
| [Public Hugging Face order-book archive](https://huggingface.co/datasets/lerchen3/kalshi-orderbook-alpha) | Snapshot/delta format with book quantities | Inspected revision contains 164 data files, all July 19, 2026. One date cannot meet C0. Earnings-market inclusion was not established. |
| [DepthFeed](https://depthfeed.com/docs) | Historical books, authenticated API | Documentation emphasizes crypto and sports; earnings-call coverage and required dates unverified. Related branded sites are not independent archives. |
| [ProbSights](https://probsights.com/p/kalshi-historical-data-api) | Advertised Kalshi book/trade history, 1m/5m, Builder plan | Advertised 60-day history on September 30 lies within Q3. It alone cannot meet two quarters; no authenticated earnings sample was inspected. |
| [KalshiBackTest free data](https://kalshibacktest.com/free-kalshi-historical-data) | Recent crypto intraday historical samples | Wrong instrument universe. |
| [Strux transcript corpus](https://struxdata.github.io/dataset/) | Historical earnings text, prepared remarks and Q&A | Published 2017–2024 span does not supply the eight most recent calls for the 2026 fixtures. Stock-return labels were not used. |
| [EarningsCall](https://earningscall.biz/api-pricing) | Paid transcript history and speaker features | Product availability does not prove exact pre-decision transcript versions; buying text would not supply missing Kalshi depth. |

No paid account, subscription, vendor outreach or new collector was started.
Provider claims are a source screen, not independently verified dataset coverage.

## Gate accounting and decision

| Requirement | Evidence established | Remaining limitation |
|---|---|---|
| Raw settled instrument supply | 5,718 contracts / 415 event tickers | Full single-occurrence filter, duplicate-call and issuer reconciliation not performed |
| Official call time | Three selected targets | Other 412 raw settled event tickers not joined; duplicate mappings could change this denominator |
| Eight prior pre-decision versions | Microsoft fixture: eight retrieved | Full completeness/rule parsing pending; no equivalent eight-source audit for other issuers |
| Fresh executable quote and size | Ten of 15 samples have aligned candle closes | Zero of 15 have resting size; candle end alone does not prove quote-update freshness |
| Fully qualified independent calls | **Zero demonstrated** | This is not a claim that zero exist in all possible archives |
| C1 and P1/P2/P3 | **Not evaluated** | No word frequencies, predictions, target outcomes, returns or capacity extrapolation |

**Keep MENTION-corpus / C1 on HOLD.** Public API discovery partly unblocks the
prior source-access concern, but does not establish an edge or a dollar/month
forecast. The original $25/month contribution floor and $100/month north star
remain unvalidated. Raw word listings cannot be multiplied into monthly trades.

**Named reopening trigger:** a lawful historical export or provider sample
demonstrates earnings-contract bid/ask quantities and usable observation times
at the frozen decision for a candidate panel meeting 60 independent calls,
10 issuers, two quarters and 20 dates. Check exact ticker coverage and gaps
before purchasing anything. Then finish the transcript/rule join and freeze
the full eligible manifest before unblinding outcomes. If only candles remain
available, any indicative forecasting study needs its own explicit scope and
cannot be represented as a realistic-fill or size-supported profitability test.

No further NLP variants or broad archive acquisition are justified by this
census alone. No live book, shared metric, platform revision, ops runner,
experiment lifecycle or promotion tally changed. This closes the authorized
bounded census with a narrower, documented blocker.

### Subsequent export investigation

The authorized [depth-export follow-up](MENTION_DEPTH_EXPORT_20260930.md) found a
documented free historical snapshot endpoint with level quantities. Its exact
earnings coverage is unverified because an API key is required and none is
configured. Other public export attempts encountered access/server errors.
C0 remains HOLD; the follow-up preserves an exact 15-contract sample request
and a verification checklist without changing any gates.
