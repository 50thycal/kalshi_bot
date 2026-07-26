# Live/paper parallel runs — the standing anti-mirage control

> **Policy: every strategy promoted to real money runs a fresh paper TWIN beside it.**
> No exceptions, no "we'll add it later". A live book without a twin is a live book whose
> paper edge can never be audited.

The north star is +$100/month realized. The single biggest threat to getting there is not a bad
strategy — it's a **good-looking paper strategy that isn't real**. Nine of the eleven mmsell
variants read positive on paper; the live-calibrated fill model says most of them are mirages. That
correction came from an *estimate* projected through one 359-trade live window. This harness
replaces the estimate with a **direct, per-book measurement**.

---

## 1. Why the obvious comparison doesn't work

The tempting move is to compare the live book against its own long-running paper book. That
comparison is confounded four ways at once:

| confound | incumbent paper book | live book |
|---|---|---|
| **sample & regime** | months of history, many sports seasons | days, one regime |
| **sizing** | 1-contract clips (`PAPER_ORDER_SIZE`) | dollar-cap sizing (`LIVE_MAX_ORDER_DOLLARS`) |
| **concurrency** | up to `MMSELL_MAX_OPEN_POSITIONS` (200) | up to `MMSELL_LIVE_MAX_OPEN_POSITIONS` (60) |
| **entry price** | the raw no-bid | no-bid **+ `MMSELL_LIVE_PRICE_OFFSET_CENTS`**, capped at the no-ask |

Any gap you measure is some unknown mixture of those four plus the thing you actually care about.
So you can't act on it.

## 2. What the twin is

For live tag `X`, the twin is a **new paper book tagged `X_pt`** that:

1. **starts at the same instant as the live run.** A fresh tag has no history by construction, and
   an epoch row (`live_paper_twins.started_at`) is written on the first cycle the live tag is armed.
   Every number in the parity report is scoped to that timestamp **on both sides** — live orders
   placed before it are excluded and reported as an anomaly.
2. **sees exactly the candidate set live saw** — identical band, hours-to-close window, series
   filters and entry-price ceiling, evaluated on the same orderbook fetch in the same cycle.
3. **is parameterized to the LIVE knobs, not paper's**: the live maker price rule, the live
   dollar-cap sizing, the live open-position cap, the live spread sanity gate.
4. **starts and stops with live.** If the kill switch goes on or the tag leaves `LIVE_STRATEGIES`,
   the twin stands down the same cycle. Otherwise it would quietly accumulate trades over a window
   live never traded, and the one-to-one property would be gone.
5. **can never place a real order.** The twin path never calls the executor, and a twin tag that
   also appears in `LIVE_STRATEGIES` is dropped from the pair list outright.

That leaves **exactly one** difference between twin and live:

> the twin assumes its resting order fills; the live book has to actually get filled.

### Gates the twin applies, and gates it deliberately doesn't

| gate | applied to twin? | why |
|---|---|---|
| entry band / htc / series / `maxyes` | **yes** | these *define* the strategy |
| live spread sanity cap | **yes** | market-quality: part of the strategy's candidate definition |
| live open-position cap | **yes** | shapes which candidates each side ever sees |
| account balance, daily-loss trip, per-market exposure, in-flight dedup | **no** | these depend on the live account's momentary state; applying them would make the twin a function of our own bankroll instead of a clean counterfactual |

The not-applied gates are **recorded instead** (§3), so their effect is visible as capacity rather
than mistaken for edge.

### Two useful consequences of the `X` → `X_pt` naming

* **The twin inherits every prefix-keyed behaviour of its parent.** The paper engine decides
  hold-to-settlement, tick capture and the abandon-on-start keep-list by strategy *prefix*, so
  `mmsell10_pt` is managed exactly like `mmsell10` with no extra wiring. A twin of a future
  `theta`/`tfav`/`pin15` book inherits the same way.
* **Twins are excluded from the gating studies**, on purpose: `mmsell_fill_model` (a twin already
  enters at the live price, so projecting it through the live fill calibration would double-count
  the correction) and the unfiltered `mmsell_exit_study` sweep both filter on
  `strategy NOT IN (SELECT twin_tag FROM live_paper_twins)`. Twins *do* appear in the daily
  `weather_digest` per-book paper rollup, which is a status read rather than a gate. A twin's own
  read is `live_paper_parity` and nothing else.

