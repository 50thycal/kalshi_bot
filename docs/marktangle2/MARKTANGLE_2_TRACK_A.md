# MARKTANGLE-2 Track A — cross-family conditional reversion

Direction is never collapsed: every conditional row is P(NO next | k YES) or P(YES next | k NO), with its n and a one-sided Wilson 95% lower bound.

## BASEBALL_TOTAL

families 18 · train points 7403 · holdout points 3174 · split at 2026-08-13T21:24:25Z · holdout priced 96%

### A-SIMPLE — streak count and directional reversal by k (TRAIN | HOLDOUT)

| streak of | k | train n | P(rev) | lb95 | hold n | P(rev) | lb95 |
|---|---|---|---|---|---|---|---|
| YES | 1 | 1458 | 53.1% | 50.9% | 596 | 47.5% | 44.1% |
| YES | 2 | 680 | 45.9% | 42.8% | 315 | 42.9% | 38.4% |
| YES | 3 | 367 | 35.4% | 31.4% | 181 | 34.3% | 28.7% |
| YES | 4 | 237 | 27.8% | 23.3% | 119 | 24.4% | 18.5% |
| YES | 5 | 171 | 18.1% | 13.8% | 90 | 22.2% | 15.9% |
| YES | >=6 | 1084 | 12.8% | 11.2% | 448 | 14.7% | 12.2% |
| NO | 1 | 1459 | 48.5% | 46.4% | 595 | 46.4% | 43.0% |
| NO | 2 | 746 | 44.0% | 41.0% | 321 | 38.3% | 34.0% |
| NO | 3 | 416 | 35.3% | 31.6% | 200 | 41.0% | 35.4% |
| NO | 4 | 269 | 40.9% | 36.1% | 118 | 41.5% | 34.3% |
| NO | 5 | 159 | 32.7% | 26.9% | 69 | 30.4% | 22.2% |
| NO | >=6 | 357 | 30.0% | 26.1% | 122 | 39.3% | 32.4% |

Class YES rate: train 54.0%, holdout 55.2%.

### A-HIERARCHICAL — logistic P(YES) with ridge family effects (TRAIN fit)

| coefficient | estimate | approx SE | z |
|---|---|---|---|
| intercept | 0.2581 | 0.2420 | 1.07 |
| prev_dir (+1 YES / -1 NO) | -0.0784 | 0.0367 | -2.14 |
| ln(k) | -0.0044 | 0.0366 | -0.12 |
| prev_dir x ln(k) | -0.0192 | 0.0393 | -0.49 |

Family effects (ridge λ=1.0): 10=-0.73, 11=-1.06, 12=-1.34, 13=-1.43, 14=-1.20, 15=-0.90, 16=-0.82, 17=-0.91, 18=-0.85, 19=-0.89, 2=2.73, 3=2.94, 4=1.97, 5=1.55, 6=0.87, 7=0.51, 8=-0.04, 9=-0.39

### Economics — TRAIN (in-sample, descriptive)

| arm | rule | N pred | N priced | accuracy | Brier | N trades | gross c | fees+slip c | net c | EV/trade c | avg edge c | return on risk | max DD c | worst streak | profit factor | mirror EV/trade c | YES / NO net c |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A0 | independence baseline (family base rate) | 7403 | 0 | 71.6% | 0.1887 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |
| A1 | one-step transition | 7403 | 0 | 71.7% | 0.1888 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |
| A2 | streak-length reversion (direction-specific) | 7403 | 0 | 62.0% | 0.2200 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |
| A3 | hierarchical reversion (family effects) | 7403 | 0 | 71.6% | 0.1881 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |

### Economics — HOLDOUT (the verdict)

