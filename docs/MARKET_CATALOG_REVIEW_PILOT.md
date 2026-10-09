# Catalog classification pilot — proposed reviews

WS-023, 2026-10-09 UTC / 2026-10-08 Chicago. Implementation actor: Codex.
These are proposed interpretations of three listings, not accepted reviews,
series-wide templates, an approved universe or numerical scores.

## Mission and acceptance checks

Outcome: show bounded, source-supported proposals in existing packets and expose
document identity before approval. Finish: tested proposed-only behavior, an explicit
evidence-bar update, sources and a catalog-only PR. Non-goals: source writes, automatic
approval, unproven historical terms, fair-price ledger expansion, numeric calibration
or admission. Stale fields, unknown term versions and exposure remain unqualified.

Acceptance: ten semantic keys/unknowns survive; matching fields never approve drafts;
changed fields flag stale proposals; public fingerprints differ from parent-bound
catalog hashes; URLs never prove PDF contents; ledger/ownership guards stay intact;
other strategies retain their own bars; targeted tests and release handoff pass.

## Verified #564 release

Merge `72c7ebe50b8710e6ee6f6d714f3898eade581db5`, Railway deployment
`f6993ead-5525-4e71-9374-bae5af5eafee` SUCCESS; finalized-head CI passed.
Health, public queue assets and unauthenticated 401s checked. Authenticated production
packets were not checked: OAuth withholds token values. No token was exported through
ops. Local tests do not replace that check. Full dated verification is on PR #564.

## Pilot selection and semantics

A SELECT-only query used the importer's MMSELL fill/order scope. Sanitized receipt:
[priority audit](https://github.com/50thycal/kalshi_bot/blob/ops/ops/results/catalog-review-priority-20261009-0201.txt),
01:59:25 UTC, executing code `72c7ebe50b8710e6ee6f6d714f3898eade581db5`.
Only public tickers/counts exported; ops returned to noop. Counts measure review volume.

| Series | Live markets / fills | Proposed mechanism | Main distinction |
|---|---:|---|---|
| KXMLBHR | 462 / 467 | Eligible player stat plus contingencies | Participation and fair-price fallback |
| KXBTCD | 102 / 110 | Sixty-second BRTI average threshold | Nested same-time strikes share one determination |
| KXTRUMPSAY | 35 / 38 | Phrase/form in permitted public channels | OR variants, written statements and a distinct observation deadline |

These series contain 599 scoped tickers. The pilot proposes one sample each,
not approval for all 599 or a profitability ranking.

The baseball sample's actual game observation boundaries remain unknown; schedule
and market close are not substituted for actual game start/end. Listing secondary
rules and current terms describe lineup/plate-appearance eligibility and specified
fair-price exceptions. A nonbinary payout cannot be admitted by the strict binary
ledger merely to clear a blocker; this PR does not expand that method.

The Bitcoin sample observes Aug 1, 20:59–21:00 UTC: sixty seconds. Its rules compare
strictly against 63,749.99, while the subtitle rounds to 63,750. Preserve the predicate.
Expected determination is five minutes after close, not verified cash-payout timing.

The mention sample observes July 27, 12:00–Aug 3, 04:00 UTC: 156 hours. Trading closes
ten hours after observation ends. This sample has no slash alias; another listing
with slashes needs its own OR interpretation. Synonyms do not automatically qualify.
Personal written posts are included, so a spoken-only classifier misses evidence.
All three current event payloads report nonexclusive markets; that does not prove
independence. Exposure keys in these drafts remain candidate groups.

## Document identity and evidence bar

Listing hashes include inline rules and contract URLs, not linked PDF bytes.
Unchanged URLs cannot establish unchanged text or a version governing an earlier
execution. Each draft captures PDF SHA-256, byte count, URL/time and page references;
it does not archive bytes or prove historical applicability. Baseball/Bitcoin PDF
creation metadata postdates the August samples. Metadata alone proves neither
effective dates nor a content change; seek dated evidence before retrospective use.

MMSELL's bar becomes `evidence-bar-v2`, requiring `verified_contract_document_binding`.
Proof stays false pending established content/version binding. Readiness method v2
exposes it. Accepted review and document proof remain separate. Listing hashes and
ledgers are not rewritten; the bar version causes an ordinary immutable assessment
refresh. Monitor volume headroom. Confidence stays null; qualification stays false.

`kalshi_bot/catalog/review_drafts.json` holds three proposed-only market records.
Packets expose drafts and matching/stale inline field status. They never populate
approved semantics or completion. Drafts have no authenticated catalog review hash;
public field matches cannot serve as parent-bound approval. Risks are qualitative.

## Primary sources

| Series | Sample and full terms |
|---|---|
| KXMLBHR | [Sample](https://external-api.kalshi.com/trade-api/v2/markets/KXMLBHR-26AUG021920BOSLAD-BOSCDURBIN17-1), [terms](https://assets.kalshi.com/contract_terms/BASEBALLENTITYSTAT.pdf) |
| KXBTCD | [Sample](https://external-api.kalshi.com/trade-api/v2/markets/KXBTCD-26AUG0117-T63749.99), [terms](https://assets.kalshi.com/contract_terms/BTC.pdf) |
| KXTRUMPSAY | [Sample](https://external-api.kalshi.com/trade-api/v2/markets/KXTRUMPSAY-26AUG03-AMER), [terms](https://assets.kalshi.com/contract_terms/TRUMPSAY.pdf) |

Drafts also reference official series/event payload fingerprints, page sections and
review questions. Current sources support proposed interpretations, not fill-time
term versions. No experimental standing or P&L is restated.

## Release and next work

Merge after CI, deploy market-catalog only with existing variables/volume. Verify
authenticated pilot packets, unchanged approved fields and document blocker. No
manual database setup. Rollback to #564 with the same volume; no schema change.
Authenticated production packets and browser rendering remain release checks.

Before study approval: version/archive authoritative bytes and establish applicable
historical terms; obtain current authenticated parent-bound hashes; resolve exposure;
use the normal actor/rationale review flow for intended bindings. Then follow the
[calibration sequence](MARKET_CATALOG_SCORING_READINESS.md). Consumer cutover remains
its separately authorized Platform Change Review.
