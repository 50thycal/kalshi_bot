# Operator runbook — desks round 1 → round 2 (DEC-024)

Moves both autonomous desks from `desks-round-1` to `desks-round-2` under the v2 research
rules ([RESEARCH_V2.md](RESEARCH_V2.md)) **in the same desk database**, with each desk's
balance **carried over** (owner decision, 2026-10-01). Steps marked **HARD STOP** are the
owner's; no desk session or agent performs them. Desk tokens can never start a round, pause,
resume, fund or deploy.

## How a new round works (from the code)

- **Same database, one setting.** On boot the service selects the round named by
  `DESKS_ROUND_ID`. If that round does not exist yet, it is created next to the earlier
  rounds — **only if every earlier round is flat**: no reserved, submitting, pending or unknown
  order and no filled-but-unsettled position. Otherwise boot fails with
  `prior_round_not_closed` and nothing is created; set `DESKS_ROUND_ID` back to finish round 1.
- **Carried balance.** `DESKS_NEW_ROUND_BANKROLL=carry` (the default) starts each new book at
  that desk's round-1 book cash, **capped at $30** (winnings above $30 stay in the subaccount but
  are not added to the bankroll: no size increase, DEC-018). `fresh` would start both at $30.
- **Preflight checks the book's own bankroll.** Before start, each subaccount must hold at least
  the book's initial bankroll (the carried amount) and be clean (no positions or resting orders).
- **Memory carries over.** Round-1 publications stay in the database (append-only). Every
  claim's context includes `prior_round_record`: the desk's own round-1 handoff, lessons, research
  cycles and postmortems, plus a few of the peer's. Status lists `prior_rounds`.
- **Readiness and pauses.** New books start unready (`session_cycle_required` until a v2 session
  cycle completes). An operator pause on a round-1 book carries into round 2 and needs an
  operator resume.

## Step 0 — Merge and deploy v2   **HARD STOP (deploy)**

1. Owner merges the PR (owner-accepted, not reviewed) and deploys it to the **desk-service
   only**. No new environment variable is required.
2. Verify from a desk session (desk token, read-only):
   `python scripts/desk_client.py markets --sort volume --limit 5` returns rows with
   `coverage.complete=true`, and `market --ticker <one>` returns `rules_primary` and
   `rules_sha256`. The first open-web capture in the next Continue proves capture. A failure is
   a deployment defect: stop and report.

## Step 1 — Final round-1 Continue per desk

Each desk runs one ordinary Continue in round 1 (Claude: `/claude-desk-continue`; ChatGPT:
[CHATGPT_CONTINUE.md](CHATGPT_CONTINUE.md)). The owner may designate one of the scheduled
Claude reminders (2026-10-01 or 2026-10-02 14:37Z) as Claude's. Tell the session it is the
**final round-1 cycle**. Its completion is the closing handoff:

- `summary`: what round 1 found and what did not work;
- `candidates`/`rejected`: strongest open leads and worthwhile rejections;
- `lessons`: durable process lessons (≤10);
- `postmortems`: every settled, unreviewed decision;
- `decisions`: **none** — a new position would block the switch;
- `next_action`: `"round 1 closed; carry these lessons into round 2"` plus the best lead.

## Step 2 — Positions settle; nothing pending

Hold to settlement; never close early. Authenticated status for **both** desks must show
`open_positions = 0`, no pending/unknown orders and `unreviewed_settlements = 0`. As of
2026-10-01 the Claude desk held 7 YES `KXHIGHNY-26SEP30-T72` (cost $0.8918, expected loss,
settling ~11:00Z 2026-10-01); it needs its settlement and postmortem. ChatGPT had no fills as of
2026-09-30. (The service also enforces flatness in Step 3.)

## Step 3 — Switch the round   **HARD STOP (config/deploy)**

On the desk-service only, set `DESKS_ROUND_ID=desks-round-2` and redeploy. Change nothing else —
not `DESKS_DATABASE_URL`, `DESKS_LIVE_ENABLED`, tokens, keys, subaccounts or alert settings.
Status then shows `round_id=desks-round-2`, `started_at=null`, `prior_rounds=["desks-round-1"]`
and each book's carried `initial_bankroll`. If boot reports `prior_round_not_closed`, set
`DESKS_ROUND_ID` back to `desks-round-1`, let it settle, and retry.

Rollback before Step 5: set `DESKS_ROUND_ID=desks-round-1` (round 2's empty books stay inert).

## Step 4 — Each desk re-declares ready under v2

In a fresh session per desk, using the updated startup packet ([CLAUDE_START.md](CLAUDE_START.md)
/ [CHATGPT_START.md](CHATGPT_START.md)):

1. Read `prior_round_record` in the claim context (own round-1 handoff and lessons) first.
2. Run **one genuine v2 research cycle with no decisions**: browse the board, capture at least
   one open-web source, complete, read back the publication.
3. `python scripts/desk_client.py --desk <desk> ready`.

Optional: `python scripts/desk_client.py --desk <desk> handoff-export --round-label desks-round-1`
renders a readable Markdown report of a desk's record (no balances) for the owner.

## Step 5 — Start round 2   **HARD STOP (round start)**

```sh
PYTHONPATH=. python scripts/desk_operator.py preflight   # exits 2 with blockers until ready
PYTHONPATH=. python scripts/desk_operator.py start
```

Preflight refreshes isolation, carried-balance funding and clean-book checks; `start` gives both
books the same timestamp. After start, Continue cycles run as in round 1.