## 3. The parity tape

`live_paper_parity_events` records, for every in-band candidate in every cycle, what all three
actors did with the same market at the same instant:

* `parent_*` — the incumbent paper book on the live tag,
* `twin_*` — the fresh twin,
* `live_*` — the real attempt, including **the specific gate that stopped it**
  (`placed`, `gate:open_cap`, `gate:dedup`, `gate:no_balance`, `gate:exposure`, `gate:spread`,
  `gate:daily_loss`, `rejected`, `unknown`, `not_attempted`).

Without this, "live traded 40 fewer trades than paper" is a mystery. With it, it's a sentence:
*31 of them were blocked by the open cap and 9 by per-market exposure.* That is a capacity
difference, **not** evidence about edge — a distinction that has previously been easy to get wrong.

## 4. Reading the result — `live_paper_parity`

```jsonc
{"type": "script", "name": "live_paper_parity", "id": "parity-1"}
{"type": "script", "name": "live_paper_parity", "args": ["--twin", "mmsell10_pt"], "id": "parity-2"}
```

Five sections: epochs, decision alignment, execution realism, the mirage read, anomalies (lead with
anomalies). The verdict distinguishes the two failure modes, which is the whole point:

| verdict | what it means | what to do |
|---|---|---|
| **TOO EARLY** | either side under n=30 settled | keep both running; the epoch *is* the sample |
| **ALIGNED** | twin and live within 0.5¢/contract | paper is a fair model of this book — its paper gates can be trusted for sizing |
| **EXECUTION GAP** | matched markets agree, books diverge | the paper arithmetic is right; live can't capture the trades. Read §2 gates and §3 fill% — usually fill rate, adverse selection, or a cap |
| **ACCOUNTING GAP** | **matched markets themselves disagree** | the simulator is wrong on trades we *did* get. Stop trusting **every** paper book's gate until it's fixed |

**Matched markets is the load-bearing statistic.** Same ticker, same side, same window, both
settled: any per-contract gap there cannot be adverse selection or fill rate, because we got the
trade. It can only be our own accounting — entry price, fee model, or settlement logic. That is the
mirage in its purest form, and it is the one finding here that invalidates work beyond this book.

### Interpretation traps

* **A gate-dominated decision gap is capacity, not edge.** Live trading 60 of its twin's 200
  candidates with `gate:open_cap` on the rest says nothing about whether the edge is real.
* **Fill rate below 100% is expected, not a finding.** The twin exists to price what that costs.
* **`px_gap` (real cost basis − assumed) is an accounting error, not adverse selection.** If we
  systematically fill 1¢ worse than we assume, every paper book is 1¢/contract optimistic.
* **Param drift voids the epoch.** Retuning a live knob mid-run is logged as a `twin` system event
  and surfaced under ANOMALIES. The fix is a **new twin tag** (`X_pt2`), not a quiet re-read — the
  old epoch's numbers describe a configuration that no longer exists.
* **`started_at` never moves**, including across redeploys. A twin tag is single-use.

## 5. Wiring a new book (three hook points)

The harness is book-agnostic; only the tracker integration is per-book. `kalshi_bot/mmsell/tracker.py`
is the reference implementation. In the tracker:

1. **Accept the harness** — `__init__(..., twin_harness=None)`, and in `kalshi_bot/main.py` pass the
   shared `TwinHarness` (built in live mode only) to the tracker.
2. **Add twin books to the book list** — for each `harness.active_specs()` whose `live_tag` is one
   of your books, append a copy of that book's spec with `tag=spec.twin_tag` and
   `twin_of=spec.live_tag`. Put twins **last** so live's decision is already taped when the twin is
   evaluated. Call `harness.ensure_epoch(session, spec, params)` once per cycle before any entry,
   with a params snapshot for drift detection.
3. **Branch on `twin_of` at entry** — price with `live.sizing.maker_no_price` (or your book's live
   price rule), size with `live.sizing.order_quantity`, cap with
   `harness.max_open_positions(<your live cap>)`, write via `harness.open_twin_entry(...)`, and
   **never** call the executor. Feed the recorder: `note_paper` for each book's decision,
   `note_live` with the executor's returned outcome code, then `flush` once per market.

