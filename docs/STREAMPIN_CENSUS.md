# STREAMPIN — streaming-count observation-pin (census-first pre-stage)

*Idea-model Area 1, 2026-07-21. **Not yet a thesis** — promoted only to a testability census,
matching the `kalshi_art_survey` / `kalshi_seasonpin_census` discipline. The full pre-registered
thesis is written only if the census (`scripts/kalshi_stream_survey.py`) clears C1–C2 below.*

## The idea

The purest port of the one mechanic that has ever worked here (weather `con`/`obs`: compute a
slowly-published ground truth the quote lags) to a genuinely new, uncorrelated universe — the
~2,550 `KXARTISTSTREAMS`-style music-streaming markets that settle on stream counts published
on a lag (weekly Luminate/Spotify/Billboard chart cadence). If the running count is estimable
before it posts, you pin the winning side while the Kalshi quote lags the real tally.

## Why census-first (the honest gate)

The research record is explicit: the weather inattention window **does not generalize
off-weather** once the answer is public. STREAMPIN survives that lesson only if the resolving
quantity is (a) slowly published AND (b) estimable *ahead of* publication from a signal the
market underuses. That is an empirical question, so it is gated — no thesis until the data says
it is worth one.

## Census gates (`scripts/kalshi_stream_survey.py`, read-only public Kalshi REST)

- **C1 — a cumulative-count instrument exists.** There are markets settling on a *running total*
  ("total streams by date X", first-week units), not just one-shot rankings ("#1 song this
  week"). Without a cumulative instrument there is nothing to pin a running estimate against.
- **C2 — settled history + an intra-window tape.** ≥ some settled cumulative-count markets with
  volume, AND they trade *during* the counting window (a live tape), rather than jumping once at
  settlement. Probed via candlesticks around settled markets' close windows.
- **C3 — (thesis-stage, external) an estimable leading signal.** A public stream proxy
  (Spotify/Luminate/chart API or artist-level counters) updatable faster than the market prices
  it and partly predictive of settlement before the official post. **Kalshi data alone cannot
  settle C3** — it is the first probe of the thesis stage, reached only if C1 and C2 clear.

## Decision

- **C1 AND C2 clear →** write `docs/STREAMPIN_THESIS.md` with pre-registered predictions, whose
  first probe is the C3 external-signal feasibility test (does a fetchable stream proxy lead the
  Kalshi quote?), then the obs-pin EV measurement.
- **Otherwise →** HOLD with the specific missing piece named (no cumulative instrument / too new
  / no intra-window tape). Not a kill — re-run as the category matures.

## Correlation / value

Zero shared return driver with any live book (weather / sports-maker / crypto tails) — the most
uncorrelated new universe on the Area-1 board if it proves testable, hence worth the cheap
census even though the prior is uncertain.

## Status

- Census script: `scripts/kalshi_stream_survey.py` (allowlisted in `ops_runner.py`).
- Verdict: **pending first census run** via the ops channel.
