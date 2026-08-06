# Fee reconciliation — paper's fee model vs what Kalshi actually billed

**Command:** `{"type": "script", "name": "mmsell_fee_recon"}` — `scripts/mmsell_fee_recon.py`

## The question

`paper/engine.py:kalshi_fee` applies one formula to every book — `ceil(0.07 × C × P × (1−P))`,
Kalshi's **taker** rate. But mmsell and theta enter as pure makers (`post_only: true`), and the
published maker rate is a quarter of that (`0.0175`). `docs/MMSELL_ROADMAP.md` flagged the
mismatch as worth ~1¢/contract and asked for confirmation against a real statement before acting.

This is that confirmation, measured off `fills.fee` — the amount Kalshi actually billed, carried
through from `/portfolio/fills`. Not our bookkeeping: their number.

## Result (2026-08-06)

| book | entry | contracts | **Kalshi billed** | paper books | overcharge |
|---|---|---|---|---|---|
| `mmsell3` | maker | 366 | **0.013¢** | 1.003¢ | **+0.990¢** |
| `mmsell10` | maker | 130 | **0.009¢** | 1.000¢ | **+0.991¢** |
| `theta4` | maker | 69 | **0.000¢** | 1.348¢ | **+1.348¢** |
| `mmsell10b` | maker | 4 | 0.022¢ | 1.000¢ | +0.978¢ |
| `weather_fav_h14` | taker | 60 | 1.407¢ | 1.833¢ | +0.426¢ |
| `weather_fav_h20` | taker | 58 | 1.576¢ | 2.000¢ | +0.424¢ |

**Maker-entry books: 569 contracts — Kalshi billed 0.010¢/ct, paper books 1.044¢/ct →
overcharge +1.034¢/contract.**

**Taker-entry control: 237 contracts — billed 1.244¢/ct, paper books 1.768¢/ct →
overcharge +0.524¢/contract.**

The weather books are the **control**, and they are what makes this conclusive. If our fee
arithmetic were simply wrong, they would be off too. They land within ~25% of the model while the
maker books are off by ~100×. The gap is maker-vs-taker, not bad arithmetic.

Two distinct effects, worth separating:

* **Maker fills cost essentially nothing** — 0.010¢/contract, *below* even the published maker
  formula (~0.11¢ at these prices). The likeliest reading is that these series are not in the fee
  schedule's "Maker Fees" section at all, and the few cents that do appear are the handful of
  fills that crossed and paid taker.
* **The taker control's +0.52¢ is a clip-size artifact, not a rate error.** Paper ceils per trade
  at qty=1; real fills batch ~5 contracts, so the ceiling amortizes. A genuine 1-contract taker
  *would* pay the full 1¢ paper models.

**The roadmap's ~1¢ estimate is confirmed.** An earlier reading in this session — that the
per-trade round-up erases the maker discount and so both routes cost the same — was **wrong**. It
assumed a maker fee is charged and then ceiled; in reality it is ~0.

## What it changes

Every mmsell/theta paper number is **~1.03¢/contract too pessimistic** at 1-contract clips. The
correction is uniform, so it changes **no ranking** — but every gate stated in absolute cents is
being judged against a number that is a full cent too harsh:

| book | realizable as stated | corrected | reading changes? |
|---|---|---|---|
| `mmsell10` | +1.40¢ | **+2.40¢** | stronger live candidate |
| `mmsell3` | −1.06¢ | **−0.06¢** | "MIRAGE" → ~breakeven |
| `mmsell6` | −0.43¢ | **+0.57¢** | "MIRAGE" → thin positive |

It also **raises the bar for the taker thesis** (`docs/MMSELL_TIMING_STUDY.md`). A taker really
would pay the taker fee a maker avoids entirely, so the endgame comparison is
+4.06¢ taker vs +1.53¢ maker rather than +4.06¢ vs +0.50¢. Taking still wins there by ~2.5¢, but
by half the margin previously claimed.

## Why the engine is not patched

Tempting, and wrong right now. The correction is **uniform**, so it preserves every ranking, while
changing `kalshi_fee` mid-flight would split each book's history across two fee models. Fourteen
`Wmmsell*`/`Tmmsell*` books are currently accumulating toward n≥100–150 settled gates; a change now
would make the first half of each book non-comparable with the second — precisely the confound
those gates exist to avoid.

**The correction belongs in the analysis layer until those gates close.** When they do, patch
`kalshi_fee` to charge the maker rate for `post_only` books and re-baseline the absolute-cent
gates in `docs/BOOK_REGISTRY.md` in the same change.

## Caveats

* n=569 maker contracts across two live epochs. Solid for a ~100× effect, thin for precision.
* `fills` has no `is_taker` flag — we infer entry style from the book's known order construction
  (`post_only: true` for mmsell/theta). A maker book whose order accidentally crossed would be
  counted as a maker fill; that is the most likely source of the residual 0.010¢.
* Fee schedules change. Kalshi moved maker fees from a flat $0.0025/contract to a
  probability-scaled formula in July 2025; re-run this after any schedule update.
