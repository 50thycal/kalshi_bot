# The contest key over-grouped distinct outcomes — Gmmsell2

**Found 2026-09-07** while pulling per-series trading detail for the registry sign-off.
Code: `regimes.SUBJECT_SPLIT_SERIES`, `contest_key_of(..., split_subjects=)`, the `contestkey=`
book-spec key. Arm: **`Gmmsell2`**. Tests: `tests/test_mmsell_subject_split_contest.py`.

## The defect

`contest_key_of` groups markets that resolve on ONE underlying outcome, so a concentration cap
counts a nested ladder as the single bet it is. Outside the sports regimes it does that by
stripping the last ticker token — which is right for a ladder and **wrong where the last token
names a distinct subject**:

| ticker | last token | shipped key | correct? |
|---|---|---|---|
| `KXTRUMPSAY-26AUG03-AMER` / `-ZOHR` | **word** (37 distinct) | one weekly contest | ❌ unrelated outcomes |
| `KXRAIN-26SEP06-TTN` / `-SEA` | **city** (22) | one daily contest | ❌ |
| `KXWCMENTION-26JUL03ARGCPV-BICY` | **word** (70) | `Soccer:26JUL03ARGCPV` | ❌ **collides with the actual match markets** |
| `KXWTI-26SEP0414-T93.99` / `-T72.49` | strike | one contest | ✅ one oil print |
| `KXNFLSPREAD-26AUG13ARILV-ARI10` | line | `NFL:26AUG13ARILV` | ✅ one game |

A cap of 1 therefore refused entries that share **no outcome at all** — and the correlation the
cap exists to catch was never in those markets to begin with.

It also distorted every measurement keyed on contests: `KXTRUMPSAY`'s own-evidence weight in the
scorecard read **19%** when the honest figure is **74%**, and `KXRAIN`'s **42%** against **85%**.
Both are among the strongest performers in the family, and both were being discounted for a
correlation they do not have.

## The rule, and why it is hand-audited

**The market type does not decide it.** `KXTRUTHSOCIAL` is typed `mention` and is a THRESHOLD
series (`-B230`, `-T240` are post-count levels), so a rule keyed on `mention` would split a
ladder that must stay grouped. `event_stat` is equally mixed — `KXRAIN` splits by city while
`KXYTVIEWSHIGH` is a view-count threshold with the artist already in segment 2.

So `SUBJECT_SPLIT_SERIES` is an explicit set, every entry verified against real traded tickers,
extended the same way — by looking, in a PR. Same convention as `SERIES_TYPES`, for the same
reason: the classification IS the claim.

## Why an arm and not a fix in place

`Gmmsell1` is running against the shipped key. Correcting it in place would move the arm
underneath the experiment and make its numbers before and after incomparable.

So the corrected key is **opt-in** (`split_subjects=False` by default, byte-identical), and a
third arm measures it:

| arm | band | cap | key |
|---|---|---|---|
| `Gmmsell0` | 5–10¢, maxyes 7 | none | — (control) |
| `Gmmsell1` | same | 1 | shipped |
| **`Gmmsell2`** | same | 1 | **`split`** |

`Gmmsell1` vs `Gmmsell2` isolates the **key**; `Gmmsell0` remains the uncapped control. The new
arm is deliberately **less strict** — it admits entries `Gmmsell1` refuses on markets sharing a
date but no outcome — so the direction of any difference is interpretable.

An unrecognised `contestkey=` value is **rejected**, not ignored: a silent fallback would give a
book that reads as testing the new key while running the old one.

## Untouched

The sports grouping from XOS-000020 — one game resolving TOTAL, SPREAD and HR against a seller
at the same instant, measured at −$5.66 of a −$6.78 drawdown — is unchanged under both keys and
tested to stay that way.

## Not yet done

`Gmmsell2` is a **config default**; under `NEW_ONLY` a tag cannot trade until it is registered
to an active deployment arm in Experiment OS, and production's `MMSELL_VARIANTS` env var
overrides the code default. Both remain to be done after merge.
