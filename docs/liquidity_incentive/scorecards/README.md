# LIMM scorecards and the 72-hour review

Every 72 hours the liquidity-incentive book (`Alimm1`) gets a scorecard and a trend review.
Each run is one file here, `YYYY-MM-DD.md`, so the history is durable and diffable. Chat is not
the record.

## How a run works

1. **Scorecard.** Run the read-only ops script:
   `{"type": "script", "name": "limm_scorecard", "args": ["--days", "3"], "id": "<unique>"}`.
   It prints rewards and fill P&L by market type and by event, the losing markets and how they
   filled, and a final `JSON {...}` line. Rewards are the operator-confirmed payouts in
   `kalshi_bot/liquidity_incentive/reward_ledger.py`; a payout the operator has not
   screenshotted yet does not exist to the script, so ask for the Rewards page first.
2. **Trend review.** Read every earlier file in this folder and compare the `JSON` lines and
   tables:
   - net per market type (mention_event, youtube_views, …), lifetime and last window;
   - reward per event, especially events entirely under the current rules;
   - loss markets per window, average loss, and how they filled (fills, span, lead before the
     event);
   - which rule changes (`docs/LIQUIDITY_INCENTIVE_THESIS.md` §9.x) fall inside the window.
3. **Recommendations**, in two groups, each with the evidence behind it:
   - **reward harvesting** (more reward per dollar and per slot): universe, sizing, ranking,
     step-aside timing, slot use;
   - **fill protection** (smaller or rarer fill losses): partial-fill cancel, contagion, price
     caps, stop-loss shape.
   Mark each as *keep*, *try*, or *revert*, and say what result would change the call.
4. **Write the file** `YYYY-MM-DD.md`: the scorecard output verbatim (fenced), then the
   trend review and recommendations, under 60 lines of prose. Open a docs-only PR and merge it
   when CI is green.
5. **Update the dashboard** (https://claude.ai/artifact/V4SBppmpJee6qzMAgdi1vQ): run the script
   again with `--json`, upload `by_type`, `by_event`, `losers` as JSON files, append this run's
   per-type totals to `history`, and replace `recommendations` with this run's list (fields
   `area`, `call`, `item`, `evidence`, `change_if`). Each upload updates its dataset's `url`,
   `name` and `updated`.
6. **Tell the operator** in plain words: net this window, the one trend that matters, and the
   recommendations. Nothing is built or changed without the operator's go.

## Limits

- Rewards lag events by a day or two, and only screenshotted payouts count: the newest window
  always under-counts rewards. Say so rather than read it as a decline.
- Samples are small (a handful of fills per window). Prefer "consistent across three windows"
  over a single swing.
