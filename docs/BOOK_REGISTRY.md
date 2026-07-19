# Book registry — every trading book the worker runs

**Canonical index of every book that writes `paper_trades` (or trades live).** The strategy
status loop reconciles the live `paper_trades.strategy` rows against this table every run: a
`book:<tag>` the query finds with **no row here is UNTRACKED** — the loop flags it (a book must
never trade without its rationale + pre-registered gate written down where the loop can see it).

**Who maintains this:** whoever builds or changes a book. The `kalshi-strategy` skill's Phase 5
(paper trade) REQUIRES adding a row here the moment a new book starts writing `paper_trades`, and
Phase 6 (live) REQUIRES updating its `status`. This file lives on the default branch (unlike the
loop's `STRATEGY_LOOP_STATUS.md`, which lives on `strategy-loop-status` and is loop-owned), so a
build/fable session can update it in the same PR that adds the book. Newest books at the top.

The `tag` column MUST match `paper_trades.strategy` exactly (or its prefix for a variant family),
so the loop can join on it. Keep the gate one line; the linked thesis doc carries the full detail.

| tag | status | thesis / rationale | one-line edge | pre-registered gate / kill criteria |
|---|---|---|---|---|
| `pin15` | **paper** (built 2026-07-11) | `docs/PIN15_THESIS.md` | 15-min crypto endgame observation-pin: taker-buy the drift-favored side 2–3 min before close, held to the 60s-average settle | at **n≥150 settled**, KEEP only if per-trade **> +1.5¢** AND the positive P&L concentrates in the T≈120–180s entries (slice by the `T-at-entry` recorded in `fill_assumption`); if it only earns at T<60s or nets ≤0, the ~300s loop can't capture it → shelve |
| `theta4` | **paper** (live variant; rest of theta family collect-only) | `docs/THETA_THESIS.md` | fat-tail (×2.0) revival of theta tail-sell, edge=6¢, cheap band / final 35 min | at **n≥80 settled**, KEEP only if per-trade **> 0** AND realized-tail-hit **≤ 1.25× modeled**; fail both → theta family fully dead |
| `theta`, `theta1`, `theta2`, `theta3` | paper, **collect-only (shelved)** | `docs/THETA_THESIS.md` | model-anchored crypto tail-sell (control + revisions) | SHELVED 2026-07-09 (failed "positive AND calibrated at n≥60"); collectors kept, entries off |
| `mmsell`, `mmsell1`, `mmsell2`, `mmsell3` | **paper** (`mmsell3` LIVE, real money) | `docs/RESEARCH_JOURNAL.md`, `docs/edge_research.md`, `docs/MMSELL_LIVE_PLAN.md` | favorite-longshot maker-sell in the cheap band (mostly sports) | `mmsell3` passed its paper gate + went LIVE 2026-07-13 (V2 events endpoint). Live read: non-WC book +5.6¢/96% win, dragged to ~breakeven by World Cup (−9.9¢) — see `mmsell4-8` and `docs/MMSELL_VARIANTS_THESIS.md` |
| `mmsell4`, `mmsell5`, `mmsell6`, `mmsell7`, `mmsell8` | **paper** (built 2026-07-15) | `docs/MMSELL_VARIANTS_THESIS.md` | 5 variants from the live+paper by-sport / by-market-type decomposition (clean-book, type-allowlist, ultra-cheap, short-dated, scheduled-settle) | each has its own PROMOTE/KILL gate at n≥150 (mmsell5/8 at n≥100) — full per-variant gates in `docs/MMSELL_VARIANTS_THESIS.md`. Headline: PROMOTE the one that beats `mmsell3` per-trade; KILL any that fall below it. mmsell8's key read = live win% within 1pp of paper (adverse-selection isolator) |
| `mmsell9`, `mmsell10`, `mmsell11` | **paper** (built 2026-07-18) | `docs/MMSELL_VARIANTS_THESIS.md` (2nd cohort) | from the live 2×2 (price×type, n=232): cheap (yes≤7¢) × non-winner is the +EV cell; rich h2h-winners are the drag. mmsell9=sweet-spot cell, mmsell10=entry-price ceiling only (`maxyes`), mmsell11=no-late-entry (`htcmin=6`) | gates at n≥150 (mmsell9 n≥100), full detail in the thesis doc. **mmsell10 is the highest-value read** — a price ceiling is promotable straight into the live mmsell3 entry if it beats the control |
| `weather_con` | **paper** (only historically +EV book) | `docs/RESEARCH_JOURNAL.md` | synoptic-temperature consensus pick | ongoing; watch it stays net-positive as its own n grows |
| `weather_concity` | **paper** (A/B of con: AUS/CHI/NYC) | `docs/RESEARCH_JOURNAL.md` | con restricted to the three by-city edge cells | at **n≥120 settled**, KEEP (and consider retiring all-city con) only if it beats all-city con |
| `weather_*` (other cells) | paper, **pruned / wound down** | `docs/RESEARCH_JOURNAL.md` | legacy weather cells (directional, favband, etc.) | pruned 2026-07 — net −EV; 0 open |
| `tfav` | **KILLED / disabled** | `docs/RESEARCH_JOURNAL.md` | buy model-underpriced hourly-crypto favorites | KILLED 2026-07-09 (−3.6¢ at n≥210); re-enable only under fresh pre-registration |
| `wcprop` | **KILLED / disabled** | `docs/RESEARCH_JOURNAL.md`, `docs/IDEA_MODEL_20260704.md` | World Cup winner-ladder lag after a decisive result | KILLED (no repricing lag); WC window closed |
| `xgame` | **collector only / book KILLED** | `docs/IDEA_MODEL_20260704.md` | cross-venue in-play lead-lag (PM leads, Kalshi lags) | book KILLED (P3 symmetric — both venues track the shared feed); collector-only |

_Provenance note: this registry was introduced 2026-07-11 after a `pin15` book appeared from a
parallel build session and the loop had to reverse-engineer its rationale — the exact failure this
file exists to prevent. Backfilled from `RESEARCH_JOURNAL.md` / `THETA_THESIS.md` / the config._
