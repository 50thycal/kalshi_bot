---
name: mm_check_1
description: Run one mmsell check — pull the standing per-book realizable P&L read (mmsell fill model) and the exit-rule study (mmsell exit study) via the ops channel, diff both against the last check, and post a before/after report with the best exit result per book. Use when the user asks "mm check 1", "how are the mmsell strategies looking", "how's mmsell doing now", or wants a fresh mmsell + exit-study status pass.
---

# mm check 1 — mmsell standing read + exit-study check, with before/after

One-shot status pass over all mmsell books (`mmsell`, `mmsell1`–`mmsell11`, and the
`mmsellA1`–`mmsellA5` anchor set): the
live-calibrated realizable P&L read (no exit) and the exit-rule study (with exit),
diffed against the last time this skill ran. Read-only; touches no trading config.

## Background — what the two reads mean (don't re-derive this each run)

- **`mmsell fill model`** — projects each book's own entry-price mix through the live
  mmsell3 (price → fill, realizable P&L) calibration. `real_$/ct` is the number to
  gate on, not blended paper — paper overstates edge by the maker adverse-selection
  gap (a resting order only fills when someone takes the other side; paper counts the
  quiet winners a maker never captures). `cover` = share of a book's trades priced in
  a trusted live cell; low cover means the estimate speaks for only part of the book.
  Full mechanism: `docs/MMSELL_FILL_MODEL.md`.
- **`mmsell exit study`** — replays each settled position's captured intraday path
  (`mmsell_position_ticks`) through a grid of confirmed catastrophic stops (yes-mid ≥ L
  for K consecutive ticks) and volatility exits (yes-mid range over W ticks ≥ V),
  reporting mean, 5th-pctile tail, win%, %exit and the Δ vs hold, per book. Gate: at
  n≥100 **replayable**, promote a rule if Δp5-tail is clearly up AND Δmean ≥ −0.3¢.
  Full mechanism: `docs/MMSELL_EXIT_STUDY.md`.
- **A position must be born AND settle inside the capture window to be replayable** —
  coverage grows slowly after deploy. `replay n` (exit study) and `settled n` (fill
  model) are different denominators; don't conflate them.
- **Percentile-statistic gotcha:** at replay n < ~20, the "5th-percentile tail" is
  literally the single worst trade in the sample — one disaster dominates it entirely.
  Past n≈20 it becomes the *second*-worst trade, so a book's tail stat can swing
  sharply (even flip from disaster to clean) purely from n crossing that threshold,
  with no change in the underlying risk. Flag this explicitly when it happens instead
  of reporting it as "the exit fixed it" or "the risk went away."
- **A `*_pt` book is a live/paper TWIN, not a variant.** Twins are the parallel paper control
  beside a live run (`docs/LIVE_PAPER_TWIN.md`) and are deliberately filtered OUT of both reads
  here — they already enter at the live price, so the fill model would double-count the
  correction, and they must never be gated like a paper variant. If a live book is running, its
  read is the `parity` command (`live_paper_parity`) / the `live-paper-parallel` skill; mention it
  alongside this check rather than folding the twin into these tables.
- **mmsell10 and mmsell9 have historically been the only REALIZABLE EDGE books**
  (positive realizable P&L at high coverage); mmsell3/6/11/4/7 have run MIRAGE (paper
  reads positive, realizable negative) — a confirmed 50¢ stop (L50, K1 or K2) has been
  the consistent best-performing exit rule wherever a book actually has a tail to cut,
  but as samples grow this benefit has been **shrinking toward zero on most books**
  (their earlier "disaster" got diluted by new wins, not fixed by the rule) — mmsell2
  is the one book where a real, durable improvement has held up across repeated
  checks. Don't assume the pattern from the last run still holds; each check should
  speak for its own numbers.
