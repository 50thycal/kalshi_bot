# MARKTANGLE-2 summary

| track | verdict | why |
|---|---|---|
| A | HOLD | A3 fails in 3 classes and is under-powered in 2; the track is not adequately answered |
| B | HOLD | B3 under-powered in every class (4) |

## Arms surviving on untouched holdout

- none

## Statistical but not economic

Arms whose holdout Brier beats the base rate while net P&L is non-positive — forecastability the price already carries:

- A2 in BASKETBALL_TOTAL
- A3 in BASKETBALL_TOTAL
- B3 in CRYPTO_DAILY:BTC
- A2 in FOOTBALL_TOTAL
- A3 in SOCCER_TOTAL

## Per-class primary verdicts

| class | track | primary | verdict | holdout trades | net c | EV/trade c | mirror EV c |
|---|---|---|---|---|---|---|---|
| BASEBALL_TOTAL | A | A3 | FAIL | 2040 | -6465 | -3.17 | -3.98 |
| BASKETBALL_TOTAL | A | A3 | FAIL | 243 | -1416 | -5.83 | -1.07 |
| CRYPTO_DAILY:BTC | B | B3 | HOLD | 2 | -27 | -13.50 | 7.50 |
| CRYPTO_DAILY:ETH | B | B3 | HOLD | 0 | 0 | — | — |
| CRYPTO_DAILY:SOL | B | B3 | HOLD | 0 | 0 | — | — |
| CRYPTO_DAILY:XRP | B | B3 | HOLD | 0 | 0 | — | — |
| FOOTBALL_TOTAL | A | A3 | HOLD | 54 | -529 | -9.80 | 2.44 |
| SOCCER_TOTAL | A | A3 | FAIL | 153 | -1468 | -9.59 | 1.82 |
| WEATHER_HIGH_BUCKET | A | A3 | HOLD | 0 | 0 | — | — |

## Exact next gate

- A PASS authorizes NOTHING live. The next gate for a passing track is a prospective paper/twin experiment registered in Experiment OS with its own pre-registered floors (`paper_to_live_canary_<track>` on the frozen v1 contract), never a live canary.
- A FAIL retires that track's thesis (§19). No broader classes, no re-read bars.
- A HOLD is no result: the named floor is the thing to satisfy (forward collection or price-data reconstruction), and the instrument is re-run unchanged.

## Reproducibility

| item | value |
|---|---|
| code SHA | 69337639146d7665ce8a5dcf74340a0f56382b05 |
| data cutoff (latest settled close) | 2026-09-02T16:00:00Z |
| universe fingerprint (sha256 of ticker,close,result) | 35b6d930c6668e1670e0b410427721d314d62b65ba7409f03378b1deeac87043 |
| trades fingerprint (sha256 of MARKTANGLE_2_TRADES.csv) | 51bbdc536f78e9065243a26827a505c6b1cfab73c89f021a58ee032f58c0731f |
| results fingerprint (sha256 of per-arm verdicts + holdout net) | 03efb4b72fc0ae34f075235bbe7774ad29230737e47024548265a68f71f44c2f |
| split | chronological per class, first 70% of decision times TRAIN |
| decision offset | T-60m |
| fee model | worst-case Kalshi taker, ceil(7 p (1-p)) c/contract, entry only |
| slippage | 1.0c/contract; spread screen <= 10.0c |
| edge bar | >= 3.0c net |
| floors | train >= 500 points; holdout >= 100 trades; price coverage >= 50% |
| mirror delta | >= 3.0c/trade |
| shrinkage / ridge | m=20.0; family ridge=1.0; slope ridge=0.001 |
| buckets | Track A k<= 5 then pooled; Track B ((1, 1), (2, 2), (3, 3), (4, 5), (6, 9), (10, 19), (20, 1000000000)); z bins (-6.0, -2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 6.0) |
| vol window | 20 daily closes, min 10 returns; |z| cap 6.0 |
| config | {"max_fetch": 6000, "min_vol": 0.0, "pages": 60, "series": ["KXBTCD", "KXETHD", "KXSOLD", "KXXRPD", "KXDOGED", "KXHIGHNY", "KXHIGHCHI", "KXHIGHLAX", "KXHIGHMIA", "KXHIGHAUS", "KXHIGHDEN", "KXHIGHPHIL", "KXHIGHSFO", "KXHIGHDC", "KXHIGHATL", "KXHIGHSEA", "KXHIGHDAL", "KXHIGHHOU", "KXHIGHLV", "KXUSLTOTAL", "KXLIGAMXSPREAD", "KXMLSTOTAL", "KXEPLTOTAL", "KXNBATOTAL", "KXWNBATOTAL", "KXMLBTOTAL", "KXNHLTOTAL", "KXNFLTOTAL", "KXNCAAFTOTAL"], "spot": true} |
| elapsed | 2060s |
