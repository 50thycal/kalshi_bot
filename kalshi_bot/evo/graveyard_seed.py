"""Seed corpus for the strategy graveyard: every killed/shelved/pruned strategy from
the repo's research history (docs/RESEARCH_JOURNAL.md, docs/BOOK_REGISTRY.md, thesis
docs, docs/edge_research.md, IDEA_MODEL_* runs), so agents never regenerate dead ideas
without documenting a material difference (spec §31).

Each entry: slug, title, family, outcome, why it failed, revival conditions.
`seed_graveyard()` is idempotent (unique slug)."""

from __future__ import annotations

from sqlalchemy import select

from .models import EvoGraveyardEntry

SEED_ENTRIES: list[dict] = [
    # --- retired/killed paper+live books ---
    dict(
        slug="pin15-endgame-pin",
        title="PIN15: 15-min crypto endgame settlement-average pin",
        strategy_family="obs_pin",
        market_categories="crypto",
        outcome="retired",
        summary="Taker buy of the drift-favored side 2-3min before close on KXBTC15M/"
                "KXETH15M, exploiting the 60s-average settlement. Retired 2026-07-16 at "
                "n=405: target T=120-180s window earned only +0.27c/trade (bar +1.5c); "
                "entire -$17 loss traced to the 60-120s sub-window at -53c/trade.",
        why_failed="The edge did not survive at executable entry timing; the negative-skew "
                   "late sub-window dominated. Pre-registered gate failed outright.",
        revival_conditions="A mechanically different entry that provably lands T>=180s, or a "
                           "new settlement-arithmetic blindness with fresh pre-registration.",
        source_ref="docs/RESEARCH_JOURNAL.md (2026-07-16); docs/PIN15_THESIS.md",
    ),
    dict(
        slug="theta-tail-sell",
        title="THETA family: model-anchored crypto tail-selling (hourly ladders)",
        strategy_family="model_vs_quote",
        market_categories="crypto",
        outcome="shelved",
        summary="Sell model-overpriced 3-40c tails on hourly KXBTC/KXETH ladders. Shelved "
                "2026-07-09: realized tail-hit rates 1.4-2.6x modeled on every book; control "
                "round-tripped +$15.53 -> -$0.07. theta4 (2x fat-tail revival, edge=6c) is the "
                "one pre-registered revival test still trading (gate n>=80).",
        why_failed="Distribution-SHAPE error: the spot-window return model underprices tails "
                   "by a non-constant factor; a parametric widen cannot rescue it.",
        revival_conditions="A genuinely new tail model, fresh pre-registration, calibrated "
                           "(realized-tail-hit <= 1.25x modeled) AND positive at n>=80.",
        source_ref="docs/THETA_THESIS.md; docs/RESEARCH_JOURNAL.md (2026-07-09)",
    ),
    dict(
        slug="tfav-favorite-buy",
        title="TFAV: taker-buy model-underpriced crypto favorites",
        strategy_family="favorite_buy",
        market_categories="crypto",
        outcome="killed",
        summary="Mirror of theta: buy 65-90c favorites whose model P beats the ask. Crossed "
                "its pre-registered n>=150 gate NEGATIVE: -3.6c/trade at n=210.",
        why_failed="The favorite side of the favorite-longshot bias accrues to makers, not "
                   "takers; taker fees + spread eat the edge.",
        revival_conditions="Maker-side expression only, with fill-realism evidence.",
        source_ref="docs/RESEARCH_JOURNAL.md (2026-07-09)",
    ),
    dict(
        slug="wcprop-winner-ladder-lag",
        title="WCPROP: tournament winner-ladder lag after decisive matches",
        strategy_family="lead_lag",
        market_categories="sports",
        outcome="killed",
        summary="Trade KXMENWORLDCUP rungs after KXWCGAME settlements. Armed through the "
                "entire knockout stage: ZERO entries — no rung ever moved >=3c within 45min "
                "of a match settling on a liquid rung.",
        why_failed="Post-match repricing completes inside one cycle or does not happen; the "
                   "winner ladder is efficiently priced.",
        revival_conditions="Evidence of a venue/event where cross-market repricing measurably "
                           "lags at reachable cadence.",
        source_ref="docs/RESEARCH_JOURNAL.md (2026-07-09)",
    ),
    dict(
        slug="xgame-cross-venue-leadlag",
        title="XGAME: in-play Polymarket->Kalshi lead-lag on scoring shocks",
        strategy_family="lead_lag",
        market_categories="sports",
        outcome="killed",
        summary="Buy the lagging Kalshi side after a PM jump. Verdict on ~104 shocks: median "
                "net follow-through -2.0c after costs; PM->K 60% vs K->PM 59% — symmetric.",
        why_failed="Both venues track the shared live game feed; there is no leading venue. "
                   "Structural finding that transfers across sports.",
        revival_conditions="A genuinely private/faster feed, not venue-vs-venue latency.",
        source_ref="docs/RESEARCH_JOURNAL.md (2026-07-09); docs/IDEA_MODEL_20260704.md",
    ),
    dict(
        slug="weather-books-pruned",
        title="Weather base books: fav/nws/cal/dist/cwin/favband/obs/pm (+ all lows)",
        strategy_family="model_vs_quote",
        market_categories="weather",
        outcome="neg_ev",
        summary="Eight weather books pruned 2026-07-04 with ~-$235 collective paper bleed "
                "(-3.7 to -8.9c/trade; fav -4.1c/1196, nws -5.6c/1146, cal -6.3c/1137, dist "
                "-4.0c/669...). Low-temperature program separately ~-$88 with no +EV sub-book. "
                "Only the consensus book (weather_con, +4.1c/239 at prune) survived.",
        why_failed="Single-signal forecast-vs-market books lose to fees+spread; the market's "
                   "ladder already prices the public forecasts.",
        revival_conditions="Multi-family convergence (the con pattern) or a new data source "
                           "with demonstrated latency advantage.",
        source_ref="docs/RESEARCH_JOURNAL.md (2026-07-04 prune)",
    ),
    dict(
        slug="scanner-ta-books",
        title="Scanner TA books: momentum / reversion / buy_favorite",
        strategy_family="price_history",
        market_categories="all",
        outcome="neg_ev",
        summary="The original generic scanner paper books lost ~-5c/contract over 1,000+ "
                "trades — the round-trip spread+fee cost floor.",
        why_failed="Price-history-only signals on mature markets carry no edge; costs dominate.",
        revival_conditions="None as-is. Requires an information edge, not chart shapes.",
        source_ref="docs/RESEARCH_JOURNAL.md (earliest entries)",
    ),
    # --- probe-stage kills (never traded) ---
    dict(
        slug="decay-deadline-hazard",
        title="DECAY: short YES on by-date 'hope' markets at T-14d",
        strategy_family="structural",
        market_categories="science_tech",
        outcome="killed",
        summary="Shorting YES 5-20c by-date markets = -19.97c/contract over n=152: Sci/Tech "
                "by-date markets resolve YES 94% of the time — 'hope' is UNDER-priced.",
        why_failed="The premise inverted: deadline hazard premium does not exist; the crowd "
                   "underprices resolution.",
        revival_conditions="Category-level base-rate evidence that a specific by-date family "
                           "over-prices YES.",
        source_ref="docs/RESEARCH_JOURNAL.md (2026-07-10)",
    ),
    dict(
        slug="pinned-post-pin-discount",
        title="PINNED: buy the discounted side after an outcome is observably pinned",
        strategy_family="obs_pin",
        market_categories="all",
        outcome="killed",
        summary="Pooled +1.80c (n=119) under the +3c bar and does NOT beat the pre-pin "
                "favorite-buy control (+2.23c). AAA-gas cell perfectly efficient (+0.02c).",
        why_failed="Post-pin discounts are already harvested; the residual (jobs-print "
                   "+43.4c, n=36) was logged as non-promotable residual, not an edge.",
        revival_conditions="A settlement-arithmetic blindness the counterparty demonstrably "
                           "does not compute (the PIN15-style shape) in a NEW mechanism.",
        source_ref="docs/RESEARCH_JOURNAL.md (2026-07-10)",
    ),
    dict(
        slug="mlbwx-rain-runs",
        title="MLBWX: rain-forecast staleness into MLB run totals",
        strategy_family="model_vs_quote",
        market_categories="sports,weather",
        outcome="killed",
        summary="Short the Over on rain-shortened-game risk: -1.5c/ct (n=44) vs a +4c bar. "
                "v1 showed a fake +5.5c from a lookahead bug (caught because BOTH sides of "
                "every game showed profit).",
        why_failed="No stale-weather edge in run totals; also a standing lesson in lookahead "
                   "bugs.",
        revival_conditions="A verified-latency weather feed materially ahead of the books.",
        source_ref="docs/RESEARCH_JOURNAL.md (2026-07-09)",
    ),
    dict(
        slug="dutch-book-arb",
        title="Dutch-book arbitrage scanner across MECE event sets",
        strategy_family="structural",
        market_categories="all",
        outcome="killed",
        summary="882 events scanned: 0 real arbs; all 15 apparent hits were parser artifacts "
                "(dropped minus signs, K/M/B units). Liquid MECE sets sit at the no-arb "
                "boundary.",
        why_failed="Kalshi MECE pricing is internally consistent; apparent arbs are data bugs.",
        revival_conditions="None on-venue. (Cross-venue arb is a different, geofence-blocked "
                           "question.)",
        source_ref="docs/edge_research.md",
    ),
    dict(
        slug="flb-taker",
        title="FLB taker: back the favorite as a taker",
        strategy_family="favorite_buy",
        market_categories="all",
        outcome="killed",
        summary="Favorite-longshot bias is real at the calibration level but taker capture "
                "collapses: the 65-80c 'pocket' fell to +0.004/trade at n=280 — the 5th "
                "small-n mirage. Positive only at T-30 (already-decided artifact).",
        why_failed="The bias accrues to makers; taker spread+fees consume it.",
        revival_conditions="Maker-side only (see mmsell family), with fill realism measured.",
        source_ref="docs/edge_research.md",
    ),
    dict(
        slug="weather-maker-bid",
        title="Weather maker: rest bids under the favorite",
        strategy_family="maker",
        market_categories="weather",
        outcome="killed",
        summary="Buy-at-bid looked +1c until conditioned on filling: filled win% 47% vs 60% "
                "unconditional — adverse selection makes it realistically -8.6c/trade.",
        why_failed="Passive orders on informative flow are adversely selected.",
        revival_conditions="A fill model that demonstrates selection-free fills (e.g. "
                           "scheduled-settle series), pre-registered.",
        source_ref="docs/edge_research.md",
    ),
    dict(
        slug="xvenue-divergence",
        title="Cross-venue Kalshi-Polymarket price divergence",
        strategy_family="lead_lag",
        market_categories="all",
        outcome="killed",
        summary="Divergence ~1-1.6c vs 2-4c round-trip cost; xvenue_shock found no tradeable "
                "lead-lag.",
        why_failed="Sub-cost divergence; same shared-information structure as XGAME.",
        revival_conditions="A venue pair with structurally different participants and >4c "
                           "persistent divergence.",
        source_ref="docs/edge_research.md",
    ),
    # --- backfill structural probes (all priced/refuted) ---
    dict(
        slug="backfill-structural-probes",
        title="Nine structural probes on weather history (overround, persistence, longshot, "
              "momentum, W->E lead-lag, diurnal, liquidity...)",
        strategy_family="structural",
        market_categories="weather",
        outcome="killed",
        summary="All nine complete: overround DEAD (0% offer credit); persistence PRICED; "
                "mean-reversion real but +0.4c sub-fee; longshot tails well-calibrated; "
                "momentum refuted (autocorr -0.03); W->E physics real but market error corr "
                "-0.03 (fully priced); diurnal coupling priced (corr 0.97); liquidity-"
                "conditioned mispricing refuted (mid honest at zero volume); distribution "
                "overdispersion REAL (SD(z)=0.78) but sub-cost — kept as a model feature.",
        why_failed="The weather ladder is structurally efficient at the fee scale.",
        revival_conditions="Use overdispersion/mean-reversion as MODEL FEATURES inside a "
                           "multi-signal book, never as standalone trades.",
        source_ref="docs/RESEARCH_JOURNAL.md (backfill probes)",
    ),
    # --- holds (not dead, but blocked: do not naively re-propose) ---
    dict(
        slug="freeze-compin-seasonpin-holds",
        title="FREEZE / COMPIN / SEASONPIN: pre-registered UNTESTABLE holds",
        strategy_family="obs_pin",
        market_categories="commodities,sports",
        outcome="shelved",
        summary="FREEZE (dark-window commodity freezes): hub settles on 24/7 Pyth; 0 settled "
                "grain/soft markets — revisit when settled counts reach hundreds. COMPIN "
                "(TWAP/average pinning): 0 settled average markets at census. SEASONPIN: MLB 0 "
                "settled rungs, WNBA exactly at the n=40 floor. All carry named revisit "
                "triggers; data absence is a HOLD, not a kill.",
        why_failed="Not failed — untestable now. Regenerating these without new settled data "
                   "is a graveyard conflict.",
        revival_conditions="The pre-registered revisit triggers in each thesis doc.",
        source_ref="docs/FREEZE_THESIS.md; docs/COMPIN_THESIS.md; docs/SEASONPIN_THESIS.md",
    ),
    # --- meta-lessons (searchable as graveyard context) ---
    dict(
        slug="meta-lessons",
        title="Meta-lessons: the evolved prior set from 7 idea-model runs",
        strategy_family="meta",
        market_categories="all",
        outcome="killed",
        summary="(1) Price-history-only edges on mature markets are dead. (2) Observation-"
                "pinning survives; model-vs-quote keeps dying — prefer signals deterministic "
                "about the outcome. (3) The surviving off-weather shape is MECHANICS "
                "BLINDNESS (settlement arithmetic the counterparty doesn't compute). "
                "(4) Sports reaction/latency plays are structurally dead (shared feed). "
                "(5) Adverse selection kills passive-on-informative; FLB is harvestable only "
                "maker-sell in the cheapest band. (6) Edges are cell-concentrated — averaging "
                "across cells destroys them. (7) Small-n mirages and lookahead bugs are the "
                "top process risk (fooled ~5x). Idea-model base rate: 11 promotions -> 1 "
                "gate-passing book; testability-NOW is the highest-leverage screen.",
        why_failed="n/a — process knowledge distilled from the graveyard.",
        revival_conditions="n/a",
        source_ref="docs/IDEA_MODEL_SCORECARD.md; docs/IDEA_MODEL_20260712.md",
    ),
    # --- retired 2026-08-12 (the closeout pass; see docs/BOOK_REGISTRY.md) ---
    dict(
        slug="mmsell-wide-band-type-slicing",
        title="Wmmsell1-8: contract-TYPE slicing of the mmsell WIDE band (5-40c)",
        strategy_family="maker_sell",
        market_categories="sports,all",
        outcome="retired",
        summary="Eight books slicing the wide maker-sell band by contract structure (in_play, "
                "player_prop, price_strike, mention, ...). Retired 2026-08-12 as UNMEASURABLE, "
                "not disproven: their control `mmsell` LOSES money in the wide band, and live "
                "fill coverage is only 19-41% because our maker-fill calibration comes entirely "
                "from CHEAP-band orders. The same type axis runs on in the TIGHT band "
                "(Tmmsell1-6) at 99-100% coverage.",
        why_failed="No live fill evidence exists for 10-40c maker entries, so paper's "
                   "always-fills assumption is doing the work for most of every book. A larger "
                   "paper sample would only produce a more confident version of an unfounded "
                   "number. Beating a losing control is also not an edge.",
        revival_conditions="Live maker fills in the 10-40c band. Evidence, not more paper — "
                           "the fill assumption is precisely what is in question.",
        source_ref="docs/MMSELL_TYPE_BOOKS.md; docs/MMSELL_FILL_MODEL.md; docs/BOOK_REGISTRY.md",
    ),
    dict(
        slug="mmsell-bid-triggered-stop-loss",
        title="mmsellA1/A2/A3: confirmed yes-BID stop-loss on the mmsell10 entry (L12/L20/L30)",
        strategy_family="maker_sell",
        market_categories="sports,crypto",
        outcome="killed",
        summary="Exit the short cheap tail when the yes-BID holds at/above 12, 20 or 30c for 2 "
                "consecutive cycles. Pre-registered gate FAILED on both halves at every level: "
                "A1 -4.16c/trade vs the mmsell10 control's +3.14c, and 5th-pctile -19.0 vs "
                "+5.0 — the stop makes the TAIL WORSE, which is the opposite of its purpose.",
        why_failed="The stop fires on 52% of positions. At that rate it is not truncating a "
                   "rare disaster, it is converting ordinary winners into realized losses: a "
                   "cheap tail that ticks up usually still expires worthless. The motivating "
                   "backtest was measured on htc<1h CRYPTO (continuous paths, where a stop "
                   "exits near its trigger) while mmsell trades htc>=1h SPORTS, which JUMP "
                   "straight through a trigger on a score. Different population.",
        revival_conditions="A stop whose trigger cannot fire on a non-informative quote, with "
                           "fresh pre-registration. NOT a re-sweep of levels — the level was "
                           "never the problem, the exit mechanic is.",
        source_ref="docs/MMSELL_ANCHOR_SET.md; docs/MMSELL_CRYPTO_STUDY.md",
    ),
    dict(
        slug="mmsell-band-variants-first-cohort",
        title="mmsell1/2/3/4/11: first-cohort mmsell band + filter variants",
        strategy_family="maker_sell",
        market_categories="sports,all",
        outcome="retired",
        summary="Band variants (5-20, 10-20, 5-10), a series skip-list, and a no-late-entry "
                "filter. Retired 2026-08-12 as indistinguishable from zero on the "
                "fee-normalized fill model: mmsell3 +0.02c, mmsell11 +0.04c, mmsell4 +0.04c. "
                "mmsell3 is the one with LIVE ground truth — it traded real money 2026-07-13/19 "
                "and realized +0.18c/trade at n=359, i.e. paper and live agree it is breakeven. "
                "mmsell1/mmsell2 additionally sit at 49.6%/19.5% fill coverage. Superseded by "
                "mmsell10 (maxyes=7 price ceiling): 99.8% coverage, +2.19c realizable.",
        why_failed="Breakeven, not losing — but breakeven does not earn capital. The price "
                   "CEILING, not the band width or the filters, is what makes a cheap-tail "
                   "book fillable, and that is the whole difference.",
        revival_conditions="A mechanically different entry, pre-registered. Not a re-read of "
                           "this data: it has been read against two fee models and a live "
                           "postmortem and says the same thing each time.",
        source_ref="docs/MMSELL_VARIANTS_THESIS.md; docs/MMSELL_LIVE_POSTMORTEM.md",
    ),
    dict(
        slug="weather-consensus-book",
        title="weather_con / weather_concity: multi-family consensus temperature pick",
        strategy_family="model_vs_quote",
        market_categories="weather",
        outcome="retired",
        summary="Buy the bucket where independent forecast families (NBM, ensemble, "
                "observations, Polymarket) agree. Retired 2026-08-12 at n=775, -3.50c/trade, "
                "-$27.14. Its city-restricted A/B (weather_concity) was RETIRED WITH IT at "
                "n=203, -8.29c/trade — restricting to the historical 'edge cities' was more "
                "than TWICE as bad as taking all of them. Weather books enter at the ask, so "
                "the 2026-08-11 maker-fee correction does not flatter these numbers.",
        why_failed="Consensus among correlated forecast families is not independent "
                   "confirmation. The concity result is the sharper lesson: with n=23-63 per "
                   "city the per-city ranking was NOISE, so selecting the top cells bought the "
                   "sampling error rather than the signal.",
        revival_conditions="A forecast edge demonstrated OFFLINE against "
                           "weather_forecast_outcomes (the persisted forecast->settlement "
                           "dataset, which collection continues to feed), pre-registered, "
                           "before any weather book re-enters.",
        source_ref="docs/RESEARCH_JOURNAL.md (2026-08-12); docs/BOOK_REGISTRY.md",
    ),
    dict(
        slug="slice-a-losing-book-by-its-own-history",
        title="PROCESS: rescuing a losing book by slicing on a dimension from its own history",
        strategy_family="process",
        market_categories="all",
        outcome="killed",
        summary="The recurring shape behind weather_concity (-8.29c vs the -3.50c parent it was "
                "meant to rescue) and the wide-band type books. A losing book is examined "
                "per-cell, some cells look positive, and a new book is built from the winners. "
                "Forward performance is then WORSE than the parent, not better.",
        why_failed="The per-cell ranking of a low-n book is mostly sampling noise, so the "
                   "selected cells are the upper tail of a null distribution. Selecting on them "
                   "buys the noise; the apparent edge cannot survive out of sample because it "
                   "was never in the sample.",
        revival_conditions="Cell selection is legitimate only when the cells are chosen on a "
                           "PRIOR (a mechanism argued in advance) and tested out-of-sample, or "
                           "when per-cell n is large enough that the ranking is not noise. "
                           "Never re-slice the same history that produced the hypothesis.",
        source_ref="docs/RESEARCH_JOURNAL.md (2026-08-12); docs/MMSELL_TYPE_BOOKS.md",
    ),
]


def seed_graveyard(session) -> int:
    """Insert any missing seed entries. Idempotent on slug. Returns rows added."""
    existing = set(session.scalars(select(EvoGraveyardEntry.slug)).all())
    added = 0
    for entry in SEED_ENTRIES:
        if entry["slug"] in existing:
            continue
        session.add(EvoGraveyardEntry(source="docs_seed", **entry))
        added += 1
    session.flush()
    return added
