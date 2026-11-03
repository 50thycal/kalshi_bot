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

## 5. Results (2026-11-03, `rl-s7-census-20261103`)

```
LIMM volume census — non-liquidity incentive programmes (pre-registered docs/LIMM_VOLUME_CENSUS.md)

== by type (current terms)
  type                    active since 09-01 pool since $  first seen   last seen
  liquidity                 4244      204726  14203580.00  2026-09-17  2026-11-03
  volume                       0          33     17500.00  2026-09-19  2026-10-29

== active non-liquidity programmes by series (0 programmes, 0 series, $0.00/day in all)
  series                   progs    $/day cap/acct $  types | description

== overlap: 0 of 0 active non-liquidity markets were entered by one of our books in the last 30 days

== VERDICT (a census; a nomination authorizes nothing)
  NO-LEAD  (total $0.00/day; best series $0.00/day)
```

- **Verdict: NO-LEAD.** No volume programme is active today.
- **They do exist:** 33 volume programmes were seen from 2026-09-19 to 10-29, with $17,500 of
  pools in all, about $530 each. Each was live for a short time and none are live now.
- **No overlap** with our books, because nothing is active.
- **Reading:** volume rewards come in bursts and are large when they appear. The census bar
  (an active pool) cannot be met between bursts. A follow-up would need to catch the next
  burst. That is recorded on the review's candidate list, not started here.