| arm | rule | N pred | N priced | accuracy | Brier | N trades | gross c | fees+slip c | net c | EV/trade c | avg edge c | return on risk | max DD c | worst streak | profit factor | mirror EV/trade c | YES / NO net c |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A0 | independence baseline (family base rate) | 3174 | 3057 | 71.3% | 0.1902 | 2067 | -1545 | 5271 | -6816 | -3.30 | 20.38 | -12.3% | 8528 | 38 | 0.81 | -3.83 | -413 / -6403 |
| A1 | one-step transition | 3174 | 3057 | 70.7% | 0.1918 | 2079 | -1843 | 5284 | -7127 | -3.43 | 20.60 | -13.1% | 8657 | 38 | 0.80 | -3.71 | -356 / -6771 |
| A2 | streak-length reversion (direction-specific) | 3174 | 3057 | 61.4% | 0.2237 | 2410 | -5955 | 6054 | -12009 | -4.98 | 24.17 | -22.5% | 13021 | 41 | 0.71 | -2.12 | -157 / -11852 |
| A3 | hierarchical reversion (family effects) | 3174 | 3057 | 71.3% | 0.1906 | 2040 | -1248 | 5217 | -6465 | -3.17 | 20.61 | -11.5% | 8006 | 38 | 0.82 | -3.98 | -27 / -6438 |

### Verdicts (HOLDOUT)

- **A0 — —**: baseline: comparator, not graded
- **A1 — FAIL**: failed: net P&L > 0; failed: EV/trade > 0; failed: Brier < baseline Brier; failed: net P&L > baseline net P&L; failed: EV/trade - mirror EV/trade >= 3.0c; failed: net P&L > 0 without the top family; failed: net P&L > 0 without the top 1% of trades
- **A2 — FAIL**: failed: net P&L > 0; failed: EV/trade > 0; failed: Brier < baseline Brier; failed: net P&L > baseline net P&L; failed: EV/trade - mirror EV/trade >= 3.0c; failed: net P&L > 0 without the top family; failed: net P&L > 0 without the top 1% of trades
- **A3 — FAIL**: failed: net P&L > 0; failed: EV/trade > 0; failed: Brier < baseline Brier; failed: EV/trade - mirror EV/trade >= 3.0c; failed: net P&L > 0 without the top family; failed: net P&L > 0 without the top 1% of trades; ok: net P&L > baseline net P&L

### Per-family HOLDOUT net P&L (A3, treatment)

| family | trades | net c | EV/trade c |
|---|---|---|---|
| KXMLBTOTAL|10 | 215 | -1372 | -6.38 |
| KXMLBTOTAL|11 | 213 | -1348 | -6.33 |
| KXMLBTOTAL|12 | 213 | -1221 | -5.73 |
| KXMLBTOTAL|13 | 178 | -996 | -5.60 |
| KXMLBTOTAL|14 | 75 | 335 | 4.47 |
| KXMLBTOTAL|15 | 38 | 157 | 4.13 |
| KXMLBTOTAL|16 | 29 | -234 | -8.07 |
| KXMLBTOTAL|17 | 19 | -14 | -0.74 |
| KXMLBTOTAL|18 | 6 | -181 | -30.17 |
| KXMLBTOTAL|19 | 6 | 33 | 5.50 |
| KXMLBTOTAL|2 | 6 | -266 | -44.33 |
| KXMLBTOTAL|3 | 34 | -242 | -7.12 |
| KXMLBTOTAL|4 | 80 | -80 | -1.00 |
| KXMLBTOTAL|5 | 126 | -19 | -0.15 |
| KXMLBTOTAL|6 | 179 | 590 | 3.30 |
| KXMLBTOTAL|7 | 190 | 41 | 0.22 |
| KXMLBTOTAL|8 | 216 | -489 | -2.26 |
| KXMLBTOTAL|9 | 217 | -1159 | -5.34 |

Robustness: net without top family `KXMLBTOTAL|6` = -7055c; net without top 21 trade(s) = -8369c.

## BASKETBALL_TOTAL

families 20 · train points 680 · holdout points 295 · split at 2026-08-13T03:14:39Z · holdout priced 97%

### A-SIMPLE — streak count and directional reversal by k (TRAIN | HOLDOUT)

