# MMSELL: can the live book earn meaningful dollars?

**Research Lab · scoped `kalshi-idea-model` · 2026-09-30.**

**Result: nine candidates screened; zero promoted. Recommend pausing new MMSELL
development and redeploying research effort.** Preserve one precisely defined
high-volume hypothesis for a possible later read of naturally accumulating data.
This is a research-allocation recommendation, not a stand-down of live trading.
No probe, collector, order, configuration, XOS object, or existing gate is changed.

## Mission and completion checks

The operator supplied the scope: the mmsell10 family / live tag `Fmmsell10` and the
path to real dollars. No scope menu is needed. Complete means verified ops
grounding, at least eight candidates, six-axis screening, bucket-specific fill
selection reasoning, realistic dollar arithmetic, at most two promotions, and a
reconciled holds queue. No probe-building or live changes; scorecard edits are
limited to this run's ledger bookkeeping. All checks are met by this document.

Repository grounding: default branch commit
`17f72e199433c8422cc747258ea14a2f797a2406`. Build OS v0.12 matches canonical v0.12.
Existing research is continued; this screen opens no development workstream.

## 1. Verified evidence, with its actual scope

Ops is shared. Another session had already submitted the same-scope Control Tower
and truth reads. This run inspected their completed, per-ID results rather than
submitting duplicate requests. It did not drive or reset that session's channel.
These are contemporary production reads, not numbers inferred from the handoff.

