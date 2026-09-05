---
name: live-paper-parallel
description: Set up and audit a live real-money strategy with a fresh paper TWIN running in parallel, so paper-vs-live alignment is measured instead of assumed. Use when taking any strategy live (e.g. "set up mmsell10 live", "go live with X", "arm the live test"), when adding a parallel/shadow paper run beside a live book, or when checking whether a live book's paper edge is real ("run the parity check", "is our paper trading aligned with live", "is this edge a mirage").
---

# live/paper parallel run — arm a live strategy with its paper twin, then audit the two

**Standing policy: no strategy goes live without a paper twin.** A live book without one is a book
whose paper edge can never be audited, and paper is what every promotion decision in this repo is
gated on.

The mechanism, its design rationale, and the interpretation traps are in **`docs/LIVE_PAPER_TWIN.md`**
— read it before interpreting any number here; do not re-derive it in chat.

## What the twin is (one paragraph)

For live tag `X`, a fresh paper book `X_pt` starts the same instant, sees the same candidates, and
uses the **live** parameters (maker price rule, dollar-cap sizing, live open cap, live spread gate).
It never places a real order and it stands down whenever live does. The only remaining difference:
**the twin assumes its resting order fills; live has to actually get filled.** So every twin-vs-live
divergence is execution reality, and any divergence *on markets both sides settled* is our own
accounting being wrong — a mirage.

---

## Phase A — arming a new live strategy

1. **Confirm the book is wired.** mmsell and theta are both wired (`kalshi_bot/mmsell/tracker.py`
   and `kalshi_bot/theta/tracker.py` are the reference implementations — theta's is the one to copy
   when the new book shares the maker-sell convention but needs its OWN live sizing knobs rather
   than reusing another book's, per `docs/THETA_LIVE_PLAN.md` §3). For any other book family, wire
   the three hook points in `docs/LIVE_PAPER_TWIN.md` §5 first — do not arm live without them, or
   there will be no twin.
2. **Pre-register the test** before flipping anything: bankroll, per-order cap, open cap, duration,
   and the kill criteria. Write it into the book's plan doc (`docs/MMSELL_LIVE_PLAN.md` /
   `docs/THETA_LIVE_PLAN.md` are the templates). This is what stops the test being quietly
   re-scoped after the fact.
3. **Set the live config through the ops channel** (see `CLAUDE.md` for the `ops` branch mechanics).
   Twins are auto-derived from `LIVE_STRATEGIES`, so there is normally nothing twin-specific to set:
   ```jsonc
   {"type": "env", "set": {"MMSELL_LIVE_MAX_OPEN_POSITIONS": "60",
                           "MMSELL_LIVE_PRICE_OFFSET_CENTS": "0",
                           "LIVE_MAX_ORDER_DOLLARS": "1.0",
                           "MAX_ORDER_SIZE": "1"}, "id": "live-cfg-1"}
   ```
   Then arm, as a **separate, deliberate step** (this is the one that risks money):
   ```jsonc
   {"type": "env", "set": {"BOT_MODE": "live", "LIVE_STRATEGIES": "mmsell10",
                           "LIVE_ENABLED": "true", "KILL_SWITCH": "false"}, "id": "live-arm-1"}
   ```
   Ask the operator to confirm before sending the arming request. Never widen `LIVE_STRATEGIES`
   beyond the one book being tested.
3b. **Name the GLOBAL knobs before you set any of them.** Several mmsell risk settings are
   process-wide, not per-book — they read off `Settings` in the shared
   `kalshi_bot/mmsell/tracker.py`, so setting one "for this book" re-scopes every other book in
   the worker, including grandfathered ones mid-experiment. `MMSELL_CONTEST_CAP_ENABLED`
   (XOS-000020) is the newest and least obvious; `MMSELL_SETTLEMENT_CAP_ENABLED`,
   `MMSELL_SETTLEMENT_CORRELATED_REGIMES` and `MMSELL_EVENT_RUNG_CAP*` are the same shape.
   `LIVE_PAPER_TWIN_SUFFIX` is worse than the rest: it is a single global suffix, so changing it
   orphans every OTHER live book's twin tag, which then resolves to no deployment arm and goes
   dark under `NEW_ONLY` (the XOS-000011 shape). Tell the operator which of the vars you are
   about to set are global and which books they will touch. Wanting one globally for one book is
   a shared-semantic change → Platform Change Review, or a request for a per-book variant key.