| streak of | k | train n | P(rev) | lb95 | hold n | P(rev) | lb95 |
|---|---|---|---|---|---|---|---|
| YES | 1 | 179 | 52.5% | 46.4% | 79 | 72.2% | 63.2% |
| YES | 2 | 85 | 32.9% | 25.2% | 22 | 68.2% | 50.6% |
| YES | 3 | 54 | 44.4% | 33.9% | 9 | 100.0% | 76.9% |
| YES | 4 | 29 | 44.8% | 30.7% | 1 | 100.0% | 27.0% |
| YES | 5 | 16 | 12.5% | 4.2% | 0 | — | — |
| YES | >=6 | 33 | 42.4% | 29.4% | 0 | — | — |
| NO | 1 | 177 | 70.6% | 64.7% | 77 | 42.9% | 34.0% |
| NO | 2 | 50 | 34.0% | 24.1% | 43 | 41.9% | 30.3% |
| NO | 3 | 32 | 53.1% | 38.9% | 25 | 24.0% | 13.0% |
| NO | 4 | 15 | 46.7% | 27.7% | 19 | 36.8% | 21.4% |
| NO | 5 | 8 | 62.5% | 34.8% | 10 | 50.0% | 26.9% |
| NO | >=6 | 2 | 50.0% | 12.1% | 10 | 40.0% | 19.4% |

Class YES rate: train 57.8%, holdout 34.6%.

### A-HIERARCHICAL — logistic P(YES) with ridge family effects (TRAIN fit)

| coefficient | estimate | approx SE | z |
|---|---|---|---|
| intercept | 0.4548 | 0.2489 | 1.83 |
| prev_dir (+1 YES / -1 NO) | -0.3821 | 0.1065 | -3.59 |
| ln(k) | -0.2736 | 0.1454 | -1.88 |
| prev_dir x ln(k) | 0.4656 | 0.1442 | 3.23 |

Family effects (ridge λ=1.0): 164=0.67, 167=0.39, 169=0.20, 170=0.27, 171=0.31, 172=0.13, 173=0.14, 174=0.31, 175=0.10, 176=-0.02, 177=0.35, 178=0.08, 179=-0.24, 180=0.39, 181=-0.32, 182=-0.36, 184=-0.55, 185=-0.38, 187=-0.68, 190=-0.79

### Economics — TRAIN (in-sample, descriptive)

| arm | rule | N pred | N priced | accuracy | Brier | N trades | gross c | fees+slip c | net c | EV/trade c | avg edge c | return on risk | max DD c | worst streak | profit factor | mirror EV/trade c | YES / NO net c |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A0 | independence baseline (family base rate) | 680 | 0 | 60.7% | 0.2347 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |
| A1 | one-step transition | 680 | 0 | 62.5% | 0.2293 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |
| A2 | streak-length reversion (direction-specific) | 680 | 0 | 61.5% | 0.2305 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |
| A3 | hierarchical reversion (family effects) | 680 | 0 | 63.8% | 0.2273 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |

### Economics — HOLDOUT (the verdict)

| arm | rule | N pred | N priced | accuracy | Brier | N trades | gross c | fees+slip c | net c | EV/trade c | avg edge c | return on risk | max DD c | worst streak | profit factor | mirror EV/trade c | YES / NO net c |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A0 | independence baseline (family base rate) | 295 | 285 | 38.3% | 0.2815 | 240 | -1061 | 604 | -1665 | -6.94 | 29.52 | -33.6% | 1905 | 40 | 0.60 | 0.00 | -1535 / -130 |
| A1 | one-step transition | 295 | 285 | 36.9% | 0.2858 | 247 | -1206 | 625 | -1831 | -7.41 | 29.29 | -34.5% | 1956 | 38 | 0.59 | 0.45 | -1618 / -213 |
| A2 | streak-length reversion (direction-specific) | 295 | 285 | 48.8% | 0.2774 | 247 | -461 | 625 | -1086 | -4.40 | 29.69 | -20.6% | 1343 | 38 | 0.73 | -2.58 | -1398 / 312 |
| A3 | hierarchical reversion (family effects) | 295 | 285 | 45.4% | 0.2806 | 243 | -803 | 613 | -1416 | -5.83 | 29.20 | -27.2% | 1719 | 41 | 0.66 | -1.07 | -1502 / 86 |