- **`mmsellA1`–`mmsellA5` are the ANCHOR SET** (`docs/MMSELL_ANCHOR_SET.md`) — paper-only books
  that each add ONE tail-mitigation mechanic on the `mmsell10` entry (`lo=5,hi=10,maxyes=7`), so
  **`mmsell10` is their control and every anchor number must be read against it over the same
  window**, never in absolute terms. A1/A2/A3 = confirmed yes-BID stop at 12/20/30¢ (K=2);
  A4 = volatility ENTRY gate; A5 = two-sided short strangle. Two things to say correctly when
  reporting them: (a) A1–A3 take their exit *inside the book*, so the exit study's replay is
  redundant for them — their stop already happened; report their realized numbers vs mmsell10
  instead of replaying a stop on top of a stop. (b) A5 accrues slowly (it needs an event with
  both tails cheap) and its gate is a 95% lower bound on the pair win rate clearing 93.9% at
  n≥82 pairs — a high raw win% at n=20 means nothing here, which is exactly how the backtest
  failed. Full pre-registered gates: `docs/MMSELL_ANCHOR_SET.md` and `docs/BOOK_REGISTRY.md`.

## Procedure

### 1. Refresh the ops worktree from the default branch

```bash
cd /tmp/ops && git fetch origin claude/confident-goldberg-83u3q ops -q \
  && git checkout -B ops origin/claude/confident-goldberg-83u3q -q \
  && git push origin ops --force-with-lease -q
```
(Substitute the current default branch name if it has changed — check with
`git symbolic-ref refs/remotes/origin/HEAD` or ask if unsure. This step matters:
`mmsell_fill_model.py` / `mmsell_exit_study.py` / their ops-allowlist entries only
exist on `ops` after this refresh if a recent PR added or changed them.)

### 2. Run the fill model (standing read)

Write `{"type":"script","id":"mmcheck-fill-<short-id>","name":"mmsell_fill_model"}` to
`ops/request.json`, commit, push. Poll (~13s intervals, up to ~12 tries) for
`ops/results/<id>.txt` via `git fetch origin ops && git show FETCH_HEAD:ops/results/<id>.txt`.
Capture the `Optimistic vs REALIZABLE per book` table (n, cover, opt, real, read) for
all 12 books.

### 3. Run the exit study

Same pattern: `{"type":"script","id":"mmcheck-exit-<short-id>","name":"mmsell_exit_study"}`.
Capture, per book: replay n, HOLD (mean/tail/win%), and every rule's mean/tail/Δmean/Δtail
— you need the full per-rule table to pick each book's best exit, not just the headline.

### 3b. Anchor set — direct read (REQUIRED; the two scripts cannot see it)

`mmsell_fill_model.py` and `mmsell_exit_study.py` both select only `status='settled'`.
The anchor stop books close their exits as **`status='closed_sl'`**, so every stopped
trade is dropped from both tables — which made A1 read as "+1.56¢ REALIZABLE EDGE" on
run #5 when its true resolved P&L was −4.64¢. **Never report the anchor books from those
two tables.** Run this instead:

```jsonc
{"type":"db","id":"anchor-<short-id>","max_rows":60,"sql":"select strategy, count(*) as entries, count(*) filter (where status in ('open','pending')) as open_n, count(*) filter (where status='settled') as settled_n, count(*) filter (where status='closed_sl') as stops_n, count(*) filter (where pnl is not null) as resolved_n, round(sum(pnl) filter (where pnl is not null)::numeric,2) as total_pnl_usd, round((100*sum(pnl) filter (where pnl is not null)/nullif(count(*) filter (where pnl is not null),0))::numeric,2) as cents_per_trade from paper_trades where (strategy like 'mmsellA%' or strategy='mmsell10') and not legacy group by strategy order by strategy"}
```

Then run the **matched counterfactual** — the only read that actually decides whether a
stop pays, because it compares the stop against what the control did on the *same market*:

```jsonc
{"type":"db","id":"anchormatch-<short-id>","max_rows":60,"sql":"select a.strategy, coalesce(m.status,'NO CTRL ROW') as ctrl_status, count(*) as n_stops, round((100*avg(a.pnl))::numeric,1) as stop_avg_c, round((100*avg(m.pnl))::numeric,1) as ctrl_avg_c, round((100*min(m.pnl))::numeric,1) as ctrl_worst_c, round((100*(avg(a.pnl)-avg(m.pnl)))::numeric,1) as stop_saved_c from paper_trades a left join paper_trades m on m.market_ticker=a.market_ticker and m.strategy='mmsell10' and not m.legacy where a.strategy like 'mmsellA%' and a.status='closed_sl' and not a.legacy group by a.strategy, coalesce(m.status,'NO CTRL ROW') order by a.strategy, ctrl_status"}
```

