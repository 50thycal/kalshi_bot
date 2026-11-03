# Volume-incentive census: what do Kalshi's volume programmes pay, and for what?

**Status:** PRE-REGISTERED 2026-11-03 (Research Lab), before the census ran. The owner asked
for it on 2026-11-03 ("yes do this too").

Study 7 of the incentive-programme research (`docs/RESEARCH_REVIEW.md`). The probe is
`scripts/limm_volume_census.py` (read-only, ops-allowlisted). It is a **census**: it inventories
and nominates follow-ups, and it authorizes nothing.

## 1. Question

Kalshi's `incentive_programs` endpoint lists more than liquidity programmes. Its types include
`volume`, `margin_maker_volume` and `margin_taker_volume` (`LIQUIDITY_INCENTIVE_RESEARCH.md`
§3). Every study so far has read liquidity programmes only.

> How much do the non-liquidity programmes pay, on which series, and do our books already trade
> their markets?

## 2. Data

- The population is `incentive_programs`, current terms only (`superseded_at IS NULL`).
- **"Active":** `end_date > now()`, `disappeared_at IS NULL`, and `last_seen_at` within 1 day.
- **"Since 2026-09-01":** every programme whose `start_date` is on or after that date, for scale.
- **Pool per day:** `period_reward_usd` ÷ the programme's length in days.
- **Cap per account:** `max_reward_per_account`, in centi-cents, read from `extra_params_json`
  when present.
- **Overlap:** active volume-type markets that any of our books entered in the last 30 days
  (`paper_trades` or `live_orders`).

## 3. Pre-registered verdict

- **`INSTRUMENT GAP`:** no non-liquidity programme has ever been recorded. The collector may not
  request those types. The follow-up is an instrument change, owned by Live Ops or a
  workstream.
- **`FOLLOW-UP`:** active volume-type pools total at least **$10 a day**, and at least one series
  pays **$2 a day** or more.
  - This nominates a design study: how volume rewards are scored, and whether existing MMSELL or
    LIMM flow already earns them.
  - It is a nomination, not a book.
- **`NO-LEAD`:** neither of the above.
- **Overlap at least 1** adds a note in any case: our books may already earn volume rewards
  that nothing attributes. That supports the "reward attribution without screenshots" candidate.

## 4. Command

`{"type":"script","name":"limm_volume_census","args":[],"id":"rl-s7-census-20261103"}`

## 5. Results

Not run.