### Verdicts (HOLDOUT)

- **A0 — —**: baseline: comparator, not graded
- **A1 — FAIL**: failed: net P&L > 0; failed: EV/trade > 0; failed: Brier < baseline Brier; failed: net P&L > baseline net P&L; failed: EV/trade - mirror EV/trade >= 3.0c; failed: net P&L > 0 without the top family; failed: net P&L > 0 without the top 1% of trades
- **A2 — FAIL**: failed: net P&L > 0; failed: EV/trade > 0; failed: EV/trade - mirror EV/trade >= 3.0c; failed: net P&L > 0 without the top family; failed: net P&L > 0 without the top 1% of trades; ok: Brier < baseline Brier; ok: net P&L > baseline net P&L
- **A3 — FAIL**: failed: net P&L > 0; failed: EV/trade > 0; failed: EV/trade - mirror EV/trade >= 3.0c; failed: net P&L > 0 without the top family; failed: net P&L > 0 without the top 1% of trades; ok: Brier < baseline Brier; ok: net P&L > baseline net P&L

### Per-family HOLDOUT net P&L (A3, treatment)

| family | trades | net c | EV/trade c |
|---|---|---|---|
| KXWNBATOTAL|164 | 13 | 0 | 0.00 |
| KXWNBATOTAL|167 | 14 | 89 | 6.36 |
| KXWNBATOTAL|169 | 9 | -93 | -10.33 |
| KXWNBATOTAL|170 | 13 | 57 | 4.38 |
| KXWNBATOTAL|171 | 10 | -151 | -15.10 |
| KXWNBATOTAL|172 | 11 | -67 | -6.09 |
| KXWNBATOTAL|173 | 17 | 245 | 14.41 |
| KXWNBATOTAL|174 | 11 | -99 | -9.00 |
| KXWNBATOTAL|175 | 12 | -145 | -12.08 |
| KXWNBATOTAL|176 | 16 | -220 | -13.75 |
| KXWNBATOTAL|177 | 11 | -274 | -24.91 |
| KXWNBATOTAL|178 | 11 | -91 | -8.27 |
| KXWNBATOTAL|179 | 14 | -125 | -8.93 |
| KXWNBATOTAL|180 | 13 | -184 | -14.15 |
| KXWNBATOTAL|181 | 11 | -139 | -12.64 |
| KXWNBATOTAL|182 | 15 | 5 | 0.33 |
| KXWNBATOTAL|184 | 13 | -50 | -3.85 |
| KXWNBATOTAL|185 | 13 | 16 | 1.23 |
| KXWNBATOTAL|187 | 9 | -8 | -0.89 |
| KXWNBATOTAL|190 | 7 | -182 | -26.00 |

Robustness: net without top family `KXWNBATOTAL|173` = -1661c; net without top 3 trade(s) = -1675c.

## FOOTBALL_TOTAL

families 8 · train points 237 · holdout points 107 · split at 2026-08-29T01:23:27Z · holdout priced 99%

### A-SIMPLE — streak count and directional reversal by k (TRAIN | HOLDOUT)

