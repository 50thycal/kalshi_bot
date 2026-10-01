# Operator runbook — desks round 1 → round 2 (DEC-024)

Moves both autonomous desks from `desks-round-1` to a fresh `desks-round-2` under the v2
research rules ([RESEARCH_V2.md](RESEARCH_V2.md)). Steps marked **HARD STOP** are the
owner's to take; no desk session or agent performs them. The desk tokens can never start a
round, pause, resume, fund or deploy.

## Facts the plan rests on (from the code, not assumed)

- **One round per database.** `DeskStore.initialize` refuses a second round ID in a database
  that already holds one (`round_already_initialized`). Round 2 therefore runs on a **new
  desk database**; the round-1 database is kept untouched as the permanent, append-only record.
- **A fresh round's books start at $30.** `initialize` creates each book with
  `initial_cents=3000`. Before a round starts, `preflight` requires each subaccount's exchange
  balance to be **≥ $30** and the book to be **clean** (no resting orders, no positions).
- **Readiness is per round.** The new books start `ready=false`; `session_cycle_required`
  holds until each desk completes a genuine session cycle in the new round.
- The v2 tools (market browse, open-web capture, 50 captures/job, 60-minute lease) are code
  defaults; they apply to round 1 as soon as v2 is deployed. The round boundary changes the
  book, not the tools.

## Step 0 — Merge and deploy v2   **HARD STOP (deploy)**

1. Owner merges the v2 PR (owner-accepted, not reviewed).
2. Owner deploys that revision to the **desk-service only** (not main/evo workers). No
   environment change is needed: `DESKS_MAX_SOURCES_PER_JOB` (default 50) and
   `DESKS_RESEARCH_LEASE_MINUTES` (default 60) are optional overrides.
3. Verify from a desk session (desk token, read-only):
   `python scripts/desk_client.py markets --sort volume --limit 5` returns rows with
   `coverage.complete=true`; `market --ticker <one>` returns `rules_primary` and `rules_sha256`.
   The first capture of a non-Kalshi page in the next Continue proves open-web capture.
   A failure here is a deployment defect: stop and report; do not work around it.

## Step 1 — Final round-1 Continue per desk

Each desk runs one ordinary Continue under the round-1 book (Claude: `/claude-desk-continue`;
ChatGPT: [CHATGPT_CONTINUE.md](CHATGPT_CONTINUE.md)). The owner may designate one of the
already-scheduled Claude reminders (2026-10-01 or 2026-10-02 14:37Z) as Claude's final one.
Tell the session it is the **final round-1 cycle**. Its completion is the closing handoff:

- `summary`: what round 1 found and what did not work;
- `candidates`/`rejected`: the strongest open leads and the worthwhile rejections;
- `lessons`: the durable process lessons (≤10);
- `postmortems`: every settled, unreviewed decision;
- `decisions`: **none** — a new position would delay the switch (Step 2);
- `next_action`: `"round 1 closed; carry these lessons into round 2"` plus the best lead.

The service keeps these publications permanently in the round-1 database.

## Step 2 — Positions settle; nothing pending

Hold to settlement; never close early. Before the switch, authenticated status for **both**
desks must show: `open_positions = 0`, no pending/unknown orders, `unreviewed_settlements = 0`.
As of 2026-10-01 the Claude desk held 7 YES `KXHIGHNY-26SEP30-T72` (cost $0.8918, expected
loss, settling ~11:00Z 2026-10-01); it needs its settlement and postmortem (a Continue can
publish the postmortem). ChatGPT had no fills as of 2026-09-30.

## Step 3 — Export the closing handoffs into the repository

Run with any authenticated token (status is read-only):

```sh
umask 077
python scripts/desk_client.py --desk claude  handoff-export --round-label desks-round-1 --out docs/desks/handoffs/claude-round-1.md
python scripts/desk_client.py --desk chatgpt handoff-export --round-label desks-round-1 --out docs/desks/handoffs/chatgpt-round-1.md
```

The export contains research text, tickers, decision outcomes and research P&L — no balances,
tokens or account identifiers. Read both files before committing; remove anything private.
Commit them in a small docs PR and merge it before Step 6. Every Markdown file in
`docs/desks/handoffs/` is placed in each claim's `archive` as `prior_round_handoff`.

## Step 4 — Funding decision   **HARD STOP (funding)**

Each round-2 book starts at $30 and preflight requires a ≥ $30, clean subaccount. Options:

- **A. Fresh $30 per desk (fits the current code; recommended).** Top up any subaccount below
  $30 back to $30 (Claude ≈ $0.89 after the T72 loss, subject to the actual settlement).
  A subaccount above $30 still passes; its book is still $30.
- **B. Carry each desk's current balance.** Not supported: books are hard-coded at $30, and a
  balance below $30 fails preflight. It needs a separate, owner-approved code change to make
  the initial bankroll a configured value. Not part of this runbook.

## Step 5 — Switch the database and round ID   **HARD STOP (deploy/config)**

1. Provision a **new, empty** Postgres database for the desk service. Keep the round-1
   database; do not drop, truncate or reuse it.
2. On the desk-service only, set `DESKS_DATABASE_URL` to the new database and
   `DESKS_ROUND_ID=desks-round-2`. Change nothing else — not `DESKS_LIVE_ENABLED`, tokens,
   keys, subaccounts or alert settings.
3. Redeploy. Startup initializes `desks-round-2` with two unready $30 books. Status shows
   `round_id=desks-round-2`, `started_at=null`.

Rollback before Step 7: point `DESKS_DATABASE_URL`/`DESKS_ROUND_ID` back to round 1.

## Step 6 — Each desk re-declares ready under v2

In a fresh session per desk, using the updated startup packet
([CLAUDE_START.md](CLAUDE_START.md) / [CHATGPT_START.md](CHATGPT_START.md)):

1. Read own `docs/desks/handoffs/<desk>-round-1.md` (and the peer's) before researching.
2. Run **one genuine v2 research cycle with no decisions**: browse the board, capture at least
   one open-web source, complete, and read back the publication.
3. `python scripts/desk_client.py --desk <desk> ready`.

## Step 7 — Start round 2   **HARD STOP (round start)**

```sh
PYTHONPATH=. python scripts/desk_operator.py preflight   # exits 2 with blockers until ready
PYTHONPATH=. python scripts/desk_operator.py start
```

Preflight refreshes isolation/funding/clean-book checks; `start` gives both books the same
timestamp. After start, Continue cycles run exactly as in round 1.
