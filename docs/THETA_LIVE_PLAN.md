# THETA4 — live real-money test plan (staged, pre-registered)

> **This plan mirrors `docs/MMSELL_LIVE_PLAN.md` end to end** — same maker convention, same twin
> harness, same ops-channel arm/audit procedure. Read that doc's §2/§9 for the parts that apply
> unchanged; this doc focuses on what's specific to theta.

> **Runs with a PAPER TWIN in parallel from day one**, exactly like mmsell10. Arming
> `LIVE_STRATEGIES=theta4` automatically starts a fresh paper book `theta4_pt` at the same instant,
> seeing the same candidates (identical band/edge/window/vol-multiplier gates — a `dict(parent)`
> copy of theta4's own paper spec) but priced/sized by the **live** knobs
> (`THETA_LIVE_MAX_ORDER_DOLLARS`/`THETA_LIVE_MAX_CONTRACTS` sizing, `THETA_LIVE_MAX_OPEN_POSITIONS`
> cap, live spread gate). The twin is exempt from `theta_collect_only`'s variant-shelving gate — it
> isn't a shelved revival experiment, it's the paper shadow of the tag that's now armed live.
> Mechanism, gates and traps: `docs/LIVE_PAPER_TWIN.md`; arm-and-audit procedure: the
> `live-paper-parallel` skill. The read is `{"type":"script","name":"live_paper_parity"}` — its
> **matched-market** rows are the load-bearing statistic (see mmsell's plan §2 header note for why).

*Plan written 2026-07-30, before any live theta order is placed. The sizing, gates and kill
criteria below are **pre-registered** so the test can't be quietly re-scoped after the fact.*

The one number that matters is still the north star: **+$100/month realized across the portfolio.**
This test does not chase that — it answers the single question that stands between theta4's paper
gate pass and ever contributing to it: **does the maker edge survive real fills, the same question
mmsell3 answered "no" to on its first live pass.**

---

## 1. Why theta4, and what paper already proved

theta4 is the fat-tail (×2.0 vol multiplier) revival of the theta thesis — sell YES on
model-overpriced crypto tail options (buy NO at the no-bid), in the cheap band (yes 3–20¢) inside
the final 35 minutes to settlement, only where the model's excess (mid − 100·P_model) clears 6¢.
It **cleared its pre-registered paper gate 2026-07-28** (`docs/BOOK_REGISTRY.md`: n≥80 settled, KEEP
only if per-trade > 0 AND realized-tail-hit ≤ 1.25× modeled) at n=95, and continues to hold at a
larger sample:

| metric | value (n=114, pulled 2026-07-30) |
|---|---|
| settled trades | 114 |
| avg P&L/trade | **+$0.392** |
| total realized (paper) | **+$44.70** |

Its siblings (`theta`, `theta1`, `theta2`, `theta3`) were SHELVED 2026-07-09 for failing the same
gate and giving back a full calm-streak gain live (control +$15.53 → −$0.07 across two windows,
realized tails 1.4–2.6× the model) — theta4 is a single pre-registered attempt to fix that specific
failure mode (fatten the model's tail ~2× to match the realized miss), not a fresh, unrelated claim.

## 2. The only thing this test can prove that paper cannot

Exactly mmsell's §2, restated for theta: paper **assumes the resting no-bid fills for free.**
theta uses the **identical maker-sell mechanism** as mmsell (`kalshi_bot/theta/tracker.py`'s entry
comment: *"the mmsell maker convention the mmsell book uses"*), so it is exposed to the same two
failure modes paper is structurally blind to — fill rate/queue position, and adverse selection
(getting filled exactly when an informed taker is lifting the other side).

**This is not hypothetical for theta.** `docs/THETA_FILL_MODEL.md` (built 2026-07-29, before any
theta live data existed) already ran theta4's paper distribution through mmsell3's *borrowed* live
fill calibration — the only maker-sell book with real fill history — and found the priced slice
(28% coverage, the yes 6–12¢ cell) collapses from **+38.62¢ optimistic to +0.51¢ realizable**, the
same order-of-magnitude mirage that hit mmsell3. The other 72% of theta4's trades sit in price
cells mmsell3 never filled live, so the borrowed read **cannot confirm or refute most of the book**
— it's a caution, not a verdict. **That doc's own conclusion is what this plan executes**: *"the
real fix is theta-specific live data... would populate theta's own calibration and replace the
borrowed read with ground truth."* Once any `theta*` strategy has live fills,
`scripts/theta_fill_model.py`'s `_load_own_calibration()` activates automatically — no code change
needed — and the caution becomes a real answer.

## 3. What was built

Mirrors `MMSELL_LIVE_PLAN.md` §3 exactly, adapted to theta's own knobs rather than reusing
mmsell's — **deliberately not shared**, because two live books now run at once and a shared
dollar/contract cap or open-position/spread gate would mean resizing one silently resizes or gates
the other (see `kalshi_bot/live/sizing.py`'s module docstring for the parameterization this forced).

1. `LiveExecutor.mirror_theta_entry` — same V2 maker order shape as `mirror_mmsell_entry`
   (`side="ask"`, `post_only`, price = `(100 − no_price)/100` dollars), gated on its own switches +
   allowlist, per-ticker dedup, daily-loss, real balance, per-market exposure, and **theta-scoped**
   `THETA_LIVE_MAX_OPEN_POSITIONS` / `THETA_LIVE_MAX_SPREAD_CENTS` (not mmsell's).
2. `LiveExecutor.close_theta_positions` — the same one-shot end-of-strategy closeout
   `close_mmsell_positions` has (`THETA_CLOSEOUT_ENABLED`/`THETA_CLOSEOUT_STRATEGIES`/
   `THETA_CLOSEOUT_SLIPPAGE_CENTS`), wired into the same live-cycle call site.
3. `ThetaTracker` now accepts `live_executor`/`twin_harness`, mirrors an allowlisted entry to the
   executor right after `create_paper_trade`, and runs the twin the same way `MmSellTracker` does
   (twin books appended to `_books()`, sharing theta4's own band/edge/window/multiplier gates, only
   diverging on entry price/size). The twin is explicitly exempted from `theta_collect_only`'s
   shelving gate (§ below) — everything else in theta's model-probability/edge computation is
   shared, unmodified code.
4. `kalshi_bot/live/sizing.py`'s `maker_no_price`/`order_quantity` were parameterized (explicit
   price-offset/dollar-cap/contract-cap arguments, no implicit `settings` read) so both books can
   use the same tested arithmetic without being able to silently borrow each other's knobs.
   mmsell10's live values are unchanged by this — verified with a byte-for-byte regression test
   before merge.

**Collect-only interaction (theta-specific, no mmsell equivalent).** Production theta runs with
`theta_collect_only=True` and only `theta_live_variants` (currently `"theta4"`) trading — the rest
of the family snapshots only. Without an explicit exemption, the twin's own tag (`theta4_pt`) is
not a member of that set and would be silently shelved the instant it's created, starving the
parity read in exactly the operating mode this test runs in. `ThetaTracker` exempts any book with
`twin_of` set from the collect-only gate; `tests/test_theta_live_twin.py` pins this.

Ships **inert**: nothing places an order until an operator flips `LIVE_ENABLED`/`KILL_SWITCH` and
lists `theta4` in `LIVE_STRATEGIES` — identical safety posture to mmsell10.

## 4. Config — Stage 1

Pre-registered live knobs. Entry style is **rest at the no-bid / join the queue** — faithful to
paper, so the measured fill rate is the real one.

| knob | value | rationale |
|---|---|---|
| `BOT_MODE` / `LIVE_ENABLED` / `KILL_SWITCH` | `live` / `true` / `false` | the three master switches |
| `LIVE_STRATEGIES` | `theta4` (added to whatever else is already live, e.g. `mmsell10`) | allowlist — does not touch mmsell10's own arming |
| `THETA_LIVE_PRICE_OFFSET_CENTS` | `0` | join the queue at the no-bid |
| `THETA_LIVE_MAX_ORDER_DOLLARS` | `3.0` | starting dollar cap |
| `THETA_LIVE_MAX_CONTRACTS` | `5` | matches paper's own `theta_order_size=5` (chosen there to amortize the fee ceiling) — live is sized the same way from day one, unlike mmsell10's initial 1-contract start |
| `THETA_LIVE_MAX_OPEN_POSITIONS` | `15` | conservative pilot cap, well under paper's 60 |
| `THETA_LIVE_MAX_SPREAD_CENTS` | `40` | generous sanity guard, matches mmsell's |
| `LIVE_ORDER_TIMEOUT_SECONDS` | shared global (currently 20 days, raised for mmsell10 — see `docs/LIVE_PAPER_TWIN.md`) | **theta doesn't need this raised**: its markets settle within the hour by construction (`theta_entry_max_minutes=55`), so the order either fills or the market closes long before any timeout — the shared value is harmless here, not a decision this plan needs to make |
| `MAX_MARKET_EXPOSURE` / `MAX_DAILY_LOSS` / `LIVE_KILL_ON_DAILY_LOSS` | existing shared values | same portfolio-level breakers as every other live book |

At `$3.00` and a typical NO entry ~75–95¢, this sizes to **3–4 contracts**, i.e. ~$2.25–3.80 per
position; at the 15-position cap that's roughly **$35–55 deployed capital** for the pilot — smaller
than mmsell10's live footprint, appropriate for a book whose fill-model caution is a live open
question, not a confirmed edge.

## 5. Metrics captured

The generic `live_paper_parity` report (zero theta-specific code — it reads any pair from
`live_paper_twins`) is the primary read: decision alignment (twin vs live overlap, gated on the
exact reason live didn't follow), execution realism (fill rate, price gap), and the matched-market
mirage/execution-gap split. Alongside it:

- `theta_fill_model` — the moment theta4 has live fills, `_load_own_calibration()` switches from
  borrowed mmsell3 numbers to theta's own, upgrading §2's caution into a real realizable-¢/trade
  read across theta4's full price mix, not just the 28% mmsell3 could price.
- The daily `digest`'s per-book paper rollup already includes `theta4` and will include `theta4_pt`
  once the tick-capture broadening (`paper/engine.py`, `.startswith(("mmsell","theta"))`) is live.

## 6. Pre-registered gates

**Stage 1 — fill realism.** Run until **≥80 filled live round-trips** (matches theta4's own paper
gate size) or 3 weeks, whichever comes first. ADVANCE to Stage 2 only if **both**:
- **fill rate ≥ 50%** of placed resting orders, AND
- **matched-market gap (twin vs live, `live_paper_parity` §4) is not a confirmed ACCOUNTING GAP** —
  i.e. on markets both sides settled, live and twin agree within noise. An ACCOUNTING GAP here
  would indict the simulator itself (the same standing severity as it would for any other book).

**Stage 2 — measure the edge.** Run until **≥150 filled round-trips**. KEEP the live book only if
`theta_fill_model`'s own-calibration realizable ¢/trade is **positive and comparable in magnitude
to theta4's paper edge** (not the borrowed-calibration mirage from §2). Otherwise **shelve live**
and keep theta4 as a paper-only research book — exactly theta's own pre-registered gate philosophy
(a live-confirmed loss is a valid, useful answer).

**Hard kill (any stage):** realized live P&L ≤ −$15 cumulative, or `MAX_DAILY_LOSS` trips twice in
a week → flip `LIVE_ENABLED=false` (or drop `theta4` from `LIVE_STRATEGIES`), hold open positions
to settlement (they close within the hour regardless), review before resuming.

## 7. Rollout sequence

1. **Build** — this PR, all switches OFF, full test suite green, PR reviewed and merged.
2. **Verify the deploy** — same ops-channel logs/db checks as every prior live change: clean
   startup, twin harness logs, no migration issues (none expected — the schema is unchanged).
3. **Confirm current theta4 paper numbers** via the ops channel immediately before arming (win
   rate wasn't re-pulled for this doc beyond the P&L figures in §1 — confirm it's still holding
   before committing real money, the same discipline `mm_check_1` applies to mmsell).
4. **Arm** — set the `THETA_LIVE_*` sizing knobs first (still inert), confirm via ops-channel env
   read, THEN add `theta4` to `LIVE_STRATEGIES` as a separate deliberate step. Verify the twin
   epoch (`theta4_pt`) opens within one cycle.
5. **Stage 1** — watch `live_paper_parity` + the digest; §6 decides advance/retry/shelve.
6. **Stage 2** — scale per §6 only on a clean Stage-1 pass.
7. Update `docs/BOOK_REGISTRY.md`'s theta4 row at each transition, and log decisions in
   `docs/RESEARCH_JOURNAL.md`.

## 8. Safety / rollback

Identical posture to mmsell10 (`MMSELL_LIVE_PLAN.md` §8–9): fail-closed everywhere;
`LIVE_ENABLED=false`/`KILL_SWITCH=true` halts new entries; open theta positions settle within the
hour regardless (no multi-day hold risk to manage, unlike mmsell). For an early flatten, use
`close_theta_positions` via the same two-step sequence mmsell's wind-down documents: drop the
allowlist entry first (closes still need `KILL_SWITCH=false` to reach Kalshi), enable
`THETA_CLOSEOUT_ENABLED`/`THETA_CLOSEOUT_STRATEGIES`, confirm flat via the ops channel, **then**
set `KILL_SWITCH=true`.
