# HANDOFF — LIMM S2 live test (to Live Ops)

**From:** Research Lab, 2026-11-14. **To:** Live Ops (arming, limits, checks) and a build
session (Build B). **Pre-registration:** `docs/LIMM_S2_LIVE_TEST.md` (the bars, stops and read).
**Rules in code:** `kalshi_bot/liquidity_incentive/s2_live.py`. **Contract:**
`kalshi_bot/experiment_os/limm_s2_live.py` (package `limm-s2-live`, register only).

## 0. Nothing is live. It starts only when BOTH hold

1. **The 2026-11-24 paper read labels `S2-best` PROMISING** (`LIMM_PAPER_STRATEGIES.md` §5–§6,
   ops `rl-ps-read-<date>`). HOLD, FRAGILE or NOT-PROMISING → this test does not start. Record
   that in `LIMM_S2_LIVE_TEST.md` §10 and the calendar row, and stop.
2. **The owner says go for this test, by name**, after seeing that read. Every step below
   marked **HARD STOP** needs that go, in words that name the action. A go for Build B's merge is
   not a go for arming, and the reverse.

## 1. What runs

About 5 live $10 bids at the best bid, on markets taken from the paper arm `S2-best`'s open
quote rows. One market per event. ≤ $50 resting. Fills are held to settlement. 14 days. Fresh
tags: live **`As2best1`**, twin **`As2best1_pt1`** (paper tag `s2best1`). Separate from
`Alimm1`, which stays stood down (`LIQUIDITY_INCENTIVE_LIVE_ENABLED=false`, thesis §9.78).

## 2. Build B — the runner change (not built yet; owner approves the merge)

The current live runner (`liquidity_incentive/runner.py`) is `Alimm1`'s: mention-only universe,
behind-touch pricing, exits, resting sells. It cannot run this book. Build B adds a small,
separate path. It touches the order path, so its PR is merged **only on the owner's go**
(STANDING_AUTHORIZATIONS hard stop). Scope:

- **Own switch, default off:** `LIQUIDITY_INCENTIVE_S2_LIVE_ENABLED` (config + `railway_env`
  allowlist). Off → nothing constructed, nothing changes.
- **Selection:** each hour read open `incentive_shadow_quotes` rows with `policy='S2-best'`
  (side and price from `reason`, event and series from `book_json.arm`, rate from
  `est_reward_per_hour_usd`, hours to step-aside from the programme and close times) and call
  `s2_live.select_live` with the account's held tickers and this book's filled tickers.
- **Placement:** `s2_live.bid_for` — the paper arm's side and price, $10 post-only. Cancel when
  a market leaves the selection. Re-price when the paper arm re-prices.
- **Executor:** an incentive-bid path for `As2best1` with **this book's** caps (5 open, $50
  resting, $10 a bid, ≤ 10¢). It must not reuse `Alimm1`'s constants (10 open, $100), its exits
  or its resting sells. `owns_tag` must also skip `As2best1` in `manage_exits`, so the
  process-wide TP/SL never sells a held fill.
