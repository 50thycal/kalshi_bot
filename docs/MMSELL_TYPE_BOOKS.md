# mmsell MARKET-TYPE books — `Wmmsell1–8` (wide band) and `Tmmsell1–6` (tight band)

Built 2026-08-03 off the market-type census (`docs/MMSELL_MARKET_TYPES.md`). Paper only.

> ## VERDICT 2026-08-12 — the WIDE half (`Wmmsell1`–`Wmmsell8`) is RETIRED. The TIGHT half runs on.
>
> **Retired as UNMEASURABLE, not as disproven.** That distinction is the whole point of this
> entry, because "we could not measure it" and "it does not work" license completely different
> future decisions.
>
> Two reasons, and the second is structural:
>
> 1. **Their control loses money.** `mmsell` (wide band) runs −1.80¢/trade, ≈−0.93¢ after the
>    2026-08-11 maker-fee correction. The gate here is *relative* — beat the control by ≥1.0¢ —
>    and five W books cleared that bar while still losing money in absolute terms. The absolute
>    floor added on 2026-08-09 exists because of exactly this. Beating a losing control is not
>    an edge.
> 2. **We have no fill evidence for this band at all.** Fill coverage is **19–41%**. The live
>    calibration in `docs/MMSELL_FILL_MODEL.md` comes from real maker orders in the CHEAP band;
>    the wide band's 10–40¢ entries have never been tested live. So for the majority of every W
>    book's trades we cannot say whether a resting order would have filled — and paper assumes
>    it always does. Their realizable numbers are estimates over a minority of each book.
>
> **The type hypothesis itself survives.** `Tmmsell1`–`Tmmsell6` test the SAME contract-type
> axis in the tight band at **99–100%** coverage. The axis lives on; only the band dies.
>
> **Revival condition — evidence, not patience.** Revive the wide band only if we obtain live
> maker fills in the 10–40¢ range. More paper cannot resolve this: paper's fill assumption is
> precisely the thing in question, so a larger paper sample would produce a more confident
> version of the same unfounded number.


## The thesis

Every mmsell book to date has been blind to what KIND of contract it is selling. The entry rule
sees a cheap tail and sells it, so a Trump-mention market, a BTC daily strike, an MLB run-line and
a tennis match winner are the same trade to the engine. The census scored all 118 traded series by
contract structure and found they are emphatically not interchangeable — **and that the ranking
changes with the price band**, which is why this is two families rather than one.

The single sharpest result, and the reason the two families exist:

| type | wide-band edge | tight-band (≤7¢) edge |
|---|---|---|
| `total` | **−0.4pp** | **+3.5pp** |
| `h2h` | +0.7pp | **−0.5pp** |
| `game_prop` | +4.2pp | **−0.6pp** |

Pooled across all prices, `h2h` looks fine and `total` looks like the problem. At the price we
actually trade, that inverts. Any type decision taken from one band and applied to the other is
therefore a coin flip, so each family is tested inside its own band against its own control.

## Design

**Each book differs from its control ONLY by the market-type filter.** No book carries a stop,
volatility gate or strangle leg — those are the anchor set's experiment (`docs/MMSELL_ANCHOR_SET.md`)
and would confound this one.

| family | band | control |
|---|---|---|
| `Wmmsell*` | `lo=5,hi=40`, no `maxyes` | **`mmsell`** (the untouched control book) |
| `Tmmsell*` | `lo=5,hi=10,maxyes=7` | **`mmsell10`** (the live candidate) |

Selection uses the taxonomy in `kalshi_bot/mmsell/market_types.py` via three new variant keys —
`mtype=` (type allowlist), `mode=` (settle-mode allowlist), `xmtype=` (type blocklist, applied
last). These select on contract STRUCTURE rather than on a series substring, so a newly listed
series is picked up the moment it enters the taxonomy instead of needing every book's `only=` list
hand-extended.

**Unknown-series asymmetry (deliberate):** an unclassified series is in no allowlist, so an
`mtype`/`mode` book never silently picks up a contract nobody has classified. It IS admitted by a
pure `xmtype` book, because those are defined as "my control minus these named types" — dropping
unknowns there would make them differ from the control by more than the thing under test.

## The books

### Wide band — read against `mmsell`

| tag | filter | thesis |
|---|---|---|
| `Wmmsell1` | `mode=in_play` | Does the live-contest clock matter at all? The pure in-play book. Note it deliberately gives up `mention` (+$80.67) and `price_strike` (+$19.67) — two of the three biggest earners — so it is a test of the *clock*, not a bid for max P&L. |
| `Wmmsell2` | `mtype=player_prop+spread+exact_score+game_prop+h2h_period` | The five in-play types with positive wide-band edge, i.e. in-play minus `h2h` and `total`. |
| `Wmmsell3` | `mtype=player_prop+spread+game_prop` | The concentrated version of W2 — only the three with both a strong edge and real volume. |
| `Wmmsell4` | `mtype=price_strike` | Crypto/oil/gas strikes alone (+1.7pp, n=1,298). Pure scheduled-settle. |
| `Wmmsell5` | `mtype=mention` | The single best-earning cell in the book: +$80.67 at +6.9pp on n=1,474, with a notably shallow tail (avg loss −66.5¢ vs −85 elsewhere). |
| `Wmmsell6` | `xmtype=total+h2h+event_stat+announcement+politics` | **Max flow minus the five negative-edge types.** The "just stop doing the bad things" book — likely the best pure-P&L wide book. |
| `Wmmsell7` | `mode=scheduled+discrete`, minus the bleeders | **The exact complement of W1.** Running both isolates the settle-mode axis cleanly, which is the prerequisite for the entry-timing work. |
| `Wmmsell8` | `mtype=player_prop+mention+spread+outright` | High conviction: every type with edge ≥ +3.0pp AND n ≥ 500. ~38% of flow, the four best-powered positive cells. |

