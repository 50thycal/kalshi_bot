# mmsell scorecards and the 72-hour review

Every 72 hours the live mmsell books get a scorecard and a trend review. The books are
`Kmmsell10`, the incumbent, and `Vmmsell10`, the WIN-set canary. `Nmmsell10` is shown while it
drains. Each run is one file here, `YYYY-MM-DD.md`, so the history is durable and diffable.
Chat is not the record.

Experiment OS stays canonical. A review links to it and never restates a standing, gate read or
verdict. **A recommendation here is not a gate verdict** and never moves a lifecycle state.

## How a run works

1. **Scorecard.** Run the read-only ops script:
   `{"type": "script", "name": "mmsell_scorecard", "args": ["--days", "3"], "id": "<unique>"}`.
   It prints, per live book and then per category (weather, index, politics, mentions, sports,
   other):
   - settled markets, contracts, realized P&L after fees, ¢/contract and losing markets;
   - open markets and their cost;
   - the same for the last window.

   It also prints three more sections, then a final `JSON {...}` line:
   - each book's same-market gap to its paper twin;
   - a KXTRUMPMENTION section (the liquidity-incentive book `Alimm1` quotes the same markets);
   - the losing markets.

   P&L comes from each book's own fills, priced at settlement, the same formula as
   `kalshi_bot/experiment_os/live_attribution.py`. So a market two books share is split
   correctly between them.
2. **Trend review.** Read every earlier file in this folder and compare the `JSON` lines and
   tables:
   - net per book and per category, lifetime and last window;
   - losing markets per window, the average loss, and how they filled (entry price, fills,
     hours from first fill to settlement);
   - the twin gap. Live well below twin means fills are adverse. The keep gates own the
     threshold.
   - the KXTRUMPMENTION overlap with `Alimm1`: shared books can cross or thin each other;
   - which changes fall inside the window: a new book or tag, a universe change, or a
     taxonomy revision. Find them in `docs/workstreams/WS-027-win-set-shared-canary.md` and
     Experiment OS.
3. **Recommendations**, in three groups, each with the evidence behind it:
   - **edge / universe**: which categories or series earn, which to add or drop;
   - **fill protection**: smaller or rarer losses (price band, timing, contest caps);
   - **profit**: what raises realized profit most, such as clip size or which book carries a
     category.

   Mark each as *keep*, *try* or *revert*, and say what result would change the call.
4. **Write the file** `YYYY-MM-DD.md`: the scorecard output verbatim (fenced), then the trend
   review and recommendations, in under 60 lines of prose. Open a docs-only PR and merge it
   when CI is green.
5. **Update the dashboard** (https://claude.ai/artifact/GKUXvPcdU7RJRUEsNvNCfP): run the script again with
   `--json --days 7`. Then update the datasets:
   - upload `by_tag`, `by_book`, `by_event`, `losers` and `overlap` as JSON files, one per call;
   - append this run's per-book totals to `history`;
   - replace `recommendations` with this run's list, with the fields `id`, `area`, `call`,
     `item`, `evidence` and `change_if`;
   - each upload updates its dataset's `source` and `updated`.
6. **Tell the operator** in plain words: net this window, the one trend that matters, and the
   recommendations. Nothing is built or changed without the operator's go.

## Limits

- Samples are small: a few settled markets per book per category each window. Prefer
  "consistent across three windows" over a single swing.
- A loss is the whole clip (about 94¢ a contract), and a win is the 5–10¢ band. One loss
  erases about 15 wins, so read the losing-market count before the P&L.
- Tags are fresh at every arming, so a tag never spans two Experiment OS epochs. Never add a
  retired tag's totals to its successor's.

## The routine

A self-bound 72h trigger in the owning session runs this protocol and re-arms itself first
(`MMSELL 72h scorecard + trend review`, first run 2026-11-01 03:30Z). Its stored prompt is the
procedure above, adapted from `docs/handoffs/HANDOFF-72h-review-for-mmsell.md`.
