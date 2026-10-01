# WS-022 — Desk research v2: open discovery, open evidence, fresh round

**Phase:** REVIEW
**Status:** Active
**Created:** 2026-10-01
**Updated:** 2026-10-01
**Build OS:** v0.12

## Goal

Let both autonomous desks (DEC-018) browse the whole Kalshi board and capture any public web
source as evidence, lift usage-rationing limits, and prepare an operator runbook that moves both
desks to a fresh round under those rules — without touching any financial control or safeguard.

## Context

Calvin's 2026-10-01 handoff (recorded as DEC-024). The desks saw a fixed 20-market rotating
sample (≈1,400 markets cached after four days, zero completed passes, the same 20 for both desks)
and could capture only 8 sources per job from a 27-host allowlist, with excerpts cut at 12,000
characters (Kalshi ladders lost buckets mid-JSON). Admitted on an explicit owner request; the
board is already over its declared limit and no other row is paused by inference.

## Current Mental Model

```text
desk session ──GET /api/markets|events|series|categories──▶ MarketBrowser (5-min whole-board index)
             ──GET /api/markets/{t}[/orderbook|/trades], /api/events/{e}, /api/series/{s}──▶ live Kalshi public API
             ──POST /api/desks/{d}/source {url}──▶ PublicFetcher: check_url → per-hop DNS → global-only
                                                   → pinned connect (SNI=host) → ≤5 redirects → text ≤1M chars
                                                   → ResearchSource(url, retrieved_at, sha256, excerpt)
completion/decision ──▶ verify_decision_sources (unchanged matching; SQL pre-filter by cited URL)
                    ──▶ executor (unchanged: 60 s quote, rules hash, bound check, isolation, caps)
round N DB (append-only) ──handoff-export──▶ docs/desks/handoffs/<desk>-round-N.md ──▶ round N+1 claim archive
```

## Decisions Made

- DEC-024 records the owner scope and the implementation choices (browse ≠ evidence; app-session
  web search, no paid search API; SSRF boundary; PDFs refused; one round per database).
- Lease 60 minutes, 50 captures per job, both configurable (`DESKS_RESEARCH_LEASE_MINUTES`,
  `DESKS_MAX_SOURCES_PER_JOB`).

## Open Decisions

- **D1 (owner, hard stop).** Round-2 funding: fresh $30 per desk (top-up; fits the code) or carry
  balances (needs a separate code change). Recommendation: fresh $30. Runbook step 4.
- **D2 (owner, hard stop).** Deploy v2 to the desk-service, then schedule the final round-1
  Continue per desk, the database switch and the round-2 start. Runbook steps 0, 5, 7.

## Assumptions

- Kalshi's public `/events?with_nested_markets=true` scan stays readable without credentials from
  Railway; the browse index depends on it. Not verifiable from the Claude sandbox (egress denied);
  runbook step 0 verifies it live.
- Railway egress connects directly (no proxy), so the pinned-address fetch reaches public hosts.

## Non-Goals

- Any change to bankroll, per-pick size, daily caps, committed-risk cap, IOC/hold rules, quote
  freshness, rules-hash, bound check, isolation, unknown-order pause, tokens or live flag.
- Performing the round switch, funding, deployment or start.
- In-process PDF parsing; a paid search API.

## Acceptance Checks

1. Desk can list by volume, newest, closing soon; filter by category/series/search; read rules,
   order book and trades; no 20-market cap, no mid-JSON truncation; pagination/sort tests.
2. Arbitrary public HTTPS capture works; private/metadata IPs and redirect-to-private refused
   (tests); provenance accepts a decision citing the page and rejects a tampered excerpt (tests).
3. 8-capture cap replaced by a configurable 50; lease decided (60 min) and documented.
4. Desk docs, packets and continue procedures describe v2; DEC-024 recorded; this row updated.
5. `pytest tests/test_desks_*` passes; lint clean.
6. Cutover steps written as an operator runbook with hard stops; the switch waits for the owner.

## Build Card

Inline: owner handoff 2026-10-01 (design requirements A–C, cutover plan, acceptance checks above).

## Implementation State

Built on `claude/intelligent-einstein-99enpn`: `kalshi_bot/desks/web.py`, `kalshi_bot/desks/markets.py`,
server/client/supervisor/config wiring, tests `tests/test_desks_web.py`, `tests/test_desks_markets.py`,
docs `docs/desks/RESEARCH_V2.md`, `ROUND_2_CUTOVER.md`, `CHATGPT_CONTINUE.md`.

## Review State

Not started. Solo mode: owner-accepted at merge, never called reviewed.

## Related Decisions

DEC-018, DEC-019, DEC-022, DEC-023, DEC-024.

## Related PRs

The v2 PR on `claude/intelligent-einstein-99enpn`.

## Parked

- PDF evidence capture via an isolated, time-limited parser (refused today to protect the
  real-money service process).
- Configurable initial bankroll so a round can carry a desk's balance (only if D1 chooses carry).

## Next Step

Owner merges and deploys v2 to the desk-service, then runs `docs/desks/ROUND_2_CUTOVER.md` step 0.
