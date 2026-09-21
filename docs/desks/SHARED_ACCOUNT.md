# Shared primary account with separate desk ledgers — DEC-021

Operator-selected alternative to creating/funding subaccounts. One existing Kalshi
primary account holds the cash. ChatGPT and Claude retain separate $30 virtual books,
$10 committed-risk limits, $1 picks including fees, daily limits, source evidence,
order identifiers, fees and settlement records. No money is transferred by this mode.
The existing isolated-subaccount mode remains available and is the configuration default.

## How separation works

All participating workers use one `kalshi_market_ownership` table in the same database
and namespace. An atomic unique claim assigns a ticker to main, ChatGPT or Claude before
submission. Claims never expire or auto-release, including after rejected/unknown orders
or settlement. A later round cannot silently reuse a claimed market for another desk.
This conservative initial implementation may exclude markets that never actually filled.

A desk refuses markets with existing order history or positions. Main claims a market
before V2 and legacy V1 placements; the V1 caller supplies the resolved ticker without
changing the exchange payload. Main filters desk-owned market rows from portfolio reads
and refuses their cancellation. It omits exchange event-position aggregates in shared
mode because those cannot be attributed reliably; market positions remain authoritative.
An order read lacking market identity fails closed. Pagination cursors are preserved.

Desk fills are attributed by exact client/exchange order IDs. Audits compare owned
market orders, positions and terminal fill costs/fees against the desk ledger. Unknown
orders keep reservations and block fresh submissions; unexplained activity pauses the
affected desk. A periodic launch recheck also revokes execution readiness. Reconciliation
continues while paused. No discrepancy automatically rewrites a ledger or creates a
balancing trade. A normal ownership conflict simply rejects that candidate.

Both virtual cash liabilities must fit the primary account's available exchange cash.
Main/manual spending can reduce that backing; the desks then stop new submissions.
This is software accounting, not protected funds or exchange-enforced key isolation.
Manual orders and external key consumers do not honor the register and must avoid
claimed markets. Audits detect discrepancies but cannot prevent such outside actions.

## Configuration and deployment sequence

No production configuration, credentials or live flags are changed by this PR.

1. Complete the shared-execution Platform Revision/impact review. Deploy the cooperating
   worker code before allowing any desk order. Keep desk live execution disabled.
2. Choose one existing persistent Postgres database for the ownership table. Give all
   participating workers the same private URL and namespace. This table is separate from
   XOS and from the desk's immutable financial ledger; never delete it to clear a refusal.
3. Main/evo: set `SHARED_ACCOUNT_OWNERSHIP_URL` privately and
   `SHARED_ACCOUNT_NAMESPACE=kalshi-primary`. Existing exchange keys stay unchanged.
4. Desk service: set `DESKS_ACCOUNT_MODE=shared_primary`, both
   `DESKS_CHATGPT_SUBACCOUNT=0` and `DESKS_CLAUDE_SUBACCOUNT=0`,
   `DESKS_SHARED_OWNERSHIP_URL` to that same database and
   `DESKS_SHARED_ACCOUNT_NAMESPACE=kalshi-primary`.
5. Privately provision the existing account's signing credentials as
   `DESKS_CHATGPT_KALSHI_KEY_ID` / `DESKS_CHATGPT_KALSHI_PRIVATE_KEY` and
   `DESKS_CLAUDE_KALSHI_KEY_ID` / `DESKS_CLAUDE_KALSHI_PRIVATE_KEY`.
   These may be the same key in shared mode. Research apps receive only their own
   desk bearer token, never the exchange key or ownership database credential.
6. Verify every active account writer uses this ownership register, including legacy V1,
   and that none can adopt/cancel desk positions. Drain any pre-upgrade in-flight writes
   before the common start. Verify deployed URL/namespace equality privately; a config
   flag alone does not prove it. Only then attest `DESKS_EXISTING_WORKERS_ISOLATED=true`.
   Here the flag means verified cooperating-worker ownership, not restricted keys.
7. Verify both app sessions, existing account cash backing (initially at least $60),
   the selected alert mode and the operator preflight. DEC-022 permits session-only
   warnings instead of an external webhook; see APP_SESSIONS.md. Live enable and the single common start remain
   separate operator actions. Neither Go/Continue nor a merge activates them.

No new Kalshi subaccount, fresh deposit or key revocation is required if the existing
account already has sufficient available cash and valid signing credentials.

## Validation scope

Offline tests cover concurrent ownership claims, restart persistence, incumbent activity
rejection, primary cash backing, foreign/manual activity detection, legacy/V2 write and
cancel refusal, filtered reads, and real desk ledger attribution through simulated Kalshi
order/fill responses. Deployment and multi-process Postgres verification are separate
launch gates. Exchange API references: [positions](https://docs.kalshi.com/api-reference/portfolio/get-positions)
and [orders](https://docs.kalshi.com/api-reference/orders/get-orders).