### Tight band (≤7¢) — read against `mmsell10`

| tag | filter | thesis |
|---|---|---|
| `Tmmsell1` | `mtype=price_strike` | Largest tight-band cell (n=492, +2.2pp). |
| `Tmmsell2` | `mtype=mention` | +8.5pp and an avg loss of only −31.9¢ — its losses are not all-or-nothing, unlike every in-play cell. Structurally the lowest-variance thing in the book. |
| `Tmmsell3` | `mtype=player_prop+total+spread` | The top three in-play cells by tight-band edge. `total` is here and absent from W2/W3 — that inversion is the point. |
| `Tmmsell4` | `xmtype=h2h+game_prop+event_stat+politics+announcement` | **Max flow minus the five negative tight-band cells.** The T-side analogue of W6. |
| `Tmmsell5` | `mode=scheduled+discrete`, minus the bleeders | The T-side no-clock book; control for T3 on the settle-mode axis. |
| `Tmmsell6` | `mtype=player_prop+spread+exact_score+mention+price_strike+outright+rank_culture` | **Both-band survivors** — only types with positive edge in BOTH tables. Excludes `total`, `h2h` and `game_prop` precisely because they flip sign across the bands. A robustness filter: if the edge is real it should not care what price we paid. |

## Pre-registered gates

Read every book against **its own control over the same window** — never in absolute terms, and
never against a control's lifetime number (the controls carry months of history and a different
regime mix).

- **KEEP** at n ≥ 150 settled (W books) / n ≥ 100 (T books) only if **all three** hold:
  1. the book's own ¢/trade is **> 0** in absolute terms;
  2. ¢/trade beats its control by **≥ +1.0¢** over the same window;
  3. `mmsell fill model` realizable ¢/trade is **> 0** — the paper number carries no maker
     adverse-selection haircut and is not promotable on its own.
- **KILL** if ¢/trade ≤ control at n ≥ 150 / 100, or ≤ 0 absolute, or realizable ≤ 0.

> **Condition 1 was added 2026-08-09 to fix a real flaw in this gate.** It originally required
> only "beats control by ≥1.0¢". Once the scan starvation was fixed and the wide-band control
> `mmsell` settled at **−1.80¢/trade**, five W books cleared that bar — `Wmmsell1` (+0.42),
> `Wmmsell2` (+0.40), `Wmmsell8` (+0.10), `Wmmsell3` (−0.13), `Wmmsell6` (−0.73) — while three
> of them were still **losing money**. Beating a losing control is not an edge. A relative test
> alone silently promotes "less bad"; the absolute floor is what makes the gate mean something.
>
> Read the wide band as **dead** rather than as five passing books. Its problem is the entry
> price (~18–22¢ average), which sits squarely in the adverse-selection zone the fill model
> identified — not the type filter, which is doing its job on top of a bad base.
- **Narrow books** (`Wmmsell4/5`, `Tmmsell1/2`) may take a quarter to reach n. An empty book early
  is a flow constraint, not a verdict — check `flow%` in the census before reading anything into it.

**The honest caveat:** these filters were derived from the same trading history they will first be
measured against, so the census cells are a PRIOR, not a result. The forward test earns its keep
two ways — out-of-sample persistence, and the Sept–Nov regime change (NFL, MLB playoffs, NBA/NHL,
the Nov-3 midterms) that `docs/MMSELL_SEASONAL_FORECAST.md` says our whole history cannot speak to.
A type edge that survives a regime change is worth acting on; one that does not was a sample.

## Known artefact in the first ~4 hours of history (2026-08-04 12:47 → 16:24)

`abandon_open_paper_trades` clears books that are not part of the running experiment, and it
matched the kept families on a **prefix**. `Wmmsell*`/`Tmmsell*` therefore looked foreign, so
every worker start wiped their open positions — and because the entry scan's dedup guard keys off
an OPEN position, each book re-entered the same market on the very next cycle. `Wmmsell6` ended up
holding 9 markets behind **47 `abandoned` rows** in under four hours.

Fixed by `repository.strategy_is_kept` (membership is no longer a plain prefix test), with a
regression test. Two consequences for reading these books:

* **Discount raw trade COUNTS before 2026-08-04 16:24.** The `abandoned` rows are duplicate
  re-entries, not independent bets. Every gate here counts `status IN ('settled','closed_sl')`,
  which excludes them, so the P&L numbers were never affected — only the row counts.
* Before the fix no book could carry a position across a deploy, so **none of them could ever have
  reached a settled-n gate.** The clock on all 14 gates effectively starts at the fix.

## Not included yet

Entry-timing twins (`<tag>.timeX`) are deliberately **not** built here. The census showed
"hours to close" means three incompatible things across `in_play` / `scheduled` / `discrete`, so a
timing rule needs a per-mode framework rather than one global window. These books establish the
type baseline that the timing twins will be measured against.

Also still open: we do not persist hours-to-close at entry per trade. `hold_h` in the census is a
proxy (`closed_at − created_at`) that conflates entry timing with settlement-detection lag. The
real value lives in `mmsell_candidate_ticks.hours_to_close`, whose coverage needs checking before
any timing study can run on our own book.