Read `stop_saved_c` on the `ctrl_status='settled'` rows only — that is the stop's true
per-trade value. Rows with an `open` control are undecided, not evidence; **always say
how many stops are still pending a control outcome**, because a favorable early
`stop_saved_c` on a handful of matched pairs can invert entirely once the rest resolve.

### 4. Reset the ops channel

`{"type":"noop"}` to `ops/request.json`, commit, push. Always do this even if a step
failed partway — never leave a stale non-noop request on the channel.

### 5. Read prior state

```bash
git fetch origin mmsell-check-status -q 2>/dev/null \
  && git show FETCH_HEAD:docs/MMSELL_CHECK_STATUS.md 2>/dev/null
```
If the branch/file doesn't exist yet, this is run #1 — skip the diff, note it's a
baseline, and create the branch on step 6 (`git checkout -B mmsell-check-status
origin/<default-branch> -q` from a `/tmp/mmcheck` worktree, or from `/tmp/ops` if
reusing that worktree, then orphan-safe: base it off the default branch once, after
that just fetch+reset the dedicated branch itself).

### 6. Diff and persist

For each book, compare fill-model `n`/`real_$/ct`/`read` and exit-study `replay n`/best
rule against the prior snapshot. Then overwrite `docs/MMSELL_CHECK_STATUS.md` (increment
a run counter, timestamp, full current numbers for both reads including the best-exit
rule found per book) and push to `mmsell-check-status` **only** — never the default
branch, never `ops`.

### 7. Report

**ALWAYS INCLUDE TOTAL P&L IN DOLLARS.** Every per-book table in the report carries a
`total P&L` column alongside the per-trade figure — no exceptions, no table without it.
Per-trade cents say whether an edge exists; total dollars say whether it is worth
anything, and the project's north star is **$100/month realized**, which is a dollar
number. A book at +5¢/trade on n=12 and a book at +1¢/trade on n=3000 read identically
in a cents column and are not remotely the same thing. Also state the combined dollar
total for the anchor set and note it against the $100/month goal.

Three tables in chat. Tables 1–2 ordered `mmsell, mmsell1, mmsell10, mmsell11, mmsell2,
mmsell3, mmsell4, mmsell5, mmsell6, mmsell7, mmsell8, mmsell9` (natural strategy order,
not alphabetical):

1. **Standing realizable read** — `book | n (last→now) | realizable ¢/ct (last→now) |
   total P&L $ | verdict`. Call out any verdict flip explicitly (edge↔mirage↔dead) and
   any book with zero new settled trades for 2+ consecutive checks (a stall worth a
   separate look).
2. **Exit study — best exit per book** — `book | replay n (last→now) | HOLD mean/tail
   (last→now) | best-performing rule this run | Δmean | Δtail`. If no rule beats hold,
   say so plainly rather than picking the least-bad one. Apply the percentile-statistic
   caveat above whenever a tail stat swings sharply at low n.
3. **Anchor set** (from step 3b, never from the two scripts) — `book | entries | open |
   settled | stops | resolved | total P&L $ | ¢/trade`, with `mmsell10` as the control
   row. Follow it with the matched-counterfactual line per stop book (`stop_saved_c` and
   how many stops still have an unresolved control). Always caveat that the resolved
   column is biased low while positions are open: a stop closes instantly, a winner
   waits days to settle, so the stop books' early numbers are a resolution-speed
   artifact, not a verdict.

Close with 2-4 sentences: what's actionable (a book crossing its gate, a rule holding
up across repeated checks vs. one that's shrinking), and the one thing worth a
follow-up if anything looks stalled or contradictory. Keep it tight — this is a status
pass, not a new analysis; save deeper investigation (e.g. "why did entries stop") for a
follow-up turn if the numbers warrant it.
