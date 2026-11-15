# HANDOFF — LIMM S2 live test (to Live Ops)

**From:** Research Lab, 2026-11-14. **To:** Live Ops (arming, limits, checks) and a build
session (Build B). **Pre-registration:** `docs/LIMM_S2_LIVE_TEST.md` (the bars, stops and read).
**Rules in code:** `kalshi_bot/liquidity_incentive/s2_live.py`. **Contract:**
`kalshi_bot/experiment_os/limm_s2_live.py` (package `limm-s2-live`, register only).

## 0. When it starts

As first written, the test was to wait for the 2026-11-24 paper read. On 2026-11-14 the owner
chose to arm it as soon as Build B is merged (`DEC-047`). Each step marked **HARD STOP** below
still needs the owner's go, in words that name that step. A go for the merge is not a go for
arming, and a go for arming is not a go for the env switch-on.

## 1. What runs

About 5 live $10 bids at the best bid, on markets taken from the paper arm `S2-best`'s open
quote rows. One market per event. ≤ $50 resting. Fills are held to settlement. 14 days. Fresh
tags: live **`As2best1`**, twin **`As2best1_pt1`** (paper tag `s2best1`). Separate from
`Alimm1`, which stays stood down (`LIQUIDITY_INCENTIVE_LIVE_ENABLED=false`, thesis §9.78).

## 2. Build B — the runner (built; its merge is a HARD STOP)

- `liquidity_incentive/s2_runner.py` (`S2LiveRunner`):
  - reads the open `S2-best` paper rows each live cycle and selects with `s2_live.select_live`
    (a held fill keeps its market slot, its event and its dollars until it settles);
  - cancels bids that left the selection or whose side or price the paper arm moved;
  - places missing bids and mirrors each one to the twin;
  - logs our order's uncalibrated estimate hourly (`system_events`, component
    `limm_s2_estimate`);
  - applies the stops (`limm_s2_stop`, recorded so a restart does not resume).
- `LiveExecutor.place_s2_bid`:
  - checks the shared gates (switches, daily loss, portfolio breaker, strategy-agnostic dedup,
    account-held, shard);
  - then checks this book's own caps: ≤ 5 open, ≤ $50, $10 a bid, ≤ 10¢.
- `_incentive_managed`: the executor treats `As2best1` the way it treats `Alimm1`. No
  process-wide TP/SL and no 4 h timeout, so fills are held and the runner manages its own
  bids. No other book changes.
- Own switch `LIQUIDITY_INCENTIVE_S2_LIVE_ENABLED` (default off): when it is off, `main.py`
  does not construct the runner and `place_s2_bid` refuses.
- Package `limm-s2-live` gains `arm`, `material_config`, `activation_env` and
  `ACTIVATION_VARS`.

## 3. Arming steps (after Build B is merged)

Ops-channel recipe: `CLAUDE.md`. Unique ids. Read before you write.

1. **Register (paper scope, no money).** `{"type":"xos","command":"package-preflight","args":
   ["limm-s2-live"],"id":"lo-s2-pf-<date>"}` → must be **GO**. Send the printed
   `REGISTER_PACKAGE` envelope exactly as printed. Check the receipt says SUCCEEDED, then run
   `xos show limm-s2-live` and confirm the state is PROBE.
2. **Wait for the probe gate.** `s2_instrument_ready` needs ≥ 12 discovery cycles and ≥ 50
   shadow quotes after registration (about an hour). `s2_paper_to_live` needs ≥ 200 quotes,
   ≥ 50 outcomes and ≥ 20 programmes (a few hours).
3. **HARD STOP — arm.** `ARM_CANARY` for `limm-s2-live`, `approved_by` = the owner's name.
   The package writes the reason (`ARM_REASON`, which cites DEC-047). Then confirm with `xos show`
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
