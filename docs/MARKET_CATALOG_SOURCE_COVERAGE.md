# Market catalog source coverage — 2026-10-05

WS-023 migration baseline, not experiment standings or a gate verdict. Queries were SELECT-only through the existing transaction-enforced read-only ops transport. These are point-in-time source observations, not permanent expected totals.

## Import scope measured at 21:35 UTC

| Observation | Paper | Live fills |
|---|---:|---:|
| MMSELL source rows | 126,380 | 2,741 |
| Closed paper rows (`settled` / `closed_sl`) | 124,873 | — |
| Distinct series in source rows | 746 | 249 |
| Minimum source ID | 6,432 | 89 |
| Maximum source ID | 140,886 | 4,331 |
| First recorded execution | 2026-07-03 17:10:41 UTC | 2026-07-13 13:52:41 UTC |
| Last recorded execution | 2026-10-05 21:15:06 UTC | 2026-10-05 21:16:07 UTC |

Paper scope is every `paper_trades` row with strategy ILIKE `%mmsell%`, including twins and still-open trades. Live scope is every matching fill joined to the lowest-ID `live_orders` record for the exchange order, exactly as the importer does. Evaluator eligibility is stricter: closed non-twin paper and actual buy fills. These totals do not certify outcome independence, reviewed semantics, live P&L attribution or qualification. Observed trade dates are not settlement dates.

At audit time the catalog contained only 200 paper and 200 live rows from the initial export seed. That is explicitly partial history; it must not be represented as a complete source migration.

Evidence: [source audit](https://github.com/50thycal/kalshi_bot/blob/ops/ops/results/catalog-source-audit-20261005-2135.txt). Ops results are routinely pruned; this document preserves the conclusions.

## Credential diagnosis

The catalog connection is refused for all five elevated-role flags and public-table write privileges. Separately, the GitHub secret named `DATABASE_URL_RO` logs in as `postgres` and is elevated. Its transport enforces read-only statements/transactions, but its underlying credential is not SELECT-only. Copying that value into the catalog is not an acceptable fix. No source write, password read, credential export or safeguard relaxation was performed.

The existing `bot_readonly` role was verified at 21:39–21:43 UTC:

- No elevated flags, no inherited role memberships and no direct non-SELECT table grants.
- Login enabled; public schema usage and SELECT access to paper trades, fills, live orders and paper twins all present.
- The source PostgreSQL supports the ordered SHA-256 ID aggregation used by the catalog coverage check.

Evidence: [role and ID audit](https://github.com/50thycal/kalshi_bot/blob/ops/ops/results/catalog-role-id-audit-20261005-2138.txt), [required access audit](https://github.com/50thycal/kalshi_bot/blob/ops/ops/results/catalog-read-access-20261005-2142.txt).

## Historical external unblocker — resolved

Calvin/database administrator supplies the actual URL for `bot_readonly`, with that role's own password, as `CATALOG_SOURCE_DATABASE_URL` on `market-catalog`, then redeploys only that service. No read-only URL was found in Railway variables that the agent could reference, and the actual role password is unavailable to the agent. Do not copy the current `DATABASE_URL_RO` value unchanged. Correcting that GitHub secret to the same properly scoped connection is also an operator credential action; no ops workflow change is required.

## Completion check

The catalog audits the source and local ID sets through its committed source cursor. Both row count and sorted-ID SHA-256 must match before a pass is marked complete. Audit, local comparison and completion cursor publish atomically; interrupted commits leave the prior checkpoint. New/daily reconciliation has a separate completion flag. Missing, substituted or extra IDs fail even when counts coincide. Original history remains retained.

Source-ID equality is a completeness check, not full payload parity. Daily replay repairs changed rows. Scientific confidence remains uncalibrated and unqualified until its independent review, integrity, attribution and forward-validation requirements are satisfied.


## Credential resolution and latest observation — 2026-10-08

The owner corrected CATALOG_SOURCE_DATABASE_URL to the actual bot_readonly values and
redeployed. The source permission guard accepted the role and continuous collection resumed.
Runtime observations: 130,492 paper and 2,823 live rows; live ID equality through source ID
4,416 at approximately 02:47 UTC; paper equality through 145,015 was previously recorded
on 2026-10-07 19:09 UTC. Daily replay continues. Payload parity remains unverified, and
neither ID equality nor successful collection establishes strategy confidence. The volume
is 20 GB (observed database ~8.56 GB, free ~9.84 GB at that check). These are timestamped
observations, not permanently current totals. See MARKET_CATALOG_EVIDENCE.md for the next
review/attribution/scoring release; no credential change is needed for that release.
