# Market review pipeline — proposal and owner decisions

Status: D1–D9 A approved by Calvin in ChatGPT on 2026-10-05; service implementation requested.
Date: 2026-10-05
Scope: Standalone Railway catalog and MMSELL-first evidence service, with existing-data import. Trading activation and experiment consumer cutover remain separate.

## Confirmed direction

- One repeatable discovery, classification, review, observation, scoring, and refresh pipeline for existing listings and new series/events/markets.
- Comparable definitions and units, with contract semantics separate from strategy-specific economics.
- An iterable design: start with MMSELL; add strategy-specific confidence and edge assessments later without replacing the shared catalog.
- Evidence strength must reflect relevant collected data, especially actual live executions and meaningful observation duration. A two-day paper-only run must not receive an established-live evidence designation merely because it produced many trades.
- Preserve settlement timing, observation-window duration, resolution mechanism, alternatives and qualifying conditions, payout relationships, shared outcome exposure, execution conditions, and evidence provenance.

## Extensible strategy assessments

Store evaluations as records keyed by strategy identifier, strategy version, series/market scope, side, entry context, execution policy, relevant epoch and assessment time. Render strategy-specific columns from those records, such as MMSELL confidence/edge and later strategy confidence/edge. Adding a strategy requires its evaluator and criteria; it must not require unrelated strategies or the shared catalog to be redesigned.

Family rollups are summaries. They do not silently pool incompatible versions, execution policies, prices, sides, or non-poolable epochs. A new strategy begins unscored; it does not inherit MMSELL's edge or confidence.

## Proposed meaning of the scores

- Review completion: which required classification/review steps are finished. Completing all steps may produce a negative assessment.
- Evidence maturity: source and coverage, such as paper provisional, live provisional, or established live evidence. Calendar age without actual relevant observations earns no credit.
- Strategy confidence: precision, relevance, data integrity, time coverage, and execution validation of a particular estimate. High confidence can mean confidently unprofitable.
- Strategy edge: estimated net economics under specified entry and execution conditions.

If D2-A is selected, evidence maturity is the first comparison dimension; paper-only evidence cannot outrank established live evidence on a blended number. Live maturity still requires sufficient actual executions, distinct outcomes, temporal coverage, and data integrity. Several months with two fills is not automatically mature. Paper and live twins on the same underlying outcomes are paired evidence, not independent sample counts.

## Approved owner decisions

Calvin selected A for D1–D9 on 2026-10-05. Exact durations, sample floors, interval widths and score mappings remain unset until measured calibration.

| ID | Decision | A — recommended | B |
|---|---|---|---|
| D1 | Score display | Separate completion, evidence maturity, confidence, and edge. Pro: explains each result. Con: more fields. | One combined score. Pro: simple. Con: hides missing evidence and negative edge. |
| D2 | Paper versus live | Separate evidence tiers; paper-only cannot qualify as established live evidence. Pro: respects actual money evidence. Con: slower qualification. | Weighted blend. Pro: faster early scoring. Con: abundant paper can overwhelm limited live evidence. |
| D3 | Confidence bar | Require distinct outcomes, active observation duration, data quality, and sufficiently narrow uncertainty. Pro: robust. Con: some series wait. | Require only a sample count. Pro: easy. Con: clustered trades and short bursts can qualify. |
| D4 | History | Keep recent and longer-term assessments separately. Pro: detects changes without erasing history. Con: more comparisons. | Use one all-time assessment. Pro: stable. Con: old evidence can hide current deterioration. |
| D5 | New listings | Automatically validate reviewed templates; human review for new or changed semantics. Pro: scales. Con: needs reliable change detection. | Human review every new market. Pro: direct oversight. Con: large backlog. |
| D6 | Refresh | Refresh on new outcomes/material changes plus daily reconciliation. Pro: current. Con: more processing. | Weekly refresh. Pro: simpler. Con: temporarily stale scores. |
| D7 | Collection scope | Catalog broadly; collect deeper execution telemetry for relevant candidates. Pro: controls storage and cost. Con: some histories start later. | Deep telemetry on every market. Pro: broad future research. Con: high storage and collection cost. |
| D8 | Strategy admission | Freeze experimental universes; update through recorded decisions. Pro: interpretable comparisons. Con: slower adoption. | Automatically update membership on every refresh. Pro: adaptive. Con: changing populations complicate interpretation. |
| D9 | Early live evidence | Allow separately authorized small canaries after semantic and paper checks; require live evidence before scaling. Pro: creates the missing live evidence. Con: bounded real-money risk. | Require mature live evidence before any live entry. Pro: strict. Con: new series cannot earn that evidence. |

D8-B, if selected later for production, requires a registered adaptive policy, score snapshots and membership-change logs. It is not silent promotion or a replacement for Experiment OS controls.

## Pipeline shape

1. Discover listings and changes, with a reconciliation sweep to recover missed notifications.
2. Capture stable identity, raw rules, timing, source, qualifying alternatives and shared-outcome relationships.
3. Propose classification with supporting evidence; route unknown or changed semantics to review.
4. Collect relevant opportunities, executions, fees, unresolved exposure and outcomes.
5. Evaluate MMSELL first with context-specific net economics, maturity and uncertainty.
6. Refresh assessments with versioned methods and preserved evidence cutoffs.
7. Generate human documentation and machine-queryable selection views from the same records.

## Initial field groups

| Group | Contents |
|---|---|
| Identity | Series, event, market and cross-series shared-outcome identity |
| Semantics | Contract type, resolution mechanism, settlement source, reviewed rules version |
| Time | Observation window; close, expected determination and expected payout; observed delays |
| Payout | Alternatives/aliases with OR/AND meaning, exclusions, overlapping/nested/exclusive outcomes |
| Risk | Contract ambiguity, position loss, concentration, execution, holding duration; unknown components remain unknown |
| Economics | Net cents per one-contract fill; net per assigned opportunity; maximum-loss-normalized return; fill probability; capital duration |
| Evidence | Paper/live source, actual observations, distinct outcomes, active observation coverage, fees/data completeness, forward evaluation, uncertainty |
| Governance | Review completion, conditions, eligibility assessment, version/as-of/freshness, exact assessment used by a strategy |

Opportunity definitions are fixed per evaluator. Comparability does not imply that every strategy uses the same entry policy or has the same expected return.

## Confidence calibration and later implementation

Do not choose sample floors, observation durations, paper/live weights, numerical risk grades or score thresholds by guesswork. Evaluate candidate requirements using the existing MMSELL record, retain outcomes as the shared-risk grouping unit, address repeated selection across many series, and preserve separate paper/live and recent/historical views.

No requirement for mature live evidence may prevent a separately authorized bounded canary from collecting the evidence it needs. Maturity and confidence are assessments, not automatic permission to trade or scale.

Changes to metric semantics, taxonomy, shared risk or admission must follow the existing Platform Impact process before implementation/activation. Any experiment universe change remains subject to its registered contract. No automatic promotion is introduced by this proposal.

## Acceptance checks for this design checkpoint

- Shared catalog and new-listing pipeline are preserved.
- MMSELL is the first evaluator; adding another strategy is supported by separate assessment records and generated columns.
- Completion, evidence maturity, confidence and edge have distinct meanings.
- Short paper-only evidence cannot masquerade as established live evidence.
- Each owner choice has two options and concise tradeoffs, with recommendations distinct from accepted decisions.
- No numerical threshold, live activation, or runtime behavior is presented as already implemented.

## Next step

Build the isolated service under WS-023 and MARKET_CATALOG_BUILD.md. Backfill is additive; adopt its outputs into experiment consumers only through the canonical Platform Impact process.