4. **Verify the twin exists within one cycle.** Two checks — logs and DB:
   ```jsonc
   {"type": "logs", "limit": 120, "filter": "twin", "id": "twin-logs-1"}
   {"type": "db", "sql": "select twin_tag, live_tag, started_at, params_json from live_paper_twins order by started_at desc", "id": "twin-epoch-1"}
   ```
   Expect a `live/paper twins configured` line with `armed: [mmsell10_pt]` and one epoch row. **If
   there is no epoch row, the twin is not running** — the live tag isn't actually armed, or the book
   isn't wired. Fix that before letting the live test accumulate trades, because an epoch that
   starts late can never be repaired (both sides must share the window).
5. **Report to the operator**: what is armed, the twin tag, the epoch start, and when the first
   meaningful parity read will be possible (n≥30 settled per side).

## Phase B — the recurring parity audit

Run the read:
```jsonc
{"type": "script", "name": "live_paper_parity", "id": "parity-1"}
// one pair / trailing window:
{"type": "script", "name": "live_paper_parity", "args": ["--twin", "mmsell10_pt", "--days", "7"], "id": "parity-2"}
```

Present it in this order — **lead with ANOMALIES**, then the verdict, then the tables:

1. **ANOMALIES** — flag anything; otherwise say "all clear" explicitly. `PARAM DRIFT`, a reused twin
   tag, live orders predating the epoch, and any live book with **no twin at all** are all
   findings, not noise.
2. **VERDICT** per twin, with the number behind it:

   | verdict | meaning | action |
   |---|---|---|
   | TOO EARLY | either side under n=30 settled | keep running; the epoch *is* the sample |
   | ALIGNED | within 0.5¢/contract | paper is a fair model of this book; its gates can be trusted |
   | EXECUTION GAP | matched markets agree, books diverge | paper arithmetic is right, live can't capture the trades — read the gate table and fill% |
   | ACCOUNTING GAP | **matched markets disagree** | the simulator is wrong. Escalate: this invalidates paper gates on **every** book, not just this one |

3. **Decision alignment** — twin-opened vs live-placed overlap, and the gate breakdown for the gap.
4. **Execution realism** — fill%, and `px_gap` (real cost basis − assumed).
5. **The mirage read** — the settled table, including the incumbent paper book's all-time row, i.e.
   what the naive comparison would have claimed.

### Rules for interpreting it

- **Matched markets is the load-bearing statistic.** Same ticker, same side, same window, both
  settled. A gap there cannot be fill rate or adverse selection — we got the trade. It can only be
  entry price, fee model, or settlement logic. Say so plainly when it appears.
- **A gate-dominated decision gap is CAPACITY, not edge.** "Live traded 60 of its twin's 200
  candidates, 140 blocked by `gate:open_cap`" says nothing about whether the edge is real. Never
  present it as evidence either way.
- **Fill% below 100 is expected**, not a finding. The twin exists to price what that costs.
- **`px_gap` ≠ adverse selection.** A systematic price gap means every paper book is optimistic by
  that amount — a much broader finding than one book's fill rate.
- **Thin-n percentile and win-rate stats swing.** Don't narrate a single-check flip on n<30 as a
  change in the underlying reality; say it's thin and confirm on the next check.
- **One confirming check.** A verdict flip is provisional until it holds on a second, larger-n read
  — the same discipline the mmsell exit-study checks use.

## Phase C — changes, retunes, teardown

- **Retuning a live knob mid-epoch voids the comparison.** The harness logs param drift and the
  report surfaces it. The fix is a **new twin tag** (`LIVE_PAPER_TWINS=mmsell10:mmsell10_pt2`), not
  a re-read of the old one. Say this out loud when proposing a config change to a live book.
- **Pausing live pauses the twin** automatically (kill switch or removing the tag). That preserves
  the one-to-one property; no twin-side action needed.
- **Ending a test:** disarm live first (`KILL_SWITCH=true` or drop the tag from `LIVE_STRATEGIES`),
  and record the final parity verdict in the book's plan doc plus `docs/BOOK_REGISTRY.md`. A twin
  tag is single-use; never reuse it for a later test.
- **Promoting the result:** an ALIGNED verdict is what licenses trusting the *other* paper books'
  gates. An ACCOUNTING GAP is the highest-priority finding this repo can produce — it means the
  paper system that every promotion decision rests on is broken, and that outranks the live test
  that found it.

## Reporting

Keep it to the tables plus the verdict and one headline sentence. The bottom line is always
dollars against the +$100/month north star: state whether this live book is contributing, and
whether its paper twin says the book is worth scaling, capping, or killing.