| streak of | k | train n | P(rev) | lb95 | hold n | P(rev) | lb95 |
|---|---|---|---|---|---|---|---|
| YES | 1 | 70 | 52.9% | 43.1% | 30 | 33.3% | 21.1% |
| YES | 2 | 33 | 75.8% | 61.8% | 19 | 68.4% | 49.6% |
| YES | 3 | 8 | 87.5% | 58.9% | 2 | 100.0% | 42.5% |
| YES | 4 | 1 | 0.0% | 0.0% | 0 | — | — |
| YES | 5 | 1 | 100.0% | 27.0% | 0 | — | — |
| YES | >=6 | 0 | — | — | 0 | — | — |
| NO | 1 | 71 | 50.7% | 41.1% | 24 | 54.2% | 37.9% |
| NO | 2 | 30 | 46.7% | 32.6% | 16 | 37.5% | 20.8% |
| NO | 3 | 16 | 75.0% | 54.5% | 9 | 22.2% | 7.6% |
| NO | 4 | 4 | 25.0% | 5.8% | 6 | 83.3% | 49.8% |
| NO | 5 | 3 | 100.0% | 52.6% | 1 | 100.0% | 27.0% |
| NO | >=6 | 0 | — | — | 0 | — | — |

Class YES rate: train 46.0%, holdout 49.5%.

### A-HIERARCHICAL — logistic P(YES) with ridge family effects (TRAIN fit)

| coefficient | estimate | approx SE | z |
|---|---|---|---|
| intercept | 0.0881 | 0.3990 | 0.22 |
| prev_dir (+1 YES / -1 NO) | -0.0354 | 0.1701 | -0.21 |
| ln(k) | -0.4997 | 0.3356 | -1.49 |
| prev_dir x ln(k) | -1.2133 | 0.3450 | -3.52 |

Family effects (ridge λ=1.0): 48=0.55, 51=1.07, 57=0.48, 36=-0.36, 37=-0.01, 38=-0.22, 39=-0.60, 40=-0.90

### Economics — TRAIN (in-sample, descriptive)

| arm | rule | N pred | N priced | accuracy | Brier | N trades | gross c | fees+slip c | net c | EV/trade c | avg edge c | return on risk | max DD c | worst streak | profit factor | mirror EV/trade c | YES / NO net c |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A0 | independence baseline (family base rate) | 237 | 0 | 59.1% | 0.2411 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |
| A1 | one-step transition | 237 | 0 | 60.3% | 0.2337 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |
| A2 | streak-length reversion (direction-specific) | 237 | 0 | 59.1% | 0.2328 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |
| A3 | hierarchical reversion (family effects) | 237 | 0 | 64.6% | 0.2164 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |

### Economics — HOLDOUT (the verdict)

| arm | rule | N pred | N priced | accuracy | Brier | N trades | gross c | fees+slip c | net c | EV/trade c | avg edge c | return on risk | max DD c | worst streak | profit factor | mirror EV/trade c | YES / NO net c |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A0 | independence baseline (family base rate) | 107 | 106 | 42.1% | 0.2607 | 53 | -250 | 133 | -383 | -7.23 | 28.93 | -36.5% | 628 | 19 | 0.57 | 0.00 | 18 / -401 |
| A1 | one-step transition | 107 | 106 | 47.7% | 0.2688 | 52 | -415 | 130 | -545 | -10.48 | 29.26 | -53.7% | 679 | 20 | 0.42 | 3.33 | -35 / -510 |
| A2 | streak-length reversion (direction-specific) | 107 | 106 | 48.6% | 0.2531 | 51 | -285 | 127 | -412 | -8.08 | 28.37 | -41.8% | 604 | 23 | 0.52 | 0.88 | -7 / -405 |
| A3 | hierarchical reversion (family effects) | 107 | 106 | 47.7% | 0.2942 | 54 | -392 | 137 | -529 | -9.80 | 29.21 | -44.4% | 659 | 14 | 0.48 | 2.44 | -84 / -445 |

### Verdicts (HOLDOUT)

- **A0 — —**: baseline: comparator, not graded
- **A1 — HOLD**: train points 237 < 500; holdout trades 52 < 100
- **A2 — HOLD**: train points 237 < 500; holdout trades 51 < 100
- **A3 — HOLD**: train points 237 < 500; holdout trades 54 < 100

### Per-family HOLDOUT net P&L (A3, treatment)

