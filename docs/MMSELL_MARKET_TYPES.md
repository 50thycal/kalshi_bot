# mmsell market-type census

**Command:** `{"type": "script", "name": "mmsell_market_types"}` — `scripts/mmsell_market_types.py`

## What it answers

mmsell sells any cheap tail it can find. To the entry rule, a Trump-mention market, a BTC daily
strike, an MLB run-line and a tennis match winner are the same trade. They are not the same trade
to the market. This script is the first read that slices our own mmsell trading history by the
**structure of the contract** rather than by price (`mmsell_fill_model`), book (`mmsell_live`),
exit rule (`mmsell_exit_study`) or sport (`mmsell_regime_backtest`).

`docs/MMSELL_ROADMAP.md` §9 found the first crack in the "a tail is a tail" assumption — h2h
winners carry the loss engine — but that was a two-cell view of a book spanning 118 series. This
builds the full taxonomy so a per-type decision (allowlist, blocklist, price cap, entry window)
can be made on our own data.

## The taxonomy

`SERIES_TYPES` maps a series prefix to `(market_type, settle_mode)`. It is a hand-audited table,
not a regex, for the same reason `mmsell_h2h_study` uses one: the classification IS the hypothesis,
so it must be reviewable. Longest prefix wins (`KXWC1HTOTAL` is a total, not the 1st-half winner;
`KXMLBHRDERBYMATCHUP` is h2h, not an outright). An unmatched series lands in `unclassified` and is
printed loudly with its volume — a new series shows up as a gap to classify, never silently folded
into a neighbouring bucket.

15 types: `h2h`, `h2h_period`, `spread`, `total`, `exact_score`, `player_prop`, `game_prop`,
`outright`, `mention`, `price_strike`, `econ_release`, `rank_culture`, `event_stat`, `politics`,
`announcement`.

**`settle_mode` is the axis a timing study needs**, and it is orthogonal to type:

| mode | meaning | what "1 hour to close" means |
|---|---|---|
| `in_play` | resolves through a live contest; the outcome is progressively revealed | the outcome is nearly determined |
| `scheduled` | resolves at a fixed instant off an external print | nothing has happened yet |
| `discrete` | resolves whenever an event does/doesn't occur in a window | nothing in particular |

Pooling those three is how a timing result becomes a mirage, so every table carries the split.

## Reading the output — four traps

1. **`n` is book-trades, not independent bets.** Up to 16 books trade the same ticker in the same
   cycle, so `n` is inflated by book duplication and the trades inside a cell are strongly
   correlated. `mkts` (distinct tickers) is the honest independence measure; the `--book` table
   is a duplication-free read under one parameter set.
2. **The pool is mostly the OLD wide-band config.** `mmsell`/`1`/`2`/`3` are ~80% of settled
   trades and entered at 12–20¢, well above the 7¢ cap the live candidate uses. The **cheap-band
   table (entry ≤ 7¢)** holds price roughly fixed so a difference between types is about the
   contract, not about what we paid for it. Prefer it for any decision.
3. **`p5` lies on a >95%-win cell.** With one loss in twenty, the 5th percentile interpolates
   *between* the loss and the premium and prints positive while the cell is bleeding.
   `loss% × avgloss` is the tail that actually drives `c/trade`.
4. **`edge` is the only cross-type-comparable column.** Each type is entered at a different
   premium, so its raw loss rate is measured against a different break-even. Selling a tail for an
   average of W and losing an average of L, a cell breaks even at `W/(W+|L|)` = `be%`; `edge` is
   `be% − loss%` in percentage points. `edge ≤ 0` means the cell is not paying for its tail.

And the standing one: this is **paper P&L, fill-everything**. It carries no maker adverse-selection
haircut. `mmsell_fill_model` converts a per-price paper number into a realizable one; nothing here
is a promote signal on its own.

## Standing result (2026-08-03, n=13,483 trades / 4,538 distinct markets / 16 books)

Blended paper across the whole mmsell family: **+$257 at +1.91¢/trade**.

In the **cheap band** (≤7¢, n=2,853 — the live regime), ranked by `edge`:

| type | n | mkts | ¢/trade | edge | read |
|---|---|---|---|---|---|
| `player_prop` | 339 | 144 | +4.29 | +4.3pp | best in-play cell |
| `total` | 415 | 109 | +3.00 | +3.5pp | |
| `spread` | 210 | 60 | +2.17 | +2.4pp | |
| `price_strike` | 492 | 106 | +1.61 | +2.2pp | |
| `exact_score` | 195 | 80 | +1.37 | +1.4pp | |
| `outright` | 153 | 40 | +0.61 | +0.6pp | thin |
| `h2h` | 447 | 118 | **−0.41** | **−0.5pp** | the §9 finding, confirmed at our price |
| `event_stat`, `politics`, `announcement` | 42 | 17 | −13 to −24 | −14 to −24pp | tiny, uniformly bad |

`mention` (+3.17¢, +8.5pp, n=441) is nominally second but its `be%` is inflated by a small number
of very large wins; treat the edge figure there as soft.

**The two actionable reads:** `h2h` is the only large in-play type that fails to clear its own
break-even at the price we trade (15.7% of cheap-band flow for −$1.82), and the
`event_stat`/`politics`/`announcement` trio loses $8.81 on 1.5% of flow. Cutting the trio lifts the
cheap band from +1.74¢ to +2.08¢/trade; cutting h2h as well takes it to +2.55¢/trade. Both are
paper numbers and must clear `mmsell_fill_model` before they mean anything live.

Note the pooled (all-price) table shows `total` at −0.35¢ and `h2h` at +0.63¢ — the *opposite*
ordering. That is trap 2 in action: pooled, both types are dominated by 16¢ entries, and the
difference between them is entry price rather than structure. The cheap-band cut is the one to
read.

## Usage

```jsonc
{"type": "script", "name": "mmsell_market_types"}
{"type": "script", "name": "mmsell_market_types", "args": ["--series"]}          // per-series detail
{"type": "script", "name": "mmsell_market_types", "args": ["--book", "mmsell3"]} // one book
{"type": "script", "name": "mmsell_market_types", "args": ["--maxyes", "5"]}     // tighter band
```

Statuses default to `settled,closed_sl` — filtering to `settled` alone silently drops every
position a stop actually closed, the exact reading error recorded against the anchor books in
`docs/BOOK_REGISTRY.md`. Twin books (`mmsell10_pt`) are excluded by default: a twin enters at the
live maker price, so pooling it mixes two entry conventions.

Classifier and stats are pure functions, unit-tested in `tests/test_mmsell_market_types.py`.
