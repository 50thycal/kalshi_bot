# mmsell exit-rule study — stop-loss & volatility exit vs hold-to-settlement

**Question:** the mmsell books hold every position to settlement. That harvests the favorite-
longshot bias but eats an all-or-nothing tail — the rare sold longshot that hits costs ~the whole
stake (a NO bought at 92¢ settling 0 = −92¢). Can a **catastrophic stop-loss** or a **volatility
exit** shave that tail without gutting the mean, and **how much does it change each book**? This
doc is the design; `scripts/mmsell_exit_study.py` (ops command **"mmsell exit study"**) is the read.

## Why this needed new data (and a forward test, not a backtest)

The obvious move — backtest exits on history — doesn't work here. Two blockers, both real:

1. **We never recorded the intraday price path for mmsell (sports) tickers.** `market_snapshots` is
   the main scanner's different universe (0 rows for these tickers) — the same gap that blocked a
   per-ticker fill model. So there was nothing to replay.
2. **Kalshi's own candle history is too noisy at these prices to trust.** Running the existing
   candle-based sweep (`kalshi_mm_exits`) on the cheap 5–11¢ band returned −93¢/100%-exit for
   *every* stop rule — an artifact: thin-book minute candles throw spurious high **ask** prints, so
   a naive stop fires on essentially every position on one bad tick. (The hold row was clean and
   confirmed the +4.1¢/94% edge.) That artifact is itself the lesson: **a stop at these prices must
   trigger on a confirmed signal, not a single print.**

So this study runs **forward** on our own clean, live-collected tape:

- **New capture:** `mmsell_position_ticks` records one price tick per open-mmsell ticker per
  management cycle, taken off the orderbook `manage_open_positions` already fetches (no extra API,
  deduped to one row per ticker per cycle — the path is ticker-level). Gated by
  `mmsell_tick_capture_enabled` (default on). Positions still hold to settlement — this is **pure
  data capture**, not an exit.
- **Counterfactual replay:** the study replays each settled position's captured path through an
  exit grid and reports what each rule *would* have done vs the actual hold. Because the baseline
  book is untouched, the A/B is clean by construction, and we can sweep every threshold at once
  instead of spawning dozens of executing mirror books.

This same tape also finally enables a true per-ticker fill model later (the `mmsell_fill_model`
follow-up) — one capture, two payoffs.

## The two exit mechanics

Both act on a NO position (bought at `entry_no`; profits as yes falls). No take-profit — a quiet
winner is left to settle, since settlement is where the edge lives.

- **Confirmed catastrophic stop (L, K):** exit once the sold longshot reprices *up* through level
  `L` (yes-mid ≥ L) for `K` **consecutive** ticks. The K-confirm is the whole point — it defeats
  the single-print whipsaw that wrecks a naive stop at thin prices. Caps the tail: exit sells the
  NO back at the current no-bid.
- **Volatility exit (W, V):** exit once the yes-mid's range over the trailing `W` ticks reaches `V`
  cents. Thesis: a position swinging hard is one informed flow is actively repricing — likelier to
  be adverse (a loser). Cutting volatile positions may preferentially shed losers; quiet ones ride
  to settlement.

Exit accounting = sell NO at the no-bid, taker fee both sides, matching the paper `kalshi_fee`
model. Grid swept by default: stop L ∈ {30,40,50,60} at K=2 (plus L50 K1 to expose the whipsaw the
confirm removes), vol V ∈ {8,15,25} at W=6, and a combined L50K2+V15. Tune in `_rules()`.

## How to read it

Per book, vs the HOLD baseline:

- **Δp5(tail)** — the number that matters for a stop. A good exit **lifts the 5th-percentile P&L**
  (cuts the −90¢ disasters), so Δp5 ≫ 0.
- **Δmean** — the cost of that protection. If Δmean is ~0 or positive while Δp5 is strongly
  positive, the exit is free tail insurance → promote it. If every rule's Δmean is sharply negative
  (exits cut winners-that-revert faster than they save losers), **hold + diversify wins** — which
  is the prior on this favorite-longshot book, so the bar is "beat hold," not "looks plausible."
- **%exit** — how often the rule fires. A rule that exits 60% of positions isn't a tail-cutter,
  it's a different strategy.

**Coverage caveat:** only positions **born and settled inside the capture window** are replayable.
Capture starts at deploy, so coverage is ~0% at first and grows as the cheap-longshot cohort turns
over (hours-to-days). The report prints coverage per book; early low-coverage numbers are noisy —
weight by n, and re-run as it matures. Empty output right after deploy is a data-maturity wait, not
an error.

## Pre-registered gate (per book, per rule)

At **≥100 replayable settled positions** for a book, a rule is **PROMOTE** if it beats hold on
**Δp5 (tail) by a clear margin AND Δmean ≥ −0.3¢** (essentially free tail protection). A rule that
improves the tail but costs > ~0.5¢/trade of mean is a **NO** (the tail wasn't the thing killing
the book — the mean was). If no rule clears for any book, the finding is "hold-to-settlement + small
size + diversification is the right risk control" — a legitimate, expected outcome given the prior,
and itself worth recording. A rule that clears cleanly on a promotable book (e.g. mmsell10) becomes
the exit for the next live re-test of that book.