| family | trades | net c | EV/trade c |
|---|---|---|---|
| KXNCAAFTOTAL|48 | 4 | 71 | 17.75 |
| KXNCAAFTOTAL|51 | 7 | 8 | 1.14 |
| KXNCAAFTOTAL|57 | 8 | -119 | -14.88 |
| KXNFLTOTAL|36 | 7 | -39 | -5.57 |
| KXNFLTOTAL|37 | 8 | -86 | -10.75 |
| KXNFLTOTAL|38 | 7 | -153 | -21.86 |
| KXNFLTOTAL|39 | 7 | -158 | -22.57 |
| KXNFLTOTAL|40 | 6 | -53 | -8.83 |

Robustness: net without top family `KXNCAAFTOTAL|48` = -600c; net without top 1 trade(s) = -603c.

## SOCCER_TOTAL

families 13 · train points 841 · holdout points 364 · split at 2026-08-20T03:59:57Z · holdout priced 95%

### A-SIMPLE — streak count and directional reversal by k (TRAIN | HOLDOUT)

| streak of | k | train n | P(rev) | lb95 | hold n | P(rev) | lb95 |
|---|---|---|---|---|---|---|---|
| YES | 1 | 124 | 58.1% | 50.7% | 66 | 57.6% | 47.5% |
| YES | 2 | 52 | 28.8% | 19.8% | 27 | 44.4% | 29.9% |
| YES | 3 | 37 | 32.4% | 21.4% | 13 | 30.8% | 14.6% |
| YES | 4 | 25 | 32.0% | 19.1% | 8 | 37.5% | 16.1% |
| YES | 5 | 17 | 29.4% | 15.1% | 3 | 33.3% | 7.8% |
| YES | >=6 | 85 | 11.8% | 7.2% | 46 | 8.7% | 4.0% |
| NO | 1 | 127 | 44.9% | 37.8% | 63 | 54.0% | 43.7% |
| NO | 2 | 69 | 31.9% | 23.5% | 30 | 33.3% | 21.1% |
| NO | 3 | 45 | 17.8% | 10.3% | 22 | 27.3% | 14.8% |
| NO | 4 | 36 | 16.7% | 8.9% | 15 | 26.7% | 12.6% |
| NO | 5 | 29 | 20.7% | 11.1% | 12 | 41.7% | 22.0% |
| NO | >=6 | 195 | 11.3% | 8.1% | 59 | 10.2% | 5.4% |

Class YES rate: train 40.3%, holdout 45.6%.

### A-HIERARCHICAL — logistic P(YES) with ridge family effects (TRAIN fit)

| coefficient | estimate | approx SE | z |
|---|---|---|---|
| intercept | -0.4565 | 0.3215 | -1.42 |
| prev_dir (+1 YES / -1 NO) | -0.0061 | 0.1243 | -0.05 |
| ln(k) | -0.0094 | 0.1144 | -0.08 |
| prev_dir x ln(k) | 0.1927 | 0.1191 | 1.62 |

Family effects (ridge λ=1.0): 1=2.56, 2=1.41, 3=0.75, 4=-0.10, 5=-0.95, 6=-1.77, 7=-1.60, 1=2.09, 2=0.77, 3=0.05, 4=-0.49, 5=-0.96, 6=-1.75

### Economics — TRAIN (in-sample, descriptive)

| arm | rule | N pred | N priced | accuracy | Brier | N trades | gross c | fees+slip c | net c | EV/trade c | avg edge c | return on risk | max DD c | worst streak | profit factor | mirror EV/trade c | YES / NO net c |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A0 | independence baseline (family base rate) | 841 | 0 | 79.3% | 0.1502 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |
| A1 | one-step transition | 841 | 0 | 76.9% | 0.1570 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |
| A2 | streak-length reversion (direction-specific) | 841 | 0 | 73.5% | 0.1779 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |
| A3 | hierarchical reversion (family effects) | 841 | 0 | 79.3% | 0.1453 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |

### Economics — HOLDOUT (the verdict)