| Read | Verified finding | Source |
|---|---|---|
| Real money, 2026-09-30 12:07:04 UTC | **+$2.4854 realized**, 689 settled positions; **+$0.1650 unrealized**, 28 marked; **+$2.6504 total** | [mmideas-truth-0930](https://github.com/50thycal/kalshi_bot/blob/600cccac/ops/results/mmideas-truth-0930.txt) |
| Same read, execution | 715 contracts filled; 934 distinct tickers ordered, 717 tickers flagged filled; 30 open under the cap rule; $24.5448 held cost basis | Same source. These distinct counting methods are not interchangeable. |
| Same read, twin | $23.5968 realized on 1,141 simulated trades | Same source; this is **not live money**. |
| Current XOS, 2026-09-30 12:05:20 UTC | `NEW_ONLY`; IDEA 0, PROBE 0; five PAPER experiments, four LIVE_CANARY experiments, no PRODUCTION experiment | [mmideas-ct-0930](https://github.com/50thycal/kalshi_bot/blob/7af26cf0/ops/results/mmideas-ct-0930.txt) |
| Current comparability | MMSELL contest-cap experiment's gates are `BLOCKED_PLATFORM`: `shared_primary_ownership_20260921`, impact #13, accepted `I2/NEW_EPOCH` but not applied | Same XOS source. Platform Change Review owns resolution. This screen neither repairs nor bypasses it. |
| Historical three-bucket diagnosis, September 29 | Twin P&L: filled 674 / 92.9% / +0.14¢; ordered-unfilled 197 / 99.0% / +6.56¢; never-ordered 240 / 96.3% / +3.14¢ | [mmideas-q1b-0929](https://github.com/50thycal/kalshi_bot/blob/600cccac/ops/results/mmideas-q1b-0929.txt) |
| Historical matched accounting, September 29 | 664 common settled markets: twin +0.22¢ versus live +0.31¢, gap −0.09¢ | [mmideas-parity-0929](https://github.com/50thycal/kalshi_bot/blob/600cccac/ops/results/mmideas-parity-0929.txt) |

The three-bucket numbers are **twin outcomes grouped by live execution**, not
three realized live returns. Their samples differ from the incumbent simulation
under the live tag, which currently contains 612 never-ordered rows. Mixing those
populations would manufacture a new conclusion. Matching prices reconciles
accounting; it does not demonstrate that the unused orders were attainable.
The remaining twin/live difference combines **admission/universe differences and
selection among admitted orders**. It is not solely a queue-position problem.

Mechanics and envelope were checked against
[the canary plan](MMSELL10_CANARY_PLAN.md) and
[the contest-cap successor / generation-2 record](MMSELL_CONTEST_CAP_CANARY.md):
post-only NO bids, max-YES-price rule, one contract, four-hour timeout,
hold-to-settlement, contest cap and existing loss/exposure bounds. The current
tag is generation 2; earlier tags are not interchangeable controls.

**Correlation baseline and duplication check.** The five paper experiments are
all MMSELL-related. Reviewed-universe and correlation-cap experiments already
exist; liquidity-incentive MM already exists separately. None is a fresh
promotion opportunity merely because an older document says it is unregistered.
`Rmmsell1` / `Rmmsell2` are actually accruing, confirmed by
[cc-rmm-0930b](https://github.com/50thycal/kalshi_bot/blob/600cccac/ops/results/cc-rmm-0930b.txt).

**Research prior.** Read the journal, edge-research record, roadmap, fill model,
offset A/B, chase/veto/thin theses, parity diagnosis, and idea-model scorecard.
The scorecard's 19-promotion figure is a dated historical tally, not a current
total; later entries are separate. Its original successful PIN15 book was later
retired. Current XOS shows no production book. This run does not repair historical
scorecard tallies or treat old fill-model projections as contemporary profits.

The verified graveyard includes chase, flow veto, thin markets, historical
series keep/drop, positive price offset, early cancel, stop/TP exits, volatility,
spread/depth and scan expansion. The September 29 keep/drop test was −0.98¢
versus −0.45¢ ([ops](https://github.com/50thycal/kalshi_bot/blob/600cccac/ops/results/mmnext-oos-0929.txt)).
No revival appears below without its mechanical distinction.

## 2. Dollar hurdle and scoped board

From epoch start `2026-09-07T02:03:36.815119Z` to the truth read: **23.419 days**.
At the observed calendar pace:

| Scenario | 30-day dollars | Interpretation |
|---|---:|---|
| Actual realized result, one contract | **$3.18** | $2.4854 × 30 / 23.419; descriptive pace, not expected return |
| Three times actual result | **$9.55** | Assumes three contracts fill identically and constraints never bind; unproven |
| Twin's all-fill result | **$30.23** | Historical optimistic reference at one contract, including a different admission set |
| Three times that twin | **$90.68** | Still below $100; neither executable nor a universal capacity ceiling |

Current settled cadence is about **883 one-contract positions per 30 days**,
with +0.361¢ per settled position. To reach $100 at that cadence and three
contracts requires **+3.78¢ net per contract**. At the present point estimate it
would take about 31.4 contracts per position, before worse fills or binding
capital limits. That is arithmetic showing the gap, not a sizing proposal.

Even the three-contract case cannot be silently implemented: at 93–97¢ it needs
$2.79–$2.91 per order, exceeds the current $1 limit, and triples a loss on the
same outcome rather than adding independent observations. At 40 fully occupied
slots it would require roughly $112–$116, before other books, against the
historically documented shared $100 exposure backstop. No such enlargement is
assumed feasible here; live configuration was not changed or freshly audited.

The September 29 board survey in [the preceding run](IDEA_MODEL_20260929.md#phase-1-the-board-within-scope)
places activity mainly in sports, elections, politics and crypto. Current
Fmmsell10 holdings confirm sports, crypto, weather and mentions. The relevant
liquidity evidence is our actual filled order rate, not the survey's average
spreads. Cheap-band books historically have mostly 0–2¢ spreads; whole-series
averages previously generated false liquidity-vacuum ideas. Sports state
changes, price feeds, weather updates and utterances can all inform the taker
who chooses to hit our resting bid. Settlement timing and capital occupancy vary
across those families; broadening names is not diversification by itself.

**Fees.** Measure actual exchange fill fees, matched by order, and use the
point-in-time series schedule for hypothetical fills. The official
[July 7, 2026 fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf), checked
September 30, distinguishes maker and taker multipliers and states no settlement
fee. Its rounding wording and illustrative table are not entirely consistent;
do not substitute an assumed 1¢ maker charge for paid fees. The current truth
read reports only $0.0837 of entry fees, so fee amortization cannot close a
$100/month gap. Hold-to-settlement incurs no second trading leg.

## 3. Divergent slate, before scoring

Each idea names the counterparty problem; none assumes a positive fill-selection
effect simply because its unconditional paper sample won.

| Candidate | Bucket moved; mechanism and adverse-selection challenge | Honest economics at small size |
|---|---|---|
| **A. SIZE-6**: three contracts only where original YES entry is ≤6¢ | **Filled**, more units of the same outcome. Old mmsell3 evidence at exactly 6¢ suggested neutral selection; this does not establish ≤6¢ neutrality in Fmmsell10, nor the quality of units 2–3. A larger uninformed taker might fill all three, but an informed taker can too. | Entire-book threefold benchmark is only $9.55/month. A filtered subset needs its own positive edge and observed extra-unit fills; no forecast granted. |
| **B. REVIEWED-EXPANSION**: rules-reviewed, previously tier-blocked series | **Never-ordered → attempted**, after exact rules review while preserving safeguards. New premise versus P&L keep-lists is governance completion, not ranking past winners. Retail participation could add flow, but never-posted winners have no demonstrated fillability. Reviewed-universe research already runs. | September 29 never-ordered twin earned $7.54 over roughly 22 days, about $10/month under perfect fills. Not attainable profit; incremental executable dollars unknown. |
| **C. BUSY-FLOW**: prior completed-hour V24 >11,365 | **Filled/unfilled → not attempted** for the excluded set. Distinct from killed THIN: opposite mechanism, diffuse participation may dilute informed flow. Distinct from FLOW-VETO: unsigned 24-hour activity rather than signed 10-minute imbalance. High volume might instead mean faster informed takers. Both existing samples were already inspected. | Discovery's 374 fills in roughly 22 days at +0.97¢ imply about $4.95/month at one, $14.84 at three, with unchanged fills. Post-hoc illustration only. |
| **D. INVERSE-OFFSET**: one cent below initial NO bid | Some **filled → unfilled**, remaining fills buy NO 1¢ cheaper. Mechanically new versus +1¢ offset: demand more compensation and surrender priority. But sweeping through the better bid can select even more strongly against us. | With exactly the same 883 fills, saving 1¢ adds $8.83/month. The same-fill assumption is false in general; total profit could fall or turn negative. |
| **E. CONTEST-CHOICE**: choose highest premium among simultaneous admissible rungs, retain cap=1 | Changes which candidate is **never-ordered** within a contest. New versus lifting a cap: allocation within the existing bound. More premium also implies more tail risk; no independent reason the chosen rung has better conditional EV. | Unknown. Dollar uplift cannot be inferred from premium without loss probabilities and counterfactual queue fills. |
| **F. FASTER-SETTLE**: prefer shorter rule-defined holding windows under fixed capital | Long-duration **filled → not attempted**, potentially admit short-duration **never-ordered**. New versus early cancel: selection before posting based on contractual occupancy. Faster sport resolution can coincide with more informed trading. No demonstrated current cap bottleneck or per-fill edge. | At unchanged trade count, $0 from faster turnover alone. Increment requires measured cap-bound opportunities and positive EV. |
| **G. MMSELL-REWARD-OVERLAY**: quote only paid incentive markets | Changes attempted universe and adds a **cash subsidy**, rather than fixing fill selection. Paid rewards could cover toxic fills, but eligibility, competition, adverse selection and shared budgets still decide net dollars. The mechanism belongs to existing liquidity-incentive MM. | No reward revenue credited without attributable realized payouts net of losses; duplicate of an existing research line. |
| **H. EXTERNAL-FAIR-TAIL**: require independently estimated loss probability below premium | Filters **filled/unfilled**, potentially adds **never-ordered** markets. A calibrated point-in-time sports distribution could distinguish lottery demand from rational tail buying. Mechanically different from past-series rankings or lead-lag; model error and stale odds could be exactly what the taker exploits. No verified dataset/model for this scope. | Unknown; paid feed/build costs could exceed the current book's $3.18/month pace. No existing proven fair-value edge credited. |
| **I. DEPENDENCE-AWARE-SIZE**: size by independent contest/day risk rather than number of tickers | Same **filled** bucket, redistributes size. New as a risk-allocation question, not as alpha; correlated adverse fills remain adverse. Existing contest-cap study already tests the nearby mechanism. | No new expected edge. Diversifying or reducing losses may improve risk, but cannot turn zero conditional EV into $100. |

## 4. Six-axis screen

Scores: **0 unfavorable, 1 uncertain/conditional, 2 favorable**. They are ordinal,
not probabilities and not added into a magic promotion threshold. C = portfolio
correlation/diversification value; E = mechanism; $ = net cost survival; T =
testability **now**; K = meaningful capacity; R = infrastructure reuse.
T=0 or an unsupported net edge prevents promotion even with reusable code.

| Candidate | C | E | $ | T | K | R | Call and binding reason |
|---|---:|---:|---:|---:|---:|---:|---|
| A SIZE-6 | 0 | 1 | 1 | 0 | 1 | 2 | **HOLD**: contemporary cheap-cell edge and marginal unit fills unproved; sizing is downstream of edge. |
| B REVIEWED-EXPANSION | 0 | 1 | 0 | 0 | 1 | 2 | **KILL new promotion / retain existing work**: governance review is not a return signal; no queue tape for never-posted candidates. |
| C BUSY-FLOW | 0 | 1 | 1 | 0 | 1 | 2 | **HOLD, highest value**: cheap to test later on actual natural fills; no untouched mature sample now. |
| D INVERSE-OFFSET | 0 | 1 | 1 | 0 | 1 | 1 | **HOLD, lower priority**: counterfactual path coverage and queue fill bounds unestablished. |
| E CONTEST-CHOICE | 0 | 0 | 0 | 0 | 1 | 1 | **KILL at screen**: premium ranking has no independent conditional-edge premise; rejected candidates lack equivalent execution evidence. |
| F FASTER-SETTLE | 0 | 0 | 0 | 1 | 0 | 2 | **KILL at screen**: turnover is not the measured bottleneck and faster settling does not produce EV. |
| G REWARD-OVERLAY | 0 | 1 | 1 | 1 | 1 | 2 | **KILL duplicate promotion**: defer to existing liquidity-incentive experiment; no new MMSELL book. |
| H EXTERNAL-FAIR-TAIL | 0 | 1 | 0 | 0 | 1 | 0 | **KILL from this run**: speculative new model/feed project without data or validated edge; high research cost. |
| I DEPENDENCE-AWARE-SIZE | 0 | 0 | 0 | 1 | 1 | 2 | **KILL as profit thesis**: risk allocation cannot supply missing alpha; existing cap work is not restarted. |

All candidates share substantial MMSELL outcome exposure. None receives a
diversification bonus. A screen KILL is not an XOS evaluator verdict and is not
a claim that a mechanism is impossible in every market.

### Why inverse offset does not earn a convenient retrospective promotion

[WS-019 telemetry](MMSELL_QUEUE_FILL_TELEMETRY.md#sampling-lifecycle-per-order)
subscribes after posting, then normally retains the market only 15 minutes after
the actual order becomes terminal. The implementation confirms retirement in
`kalshi_bot/execution/collector.py::refresh_tracked`. A hypothetical lower bid might
still be waiting for hours after that actual fill. Neither missing ticks nor a
last observed quote can be classified as a counterfactual non-fill.

Similarly, an apparent price touch is not proof that a hypothetical order behind
the queue filled. Existing tape could support conservative feasibility bounds,
but only after a coverage-only census proves the complete path and initial book
are available for a representative sample. Selecting only complete late-fill
paths itself biases the sample. This is a HOLD, not a justification to build a
new collector or to recycle the killed positive-offset experiment.

## 5. One frozen follow-up specification — HOLD, not promotion

**BUSY-FLOW prospective specification, frozen on this document's commit.** This
section preserves a potentially cheap future falsification. It authorizes no
probe, schedule, deployment or trade. No new slice-level outcome query was run
in this session. September 29 results are discovery only.

**Mechanism:** broad recent unsigned volume supplies more ordinary longshot
buyers relative to informed ones. Falsified if actual selected fills are not
positive after fees or if excluded trades contributed as much total profit.

**Enrollment:** T0 is the first UTC midnight after BOTH (a) this specification is
merged and (b) Platform Change Review records an applied disposition allowing
comparable evidence for the relevant MMSELL epoch. Use one designated comparable
one-contract baseline epoch; do not pool predecessor tags or bridge drift.
If Fmmsell10 has ended, no successor is enrolled automatically: a new
pre-registration must name it before its outcomes are inspected.

**Rule, fixed:** retain original attempted orders whose V24 is **strictly greater
than 11,365 contracts**, using the preceding 24 fully completed hourly candles
ending at or before decision time. Use `execution_order_context.decided_at`,
else order creation; `volume_fp`, else `volume`. No threshold refitting, no
tercile recalculation, no series exclusions learned from outcomes, no combination
with SIZE-6. Exact fees and all normal risk constraints remain in the accounting.

**Endpoint:** one final outcome read at T0 +90 days; allow seven more days for
settlement. Count only orders decided inside the fixed 90-day window. Coverage
counts may be checked without revealing conditional returns. No interim
significance stopping, extensions, replacement thresholds, or new cuts.

| Gate | Frozen requirement / disposition |
|---|---|
| B0 provenance and coverage | ≥95% V24 coverage over all attempted orders; ≥95% determinate outcomes by final read; documented epoch comparability, actual fill prices/fees, no unresolved material attribution defect. Missingness reported by original attempt and fill status. Failure → HOLD instrument, not PASS. |
| B1 information floor | ≥1,000 distinct settled filled selected markets and ≥40 distinct settlement dates. Multiple orders or contracts in a market do not add observations. Shortfall → HOLD, no extension in this specification. |
| B2 positive edge | Mean selected net return ≥+1.5¢ per contract and one-sided 95% lower bound >0 using settlement-date block bootstrap, 10,000 resamples, seed 20260930. Report contest clustering and effective dates. Mean ≤0 at the B1 floor → KILL; positive but insufficient → HOLD. |
| B3 economically useful selection | On the same attempted-order stream, dropping excluded orders increases total net dollars with a date-block one-sided 95% lower bound >0. Unfilled attempts contribute zero. This paired retained-vs-baseline calculation measures foregone trades, not extra hypothetical replacement fills. Failure at adequate floors → no promotion. |
| B4 contribution and robustness | Selected observed one-contract net P&L /90 ×30 ≥$5/month; both fixed chronological halves positive; no one settlement date supplies >25% of positive-date profit. Report market/series mix without promoting subgroups. Failure → no promotion. |

**Decision:** only all B0–B4 passing would justify a separate validation handoff.
It would **not** authorize a size increase, filtered live operation, or a paper
book assumed to fill. A new filtered policy can change which later opportunities
are admitted under contest/capital constraints; the fixed-attempt comparison is
an observational screen, not a complete policy backtest.

**Staged plan for that later session:** first a small read-only coverage/count
census over existing orders, contexts and fills plus public historical candles;
return only eligibility, unique markets/dates, missing features and settlement
coverage. No new production table. Below B0/B1: stop at HOLD. Only with sufficient
support, the separately authorized probe-builder can specify the full scoring
query/script, freeze provenance and apply the gates. Reuse the V24 definition
from `mmsell_thin_market_probe.py`; do **not** rerun that old probe as a fresh test.
A new script would need ops-runner allowlisting, whose merge is the operator's
hard stop. None is written here.

The 90-day floor deliberately makes the cost visible: the observed discovery
cadence of about 17 busy fills/day might supply the sample, but seasonal mix and
epoch availability may not. Even a PASS at $5/month would imply only a
**hypothetical** $15/month at three identical fills, not $100. No guaranteed
future research time is committed to this hold.

## 6. Reconciled holds and stop rule

| Lead | Trigger status | What would change the decision |
|---|---|---|
| BUSY-FLOW | **NOT FIRED**: no untouched prospective cohort exists under the frozen specification | Natural enrollment, complete comparable evidence, then B0/B1 counts. Highest-value hold, not an active probe. |
| SIZE-6 | **NOT FIRED**: old 6¢ calibration is not current evidence for ≤6¢ or extra units | A prospectively established positive one-contract edge, then a separate marginal-fill/capacity study; unchanged limits cannot support three contracts. Do not launch a second simultaneous filter test. |
| INVERSE-OFFSET | **UNVERIFIED**: representative full four-hour paths and executable queue bounds not established | Outcome-blind coverage census and conservative fill-identification design; no fresh collector approved. |
| Unreviewed universe | **Existing line, not a new hold** | Existing reviewed-universe work and exact rules review; never-ordered paper wins are not fill evidence. |
| Purchased-tail hedge / per-book risk budgets | **Still PARKED** | Operator separately admits work. No hedge study or infrastructure start in this run. |
| GRIDPIN | **Unchanged; outside scope** | Existing roughly 60-settled-day census trigger around end October; not rerun here. |

Chase, veto, thin-market, series keep/drop, positive offset, queue cancel and
previous filter/exit ideas remain closed. No renamed variants enter the queue.

**Recommendation:** stop allocating new build/probe effort to MMSELL now. Let
already-authorized operations follow their own contracts. Reopen MMSELL research
only on new mechanistic evidence, or a count-only census showing that the one
frozen hold has become gradeable. A later null is a stop, not permission to
invert another threshold. Resolving the platform block is separately owned
operational governance; it does not itself establish trading edge.

**Ledger:** zero promotion rows are due. The scorecard receives only a dated
zero-promotion run note; existing results, gates and historical base rates stay
untouched. This recommendation has not retired or stood down any experiment.