The live entry price/size arithmetic lives in **`kalshi_bot/live/sizing.py`** and is imported by
both the executor and the twin. Keep it that way — the moment the two re-derive it independently,
the parity report starts measuring our own bookkeeping instead of the market.

## 6. Config

| env var | default | meaning |
|---|---|---|
| `LIVE_PAPER_TWIN_ENABLED` | `true` | master switch for the harness |
| `LIVE_PAPER_TWIN_AUTO` | `true` | twin every tag in `LIVE_STRATEGIES` (the standing policy) |
| `LIVE_PAPER_TWINS` | `""` | explicit pairs: `mmsell10` or `mmsell10:mm10shadow`, comma-separated; wins over auto |
| `LIVE_PAPER_TWIN_SUFFIX` | `_pt` | auto twin-tag suffix (tag clamped to 24 chars) |
| `LIVE_PAPER_TWIN_MAX_OPEN_POSITIONS` | `0` | `0` = inherit the live cap (faithful); `>0` bounds the extra paper bookkeeping |
| `LIVE_PAPER_TWIN_PARITY_EVENTS` | `true` | write the per-candidate decision tape |
| `LIVE_PAPER_TWIN_PARITY_MAX` | `400` | tape rows per cycle |

Nothing needs to be set to get a twin: arm a live strategy the usual way and the twin appears. A
twin costs paper bookkeeping and (because the shared paper engine fetches per open trade) a modest
number of extra read-only API calls per management cycle — bound it with
`LIVE_PAPER_TWIN_MAX_OPEN_POSITIONS` if a book's live cap is large.

### Sizing: which knob is which (two different things, easy to conflate)

| knob | what it caps | paper | live + twin |
|---|---|---|---|
| `PAPER_ORDER_SIZE` | contracts per **entry**, paper books | `1` | — |
| `LIVE_MAX_ORDER_DOLLARS` / `MAX_ORDER_SIZE` | contracts per **entry**, live path: `min(floor(dollars / price), MAX_ORDER_SIZE)` | — | `min(floor(5.00/0.93), 1) = 1` |
| `MMSELL_MAX_OPEN_POSITIONS` | concurrent **open positions**, paper books | `200` | — |
| `MMSELL_LIVE_MAX_OPEN_POSITIONS` | concurrent **open positions**, live + twin | — | `60` |

`MAX_ORDER_SIZE` is a per-order contract count, **not** a position count — the position count is the
open-positions cap, a separate knob. At the defaults the live and twin clip is **1 contract**, which
already matches `PAPER_ORDER_SIZE=1`, so all three books use identical clips and both the
per-contract *and* the dollar comparison are apples-to-apples with no config change.

**Fee-rounding consequence, worth knowing before changing the clip.** The Kalshi fee is
`ceil(0.07 · C · P · (1−P))` rounded **up to a whole cent for the whole order**, so a 1-contract clip
is the least fee-efficient size there is. On a mmsell longshot at NO 93¢:

| clip | order fee | fee per contract |
|---|---|---|
| 1 | $0.01 | **1.00¢** |
| 2 | $0.01 | 0.50¢ |
| 5 | $0.03 | 0.60¢ |
| 10 | $0.05 | 0.50¢ |

Paper's reported per-contract P&L is already **net of the 1.00¢** worst case (paper trades at
1-contract clips), so raising `MAX_ORDER_SIZE` to 2+ is worth roughly **+0.4–0.5¢/contract** of pure
rounding relief — material against a book whose realizable edge is ~+1.4¢/contract. Raising it keeps
twin/live parity intact (both read the same knob via `live/sizing.py`) but *does* make the twin's
clip differ from the incumbent paper book's — which is fine, since the twin, not the incumbent, is
the comparison. It also counts as param drift mid-epoch, so change it **before** arming, or start a
new twin tag.

## 7. Cost, and what this does not do

* It does **not** make the twin's fills realistic — that's the point. Fill realism is measured by
  the twin/live gap and modelled separately by `mmsell_fill_model`.
* It does **not** replace the fill model or the exit study. It grounds them: those project a live
  calibration onto paper books, and this measures whether the paper book was ever a fair model to
  project from.
* It adds nothing to the live risk surface. The twin path never touches the executor, and the
  master switches keep their existing meaning.