| arm | rule | N pred | N priced | accuracy | Brier | N trades | gross c | fees+slip c | net c | EV/trade c | avg edge c | return on risk | max DD c | worst streak | profit factor | mirror EV/trade c | YES / NO net c |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A0 | independence baseline (family base rate) | 364 | 347 | 77.7% | 0.1603 | 185 | -1400 | 454 | -1854 | -10.02 | 17.48 | -46.4% | 1864 | 21 | 0.42 | 2.61 | -604 / -1250 |
| A1 | one-step transition | 364 | 347 | 71.7% | 0.1829 | 175 | -938 | 434 | -1372 | -7.84 | 21.40 | -32.4% | 1430 | 21 | 0.53 | 0.42 | -405 / -967 |
| A2 | streak-length reversion (direction-specific) | 364 | 347 | 67.9% | 0.1982 | 182 | -1031 | 446 | -1477 | -8.12 | 23.70 | -37.6% | 1598 | 24 | 0.51 | 0.74 | -412 / -1065 |
| A3 | hierarchical reversion (family effects) | 364 | 347 | 77.7% | 0.1579 | 153 | -1079 | 389 | -1468 | -9.59 | 19.39 | -35.1% | 1529 | 18 | 0.49 | 1.82 | -472 / -996 |

### Verdicts (HOLDOUT)

- **A0 — —**: baseline: comparator, not graded
- **A1 — FAIL**: failed: net P&L > 0; failed: EV/trade > 0; failed: Brier < baseline Brier; failed: EV/trade - mirror EV/trade >= 3.0c; failed: net P&L > 0 without the top family; failed: net P&L > 0 without the top 1% of trades; ok: net P&L > baseline net P&L
- **A2 — FAIL**: failed: net P&L > 0; failed: EV/trade > 0; failed: Brier < baseline Brier; failed: EV/trade - mirror EV/trade >= 3.0c; failed: net P&L > 0 without the top family; failed: net P&L > 0 without the top 1% of trades; ok: net P&L > baseline net P&L
- **A3 — FAIL**: failed: net P&L > 0; failed: EV/trade > 0; failed: EV/trade - mirror EV/trade >= 3.0c; failed: net P&L > 0 without the top family; failed: net P&L > 0 without the top 1% of trades; ok: Brier < baseline Brier; ok: net P&L > baseline net P&L

### Per-family HOLDOUT net P&L (A3, treatment)

| family | trades | net c | EV/trade c |
|---|---|---|---|
| KXMLSTOTAL|1 | 5 | -133 | -26.60 |
| KXMLSTOTAL|2 | 16 | -281 | -17.56 |
| KXMLSTOTAL|3 | 26 | -69 | -2.65 |
| KXMLSTOTAL|4 | 18 | -178 | -9.89 |
| KXMLSTOTAL|5 | 20 | -52 | -2.60 |
| KXMLSTOTAL|6 | 6 | -54 | -9.00 |
| KXMLSTOTAL|7 | 8 | -77 | -9.62 |
| KXUSLTOTAL|1 | 13 | -178 | -13.69 |
| KXUSLTOTAL|2 | 13 | -274 | -21.08 |
| KXUSLTOTAL|3 | 13 | -143 | -11.00 |
| KXUSLTOTAL|4 | 6 | -46 | -7.67 |
| KXUSLTOTAL|5 | 6 | -53 | -8.83 |
| KXUSLTOTAL|6 | 3 | 70 | 23.33 |

Robustness: net without top family `KXUSLTOTAL|6` = -1538c; net without top 2 trade(s) = -1640c.

## WEATHER_HIGH_BUCKET

families 2 · train points 54 · holdout points 24 · split at 2026-08-11T03:59:00Z · holdout priced 54%

### A-SIMPLE — streak count and directional reversal by k (TRAIN | HOLDOUT)

