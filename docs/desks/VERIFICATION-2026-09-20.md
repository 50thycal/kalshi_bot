# Desk launch verification — 2026-09-20

Deployment evidence, not launch authorization. The desk backend and workers run on
Railway; a Vercel frontend deployment alone does not verify these processes.

## Verified

- PR #445 was merged before setup. PR #449 is now merged at
  `613c99640a9b56c8efb1a1b1bd25e968a40b7ecd`; its final-head CI passed.
- Railway main and desk-service successfully deployed that merge. The main process
  reported the same commit. Authenticated balance, order, fill, position and settlement
  reads succeeded with explicit primary subaccount `0`. No test orders were placed.
- Main has fractional/V1 execution configured. This read-only check does not prove
  V1 order routing. No resting orders were available for a scoped queue-position check.
- ChatGPT app authenticated through the Railway console bridge using the existing
  own-desk token privately. Schema, claim, completion, publication readback and own
  readiness succeeded. The completed session job is
  `research-desks-round-1-chatgpt-497197`, published at 16:35:55Z.
- The preparatory cycle published two lessons and a no-decision research summary.
  Service reports one completed ChatGPT session, zero pending ChatGPT jobs and zero
  recorded model cost. This did not test a hosted model login or Claude entitlement.
- Common start remains unset. ChatGPT research readiness is not trading readiness.

## Discovery defect and bounded repair

Both captured market pages contained combos; local post-fetch filtering left the board
empty. An empty bounded scan does not prove that no eligible markets exist.
The documented `mve_filter=exclude` query returned HTTP 200, 50 binary markets and
zero combo rows in a read-only production request. See the
[exchange market endpoint](https://docs.kalshi.com/api-reference/market/get-markets).

The repair adds that filter while retaining local combo checks and the two-page budget.
An old unfiltered cursor/same-hour snapshot must be reset because cursors belong to their
query. Only the round's discovery cache and coverage counters are reset; publications,
source evidence, research jobs and financial records are retained. Both desks still
share the hourly snapshot. This code is proposed separately; the live query above is
not a claim that the new scanner has deployed.

## Remaining launch gates

- Two distinct restricted, funded desk subaccounts and their dedicated credentials.
  Only primary account `0` existed at the last authenticated setup check. The temporary
  setup credentials remain separate from desk-service; its setup process is stopped.
- Complete existing-worker isolation evidence, including legacy V1 and the unknown
  external consumers of existing unrestricted keys. Preserve both existing keys.
  PR #449's shared Platform Revision/impact requirement remains unresolved; a merge
  is not a measured cutover or retroactive platform acceptance.
- Claude must complete its own app-session research and readiness. ChatGPT must not
  impersonate Claude or set its readiness flag.
- Configure an operator alert destination and verify delivery.
- After those gates, separately verify live preflight and coordinate the single
  common start. No accounts, transfers or trading actions were performed in this check.

The app-session bridge works for this ChatGPT session through authenticated Railway
access. It does not establish that an arbitrary future app session has the same access.
Hosted runners remain deferred and no additional paid model allowance is inferred.