- **Logging for the read:** each hour a bid rests, record the uncalibrated `scoring.estimate`
  for our order (`Alimm1`'s runner already does this; reuse it).
- **Stops:** compute `fill_loss_usd` (realized losses + held fills marked at the best bid) and
  call `s2_live.stop_reason`. On a stop: cancel resting bids, log `stop_fill_loss_cap` /
  `stop_duration_over`, place nothing more. Held fills stay to settlement.
- **The `arm` verb** for package `limm-s2-live`, copying `liquidity_incentive_mm.arm`:
  re-evaluate the probe gate → PROBE→PAPER (end the probe deployment) → `arm_live_canary`
  with `live_tags={s2best_live: As2best1}`, `twin_tags={s2best_live: As2best1_pt1}` and
  `material_config()` built with `enforcement.live_material_block`. Its `reason` must name the
  11-24 read id and its label. Add `ACTIVATION_VARS` and an `activation_env(current_...)` that
  **appends** to `LIVE_STRATEGIES` / `LIVE_PAPER_TWINS` and never drops a tag.
- **Tests:** switch off ⇒ no runner object and no order; caps; one per event; no exits on the
  tag; the stop; and `tests/test_live_material_baseline_contract.py` passing with the new arm.

## 3. Arming steps (after §0 and Build B are merged)

Ops-channel recipe: `CLAUDE.md`. Unique ids. Read before you write.

1. **Register (paper scope, no money).** `{"type":"xos","command":"package-preflight","args":
   ["limm-s2-live"],"id":"lo-s2-pf-<date>"}` → must be **GO**. Send the printed
   `REGISTER_PACKAGE` envelope exactly as printed. Check the receipt says SUCCEEDED, then run
   `xos show limm-s2-live` and confirm the state is PROBE.
2. **Wait for the probe gate.** `s2_instrument_ready` needs ≥ 12 discovery cycles and ≥ 50
   shadow quotes after registration (about an hour). `s2_paper_to_live` needs ≥ 200 quotes,
   ≥ 50 outcomes and ≥ 20 programmes (a few hours).
3. **HARD STOP — arm.** `ARM_CANARY` for `limm-s2-live`, `approved_by` = the owner's name,
   reason = the 11-24 read id + "S2-best PROMISING" + the owner's go. Then confirm with `xos show`
   that `As2best1` and `As2best1_pt1` are each on an ACTIVE arm.
4. **HARD STOP — switch on.** Read the LIVE values first (`env` read). Then set, as one
   request, built from `activation_env(current_live_strategies=<live value>, ...)`:
   - `LIVE_PAPER_TWINS` += `As2best1:As2best1_pt1`
   - `LIQUIDITY_INCENTIVE_S2_LIVE_ENABLED=true`
   - `LIVE_STRATEGIES` += `As2best1` (last)

   Never retype `LIVE_STRATEGIES`. Do not touch `LIQUIDITY_INCENTIVE_LIVE_ENABLED`,
   `LIVE_PAPER_TWIN_SUFFIX`, `MAX_DAILY_LOSS` or `MAX_TOTAL_EXPOSURE`.
5. **Write the start time** in `LIMM_S2_LIVE_TEST.md` §10. The 14 days run from here.

## 4. Risk envelope (frozen; `s2_live.S2LiveRules`)

| | |
|---|---|
| Per bid | $10, cheap side 1–10¢, ≤ 1,000 contracts |
| Markets | ≤ 5, ≤ 1 per event |
| Resting | ≤ $50 |
| Fills | held to settlement; never re-entered |
| Loss stop | $30 of fill losses (realized + held marked at the best bid) |
| One-market breach | a settled market losing > $10.50 → stand down |
| Worst case before the stop | $80 ($30 + all $50 resting filling at once and going to zero) |
| Duration | 14 days |
| Account-wide breakers | unchanged; still apply |

## 5. Checks

**After 24 h:**
- Logs show the S2 runner's cycles, and ≤ 5 resting bids under `As2best1`, each ≤ $10 and on
  a different event.
- Each live market is also an open `S2-best` paper row (`db` read on `incentive_shadow_quotes`).
- There is a twin epoch row for `As2best1_pt1` (`live_paper_twins`).
- No order under `Alimm1`, and `LIVE_STRATEGIES` holds every tag it held before.
- Fills so far, and `fill_loss_usd` against the $30 stop.
- Control Tower: no silent arm, and `s2_live_loss_stop` is not FAIL.

**Daily:** fills, `fill_loss_usd`, markets rested. Ask the owner for a Rewards screenshot once a
week.

**Stand down** (exposure-reducing, under kill-switch semantics) on any of: the loss stop, the
breach, day 14, an order outside the envelope, or a bid on a market `S2-best` is not resting on.
Remove `As2best1` from `LIVE_STRATEGIES` (keep every other tag) and set the switch false. Held
fills settle normally.

## 6. The read (Research Lab)

Seven days after the stand-down, apply `LIMM_S2_LIVE_TEST.md` §7 once: confirmed payouts from
`reward_ledger.CONFIRMED_REWARD_PAYOUTS`, fill losses, and the estimate log. The label is one of
STOP / EXTEND / KEEP / SCALE-CANDIDATE, plus the ratio label. Every outcome goes to the owner as a
decision.