| streak of | k | train n | P(rev) | lb95 | hold n | P(rev) | lb95 |
|---|---|---|---|---|---|---|---|
| YES | 1 | 10 | 40.0% | 19.4% | 5 | 80.0% | 43.5% |
| YES | 2 | 5 | 40.0% | 14.3% | 2 | 100.0% | 42.5% |
| YES | 3 | 3 | 66.7% | 25.4% | 0 | — | — |
| YES | 4 | 1 | 100.0% | 27.0% | 0 | — | — |
| YES | 5 | 0 | — | — | 0 | — | — |
| YES | >=6 | 0 | — | — | 0 | — | — |
| NO | 1 | 10 | 20.0% | 6.9% | 5 | 20.0% | 4.6% |
| NO | 2 | 7 | 14.3% | 3.3% | 5 | 60.0% | 27.2% |
| NO | 3 | 6 | 33.3% | 11.7% | 2 | 50.0% | 12.1% |
| NO | 4 | 4 | 25.0% | 5.8% | 1 | 0.0% | 0.0% |
| NO | 5 | 3 | 33.3% | 7.8% | 1 | 0.0% | 0.0% |
| NO | >=6 | 5 | 40.0% | 14.3% | 3 | 33.3% | 7.8% |

Class YES rate: train 35.2%, holdout 29.2%.

### A-HIERARCHICAL — logistic P(YES) with ridge family effects (TRAIN fit)

| coefficient | estimate | approx SE | z |
|---|---|---|---|
| intercept | -0.5294 | 0.8568 | -0.62 |
| prev_dir (+1 YES / -1 NO) | 1.0927 | 0.4854 | 2.25 |
| ln(k) | -0.3737 | 0.5900 | -0.63 |
| prev_dir x ln(k) | -1.0027 | 0.6078 | -1.65 |

Family effects (ridge λ=1.0): B90.5=-0.38, B92.5=0.38

### Economics — TRAIN (in-sample, descriptive)

| arm | rule | N pred | N priced | accuracy | Brier | N trades | gross c | fees+slip c | net c | EV/trade c | avg edge c | return on risk | max DD c | worst streak | profit factor | mirror EV/trade c | YES / NO net c |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A0 | independence baseline (family base rate) | 54 | 0 | 64.8% | 0.2210 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |
| A1 | one-step transition | 54 | 0 | 70.4% | 0.2084 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |
| A2 | streak-length reversion (direction-specific) | 54 | 0 | 66.7% | 0.2077 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |
| A3 | hierarchical reversion (family effects) | 54 | 0 | 68.5% | 0.1954 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |

### Economics — HOLDOUT (the verdict)

| arm | rule | N pred | N priced | accuracy | Brier | N trades | gross c | fees+slip c | net c | EV/trade c | avg edge c | return on risk | max DD c | worst streak | profit factor | mirror EV/trade c | YES / NO net c |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A0 | independence baseline (family base rate) | 24 | 13 | 70.8% | 0.1998 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |
| A1 | one-step transition | 24 | 13 | 58.3% | 0.2456 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |
| A2 | streak-length reversion (direction-specific) | 24 | 13 | 50.0% | 0.2547 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |
| A3 | hierarchical reversion (family effects) | 24 | 13 | 58.3% | 0.2493 | 0 | 0 | 0 | 0 | — | — | — | 0 | 0 | — | — | 0 / 0 |

### Verdicts (HOLDOUT)

- **A0 — —**: baseline: comparator, not graded
- **A1 — HOLD**: train points 54 < 500; holdout trades 0 < 100
- **A2 — HOLD**: train points 54 < 500; holdout trades 0 < 100
- **A3 — HOLD**: train points 54 < 500; holdout trades 0 < 100

### Per-family HOLDOUT net P&L (A3, treatment)

| family | trades | net c | EV/trade c |
|---|---|---|---|

Robustness: net without top family `None` = —c; net without top 0 trade(s) = —c.

## TRACK A VERDICT: HOLD

A3 fails in 3 classes and is under-powered in 2; the track is not adequately answered
