# Earnings-call mentions: a different research direction

**Research Lab · scoped `kalshi-idea-model` · 2026-09-30.**

## Mission

Calvin authorized publishing the MMSELL screen and choosing a different research
direction. Scope: **pre-call corporate earnings mentions, using issuer transcript
history**, with immediate taking and hold-to-settlement as the hypothetical
execution. This continues the existing MENTION-corpus hold; it is not a new
maker-sell cell or a claim that a forecasting edge has been found. No probe,
collector, trade, experiment transition, or executable change is in scope.

**Result: nine candidates screened; zero promotions. Prioritize C1's bounded
data-feasibility question as a HOLD.** Official archives partly resolve the
source blocker, but the four checked retained-data tables have no matching
earnings-mention rows. Point-in-time public quote recovery and source version
coverage are unproved. Do not spend on a model or feed merely because the market
has volume.

## Grounding and the material difference

Repository baseline is `17f72e199433c8422cc747258ea14a2f797a2406`.
Read the research journal, edge research, scorecard, September 12/29 idea-model
records and current XOS evidence. The scorecard's dated aggregate is not a
fresh performance statistic: its original PIN15 success was later retired,
and model-vs-quote promotions THETA and MLBWX both failed. The inference is a
low prior for another forecast, not a license to recycle the same model.

This is a **continuation of MENTION-corpus**, explicitly parked in
[September 12](IDEA_MODEL_20260912.md) and
[September 29](IDEA_MODEL_20260929.md). It differs from EARNBEAT's numerical
consensus/KPI anchoring and from the MMSELL/MENTION maker-sell cell. The proposed
signal is a directly countable issuer language habit rather than extrapolated
tail-distribution shape. Whether that habit adds anything to market prices is
unproved. The new evidence is **source discovery**, not a new launch or a
measured return. Mid-call MENTIONLOCK stays a separate blocked implementation
within the same held family; it does not inherit pre-call feasibility.

The September 30 12:23:39 UTC ops doctor confirms `NEW_ONLY`, with real-money
runtime tags `Fmmsell10,Alimm1`. The contemporary Control Tower read used in
[the MMSELL screen](IDEA_MODEL_MMSELL_20260930_CHATGPT.md) lists five MMSELL paper
experiments, no IDEA/PROBE objects and no production-stage experiment.
This does not imply zero actual live trading. The candidate has a different
signal and execution mechanism, but can overlap existing mention exposure and
shared capital; no empirical zero-correlation claim is made. Existing platform
readiness findings remain owned by their current roles.

### Primary-source availability audit

| Source inspected September 30 | What it establishes | What it does not establish |
|---|---|---|
| [Microsoft FY2025 Q4 official call](https://www.microsoft.com/en-us/investor/events/fy-2025/earnings-fy-2025-q4) | Dated July 30, 2025; readable prepared remarks and named-speaker Q&A in one archive. | Target-call remarks are not a pre-call feed: the page says prepared remarks appear after the call, followed by a full transcript. Current HTML is not proof of its historical publication/version timestamp. |
| [Alphabet Q2 2025 official call](https://abc.xyz/investor/events/event-details/2025/2025-Q2-Earnings-Call/) | Dated July 23, 2025; full HTML transcript including Q&A and speaker labels. Other quarters have official archive pages. | Eight prior versions per future issuer-call, exact contract eligibility, and broad issuer coverage remain uncounted. |
| [Rev's speech datasets](https://github.com/revdotcom/speech-datasets/blob/main/README.md) | Published earnings-call audio/transcript datasets provide potential parser fixtures. | Speech-recognition benchmarks are not a contemporaneous Kalshi price/label panel and do not show an executable edge. |
| [Kalshi's July 21, 2025 announcement](https://news.kalshi.com/p/the-week-ahead-ethereum-earnings-and-even-more-epstein) | Tesla/Alphabet earnings mentions already existed in July 2025. The family is older than two months. | The age of the family does not establish enough independently settled, source-matched calls for C0. |

The old blanket “no cheap transcript source” blocker is **partly resolved** for
pre-call work. Publication/version history, coverage, contract matching and
executable quotes remain unresolved. An utterance timestamp is not a publication
timestamp. No large dataset was downloaded. Official archive acquisition via
the repo's current `desk_fetch` is not available for these hosts; this run used
web retrieval and did not change its allowlist.

### Actual board, not cached search prices

`chatgpt-mention-board-0930` ran existing `kalshi_desk_board` on September 30 at
12:24:49 UTC, prefix `KXEARNINGSMENTION`, horizon 2,160 hours, minimum volume zero.
It scanned 8,000 open events, retained **304 word markets**, printed the top 40
by volume and summed **54,474 contracts of 24-hour volume** over all retained
markets. This is a bounded scan, not a complete venue census, not 304 calls,
and not 54,474 executable units at our desired price.

Examples from that timestamp: Micron “Nvidia” 49/50 cents, “Hyperscaler” 35/36;
Cal-Maine “Echo” 59/72; Accenture “Iran / Middle…” 64/75; Levi's “Dockers” 8/47.
Spreads of 1 to 39 cents in these examples make mid-price backtests unacceptable.
Quotes are observations, not recommendations or current prices after this read.
Many markets share a call; settlement frequency is quarterly per issuer and
calendar activity clusters in earnings season. News, releases, eligible speech
and analyst questions change the underlying probabilities.

The board's Micron title is **post earnings analyst call**, not the standard
earnings call. Exclude such events from C1 unless the exact event has eight
comparable archived predecessor sessions. Likewise `close_time` can include a
long administrative resolution buffer: the printed 14-day close for September
30 calls is not a two-week forecasting horizon. The source calendar and exact
contract event must define decision time.

The existing public-structure reader (`chatgpt-mention-rules-0930`, 12:25:31 UTC)
confirms 14 Cal-Maine, 16 Micron and 15 Microsoft word markets in the three
inspected events, all `mutually_exclusive=false`. Cal-Maine and Microsoft rules
include company representatives **and the call operator**, with Q&A; an external
analyst is not automatically an eligible representative. Micron explicitly
specifies the post-earnings analyst call. Cal-Maine also mixes ordinary
single-occurrence words with **3+ occurrence** contracts. Those count contracts
are excluded from C1's binary-occurrence rule. A title-only parser would miss
both the speaker and multiplicity distinctions. Inspect full secondary rules
before including any historical contract; these excerpts alone are insufficient
for a production-grade labeler.

### Outcome-blind retained-data count

The read-only SQL request `chatgpt-mention-coverage-0930`, September 30
12:27:46 UTC, counted the exact prefix `KXEARNINGSMENTION%`. It returned:

| Retained table | Rows | Distinct tickers |
|---|---:|---:|
| `markets` | 0 | 0 |
| `market_snapshots` | 0 | 0 |
| `orderbook_snapshots` | 0 | 0 |
| `execution_order_context` | 0 | 0 |

The statement asked only counts and first/last timestamps, never outcomes or
P&L. This rules out reusing **these four tables** as a ready-made C1 panel. It
does not establish that no other table or public historical endpoint has data.
No complete public settled-event/candle/source join was attempted in this
idea-only run; its eligible count is **unknown**, not zero. C0 therefore does
not pass. A public-market fixture establishes that at least one Microsoft call
is finalized, but 15 words from that one call cannot clear a 60-call gate.

Immutable ops evidence (default-branch code stated in every result):

- [Capabilities](https://github.com/50thycal/kalshi_bot/blob/6791ffb497c53f0298484e03c8fab7b2cf3f1a89/ops/results/chatgpt-mention-cap-0930.txt)
- [Doctor / current operating roster](https://github.com/50thycal/kalshi_bot/blob/e180ea6e75843f06f4ba64421e037334c09c2de6/ops/results/chatgpt-mention-doctor-0930.txt)
- [Bounded public board](https://github.com/50thycal/kalshi_bot/blob/2a98a51255c1dda9b345d5e5e609bcac262ef239/ops/results/chatgpt-mention-board-0930.txt)
- [Three-event primary rules / schema](https://github.com/50thycal/kalshi_bot/blob/b6a0ebef2635d834321edd73991155f24e4a5560/ops/results/chatgpt-mention-rules-0930.txt)
- [Counts-only retained coverage](https://github.com/50thycal/kalshi_bot/blob/58682e5bb35b2df5e722e4d403f23b11814bfe33/ops/results/chatgpt-mention-coverage-0930.txt)

Only existing read-only scripts and a bounded SQL count ran. No predictive
study ran. The request channel was returned to noop after these reads.

## Candidate generation, before scoring

These nine candidates were enumerated before scoring or an outcome study. They
span historical forecasting, public-news updates, rules mechanics, relative
value and the adjacent live-information mechanic within this chosen scope.

| ID | Candidate and proposed source of edge |
|---|---|
| C1 | **REPEAT:** issuer-specific recurrence in the previous eight calls identifies routine language that traders underweight relative to newsworthy words. Take YES only when a conservative recurrence estimate clears the ask and costs. |
| C2 | **RARE:** buy NO on topical words absent in all eight prior calls; traders may overpay for the current news narrative. |
| C3 | **RELEASE:** words newly present in the issuer's public earnings release raise their probability on the upcoming call before mention quotes update. |
| C4 | **SPEAKER:** condition occurrence on eligible speakers rather than any transcript text; correct prices that confuse an analyst's question with an eligible executive's answer. |
| C5 | **RENAME:** detect a product's replacement branding from official announcements, then buy NO on the obsolete literal name. |
| C6 | **PEER:** an earlier competitor call identifies a shared topic likely to appear in a later issuer call. |
| C7 | **SEASON:** use the same fiscal quarter in prior years for terms tied to annual planning, holidays or annual reports, rather than recent-quarter recurrence. |
| C8 | **CONTAINMENT:** two word contracts with genuinely nested resolution conditions could support a cost-gated relative-value position. |
| C9 | **SAID:** after an eligible speaker says the term, take an unresolved YES quote using timestamped audio. |

## Frozen feasibility and validation specification

**Recorded before any candidate performance calculation. This is a conditional
specification, not a promotion or authorization to build.** Grounding exposed a
few public settled examples; Microsoft April/July 2026, CrowdStrike June 2026,
and any other event whose outcomes were viewed in this run must be marked
discovery-only. No confirmatory result may include them. Source discovery and
book inspection are not a backtest.

### C0 — cheap counts-only recon, before any probe

Enumerate corporate earnings-call events, excluding political speeches,
conferences, KPI beat/miss ladders and duplicate listings of the same call.
Join issuer, actual scheduled call start, settlement rules and source archive
availability. Do not compute candidate P&L while deciding coverage.

The gate requires **at least 60 independent settled issuer-calls, 10 issuers,
two calendar quarters and 20 distinct call dates**, with at least 300 listed word
contracts in total. Each eligible call needs eight preceding complete issuer
transcripts, a verifiable pre-decision source version, unambiguous speaker/word
rules and actual pre-call quotes. A count of words is not a count of independent
trials. Report all exclusion counts and coverage relative to the full universe.

Decision time is exactly **60 minutes before the scheduled call start**. Require
a quote observation at or before that time, no older than 60 seconds, with a
valid bid/ask and observable size. No interpolation from later candles, trades
or settlement. Candlestick bid/ask observations can support an explicitly
indicative one-contract analysis only if their timestamps actually identify the
decision-time quote; candle extrema and trade-close prices cannot. Historical
depth remains a separate capacity gate. Missing source availability timestamps
or quote coverage means HOLD, not estimated fills.

The eight source calls must end at least seven days before decision time, and
the exact transcript version must be demonstrably available before that time.
A fiscal date or word-aligned audio timestamp does not establish publication
time. A transcript retrieved today can contain later corrections. If historical
version evidence is missing, prospective capture is a separately authorized
collection project, not a reason to relax the gate here.

### Primary candidate C1 — one fixed rule, if C0 ever passes

Restrict C1 to single-occurrence contracts; exclude count thresholds such as
“3+ times.” Map each listed word to the literal forms and speaker scope permitted by its
own rules. Case folding and token boundaries are deterministic; do not infer
synonyms, translate, stem, or have an LLM decide semantic equivalence. Ambiguous
rules are exclusions. Past occurrence means at least one qualifying utterance
in the full eligible call, including Q&A when the contract includes it. All
source text must be limited to speaker utterances, not headings or navigation.

Let k be the number of preceding eight calls with a qualifying occurrence.
Use p=(k+1)/10, a fixed smoothed estimate, **not** a calibrated probability claim.
Require k>=7 and p minus the executable YES ask minus per-contract taker fees
minus a 1-cent execution stress to be **at least 10 cents**. Rank eligible
contracts by that surplus and take only the highest per issuer-call; break ties
lexicographically by ticker. One hypothetical contract, no resting, chasing,
exit, averaging or second attempt. Hold to settlement. Other candidates are
not alternate models to select after seeing this rule's result.

After C0 clears, freeze the complete event manifest and source hashes before
unblinding target labels. Require **60 selected calls** as well as the C0
universe floor; absence of enough qualifying trades is a capacity/testability
HOLD. First chronological half is diagnostic, last half confirmatory; the rule
is fixed in both and cannot be fit on the first half. Each half needs 30 selected
calls, five issuers and ten distinct dates. Duplicate calls never cross halves.

- **P1, economic value:** full sample and each chronological half must earn
  >=5 cents per contract net of actual fees and the 1-cent stress. A one-sided
  95% lower confidence bound for full-sample mean must exceed zero, using
  10,000 resamples of whole call dates (seed 20260930); report issuer-cluster
  sensitivity too, whose lower bound must also exceed zero. Failure is KILL
  for this specification, not a prompt to change k or the price cutoff.
- **P2, concentration:** at least 60% of represented issuers have positive net
  P&L; no single issuer contributes more than 35% of positive issuer P&L.
  Failure is KILL as a diversified earnings book. Words from one call never
  supply extra confidence. Report shared-topic/date concentration explicitly.
- **P3, dollars:** over the full calendar evaluation window, excluding no quiet
  months, depth-supported five-contract execution must imply >=$25/month net
  and still satisfy P1/P2. This is a contribution floor, not a $100 claim.
  Unknown historical depth is HOLD for sizing; failure at measured depth is
  KILL for the standalone business case. No linear multiplication of one-lot
  results without depth evidence.

Only C0 plus P1/P2/P3 passing could justify a later proposal for a registered
paper experiment. A historical pass alone cannot activate paper or live money.
Future probe building belongs to `kalshi-probe-builder`; any required
`scripts/ops_runner.py` merge remains an operator hard stop. The cheapest next
research action is a bounded coverage inventory, not a full NLP model.

## Six-axis screen

Scores: 0 unfavorable, 1 uncertain, 2 favorable. C = diversification versus
current books; E = plausible information advantage; $ = cost survival;
T = testability now; K = capacity/venue maturity; R = infrastructure reuse.
Scores are ordinal and are not added. A missing T gate cannot be offset by a
good story. K=1 recognizes existing trading but unproved eligible-call capacity.

| Candidate | C | E | $ | T | K | R | Decision and reason |
|---|---:|---:|---:|---:|---:|---:|---|
| C1 REPEAT | 1 | 1 | 1 | 0 | 1 | 1 | **HOLD, priority lead.** Official source discovery makes a bounded inventory worthwhile; no joined point-in-time panel or excess return is established. |
| C2 RARE | 1 | 0 | 1 | 0 | 1 | 1 | **KILL at screen.** The exchange selects currently salient words; absence from old calls is not evidence against a genuinely new topic. “Lottery demand” alone repeats an unverified favorite-buy premise. |
| C3 RELEASE | 1 | 1 | 0 | 0 | 1 | 1 | **HOLD within MENTION-corpus.** Needs exact public release times, revision history and post-release executable quotes. A widely watched release supplies no presumed speed advantage. Do not add it to C1 after outcomes. |
| C4 SPEAKER | 1 | 1 | 1 | 0 | 1 | 1 | **FOLD into C1 correctness.** Speaker eligibility is mandatory labeling hygiene, not independently demonstrated alpha. |
| C5 RENAME | 1 | 0 | 0 | 0 | 0 | 0 | **KILL at screen.** Executives can explain a rename by saying the old name; rare transitions supply little independent evidence and weak monthly dollars. |
| C6 PEER | 1 | 0 | 0 | 0 | 1 | 0 | **KILL at screen.** Shared news drives both calls and quotes. No independently established lead signal; topic/time confounding and a new model cost dominate. |
| C7 SEASON | 1 | 1 | 1 | 0 | 0 | 1 | **FOLD into held family, no second model.** A company supplies few same-quarter observations; cannot tune a seasonal variant if C1 fails. Would require separate preregistration on untouched data. |
| C8 CONTAINMENT | 1 | 0 | 0 | 0 | 0 | 1 | **KILL at screen.** These words are not MECE and multiple YES outcomes can coexist. True rule containment would need verified matching and executable two-leg surplus; none was found or demonstrated here. No revival of the structural-arb graveyard. |
| C9 SAID | 1 | 1 | 0 | 0 | 1 | 0 | **HOLD, existing MENTIONLOCK only.** After-call archives do not supply a low-latency public feed or reconstruct which quotes were executable after the utterance. No new audio infrastructure. |

**Counterparty and persistence hypothesis for C1:** participants may focus on
newsworthy subjects and neglect routine scripted vocabulary. A simple public
frequency counter is easy for competitors to copy, so any advantage may already
be priced and has no durable moat assumed. Listed terms are chosen by the
exchange, not sampled randomly from a dictionary; evaluate the actual listed
universe as of each decision and include losing delisted/renamed listings.
The best apparent historical keyword is not a valid ex ante candidate.

**Execution selection:** these ideas do not attempt to recover MMSELL's
unfilled or never-ordered paper winners. C1 chooses an independent pre-call
opportunity set and pays an observed ask immediately. That removes dependence
on waiting for someone to hit a resting quote, but does not remove adverse
information, stale quotes, failed orders or execution latency. Missing depth,
withdrawn offers and rejected orders cannot be filled synthetically. A future
paper design must record failed attempts and compare intended versus attainable
prices. Existing post-order-only WS-019 telemetry cannot represent the whole
candidate universe.

## Cost and monthly-dollar hurdle

For a bought YES contract held to settlement, realized cents per contract are
`100 * outcome - paid_ask_cents - entry_fee_cents`. Deduct the fixed 1-cent
stress in the screen. The ask already incorporates crossing the spread; do not
add the spread again. There is no second trading leg or settlement fee. Use
the actual point-in-time series multiplier and fee rounding; the
[official July 7 schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf)
has base taker factor `0.07 * quantity * price * (1-price)` in dollars. Its
one-lot table shows 2 cents at midrange prices; use that conservative amount for
illustrations, not as a rewrite of platform fee semantics.

An illustrative p=0.80 and 65-cent ask leaves 12 cents after a 2-cent fee and
1-cent stress. **This is a model estimate, not realized EV.** A 50-cent mid with
a 65-cent ask must be evaluated at 65, not 50. The validation floor of 5 cents
is deliberately lower than the admission estimate to allow forecasting error.

At 5 cents of demonstrated net edge, one contract on each of 20/50/100
independent qualifying calls per calendar month would earn **$1/$2.50/$5**.
At five contracts, identical-price illustrations are **$5/$12.50/$25**.
Five contracts at a 10-cent edge need **200 qualifying calls/month** to reach
$100; at 5 cents they need **400**. Five-lot depth and this call supply are
unproved. A quarterly busy-month result cannot be reported as every-month
income. Many word contracts from one call increase correlated risk rather than
independent capacity. There is no honest positive dollar forecast for any
candidate yet; C2/C5/C6/C8 lack even a defensible retained mechanism, and the
held alternatives do not receive separate additive revenue estimates.

Five contracts at a 65-cent ask tie up $3.25 plus fees and can lose that cost
together. That exceeds the presently observed $1 per-order live envelope. It is
a hypothetical economic stress, not an implementable or authorized size.
At existing one-contract size this looks like possible small ballast, not a
demonstrated standalone route to $100/month. The data inventory should kill the
business case quickly if independent-call supply cannot support useful dollars.

## Reconciled holds and finite next action

| Existing lead | Trigger state after this run | Disposition |
|---|---|---|
| **MENTION-corpus / C1** | **PARTLY FIRED:** free official text found; publication/version and quote coverage not established; checked local tables empty | Highest-priority different-mechanism lead. One bounded, outcome-blind public coverage inventory is the next useful research action. Stop at HOLD if C0 fails; no model/probe build. |
| C3 RELEASE, C7 SEASON | Extra signal/version/timing requirements remain unverified | Fold into the existing family as alternatives, not additional promotions or automatic fallback tests. |
| **MENTIONLOCK / C9** | NOT FIRED: no verified contemporaneous utterance/quote history | Keep the existing mid-call hold; pre-call archives do not unblock it. |
| **GRIDPIN** | NOT FIRED: [run 2](GRIDPIN_THESIS.md) has 32 settled days; existing revisit at about 60 days, end October | No rerun, new thesis or competing build. |
| **SEASONPIN** | Latest journal/census supersedes September 29's early “fired” queue | **KILLED**, not an outstanding fired hold. |
| **ECON-REACT** | September 29 rerun remains 13 settled prints / 2 probed; enumeration blocker persists | Existing HOLD; not a newly fired task in this run. |
| **EARNBEAT** | Existing Q3 accrual trigger around week of November 9 | Unchanged HOLD; corporate mentions are not its KPI panel. |
| **TOKENPIN** | Still five settled weeks in latest cited run; needs >=20 plus historical intra-week partials | Unchanged HOLD; earliest weeks-only trigger around mid-January 2027. |
| PMMSPIN, RTPIN/BOXPIN, STREAMPIN, OPTRV, COMPIN, XLOCK-P1 | No new source/count evidence for their triggers here | Carry [prior queue](IDEA_MODEL_20260929.md#holds-queue-reconciled-trigger-state-as-of-2026-09-29) unchanged; no claim triggers fired. |
| RATELAG, HURR, ART/GUARPIN, EQUITY-HUB | Event/listing triggers not verified in this scoped run | Carry prior queue; retain HURR's November 30 retirement condition and ART's after-November-sales check. |
| SPOT-PERP-CARRY | Existing separately owned operator line | Do not regenerate or absorb it. |
| MMSELL research | Prior run recommends pausing new development; frozen holds remain conditional | Publication of that recommendation changes no live book. This run explores a different signal, not a new MMSELL arm. |

**Budget the next action before doing it:** one source/quote coverage inventory,
capped at one research session and no paid feed or new collector. Enumerate
exact corporate-call series/events, report settled independent calls and the
eight-transcript/point-in-time-quote intersection, and sample raw primary plus
secondary rules to audit the join. Reuse public endpoints and existing read
paths only. If that cannot establish C0, keep this family parked until a named
archive/export becomes available; do not keep adding NLP variants. If new code
is required, hand the bounded census design to the separate probe-builder
workflow before implementation. This run does not initiate that build.

**Why this is worth a small look:** cheap official sources exist, the market is
active, and the test concerns a different return driver from maker fill
selection. **Why it is not promoted:** source availability is not alpha, the
retained quote panel is absent, and independent-call frequency can make the
dollar target impossible even with a statistically positive edge. Those are
cheap falsification questions, not a reason to invest in an earnings NLP stack.

**Ledger:** append one zero-promotion run note only. No promotion row, change to
the historical denominator, probe verdict, journal claim of profit, experiment
object or lifecycle transition. This document's conditional gates become a
durable reference on its commit; they have not been evaluated.

## Authorized coverage follow-up — September 30

The subsequently authorized [bounded census](MENTION_CORPUS_CENSUS_20260930.md)
recovered 5,718 finalized public contracts across 415 raw event tickers and
eight archived prior Microsoft transcript versions. Ten of 15 sampled contracts
have aligned pre-decision bid/ask candle closes, but none has resting-size
evidence; independent-call/source reconciliation is incomplete. C0 remains HOLD,
zero promotions. C1/P1/P2/P3 were not evaluated. The specification above is
unchanged; the census and its outcome-masked evidence narrow the reopening
trigger to a suitable historical earnings-market depth export.
