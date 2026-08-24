# MMSELL settlement-taxonomy repair — CENSUS BASELINE and FROZEN REVIEW MANIFEST

**Recorded before any classification decision was made.** The batch below is
frozen: every prefix in it is reviewed, and the review does not stop when the
census crosses 5%.

---

## 1. Reproduction of the canonical baseline

| field | value |
|---|---|
| script | `scripts/mmsell_taxonomy_audit.py` |
| ops request id | `mmt-base-1` |
| invocation | `{"type":"script","name":"mmsell_taxonomy_audit","args":["--since","2026-07-19","--until","2026-08-21","--top","200","--dump-text"]}` |
| runner commit SHA | `1b6d7bd5bdbf2fd564bac5d6af6daf009e19ebd6` |
| window | `captured_at >= 2026-07-19` and `< 2026-08-21` (canonical) |
| price band | yes mid **5–7¢ inclusive**, `BAND = (5.0, 7.0)` |
| candidate construction | `SELECT DISTINCT ON (market_ticker) ... WHERE mid BETWEEN 5 AND 7 ORDER BY market_ticker, captured_at ASC` — **the first tick AT WHICH the market is in band**, not the market's first-ever tick |
| universe | `mmsell_candidate_ticks`, non-crypto (`KXBTC/KXETH/KXSOL/KXXRP/KXDOGE` excluded) |
| unclassified bar | 5% (pre-registered, **unchanged**) |

### 1.1 Census by settlement mode — matches the last reproducible baseline exactly

| settle mode | markets | share | prior baseline |
|---|---|---|---|
| `in_play` | 4,374 | 72.68% | 4,374 / 72.68% ✅ |
| **`unknown`** | **861** | **14.31%** | 861 / 14.31% ✅ |
| `scheduled` | 671 | 11.15% | 671 / 11.15% ✅ |
| `discrete` | 112 | 1.86% | 112 / 1.86% ✅ |
| **TOTAL** | **6,018** | | 6,018 ✅ |

`unclassified_excluded_pct = 14.31%` against a 5% bar → **FAIL / `BLOCKED_DATA`**.
Distinct series in the population: 319. Unclassified prefixes: **198** — matches.

**No material difference from the baseline.** The census is byte-for-byte the same
population. The taxonomy repair may proceed.

### 1.2 Document coverage

| field | this run | prior baseline | note |
|---|---|---|---|
| prefixes with Kalshi rules text | 198 / 198 | 198 / 198 | — |
| unique Kalshi markets inspected | 1,561 | 1,558 | up to 8 per prefix, `settled` then `open` |
| DISTINCT rule documents | 1,561 | 1,558 | deduplicated on normalized `rules+source` |
| `settlement_source` populated | **0 of 1,561** | 0 | Kalshi returns no settlement source for any of these series; the strong signal available is **title + rules text** |
| DB `markets` rows for these tickers | 0 | 0 | the database cannot supply the evidence; the public Kalshi endpoint can |
| audit auto-proposals | 45 prefixes / 526 markets | 43 / 501 | +2 prefixes, +25 markets |
| audit `INSUFFICIENT_EVIDENCE` | 153 prefixes / 335 markets | 155 / 360 | |
| census if every auto-proposal accepted | 5.57% | 5.98% | **still above 5%** either way |

The ±2-prefix drift is **not a census difference**. The census is identical. The
audit samples up to eight *currently listed* markets per prefix from `settled`+`open`,
and which markets are open moves day to day, so two long-tail prefixes that returned
a below-threshold sample on 2026-08-21 returned a sufficient one on 2026-08-24. The
gated number — `unclassified_excluded_pct` — is unchanged at 14.31%.

### 1.3 Arithmetic of the gate

| target | unknown markets allowed | markets that must be resolved |
|---|---|---|
| the fixed 5% gate | ≤ 300 | ≥ 561 of 861 |
| the 4% safety margin sought | ≤ 240 | ≥ 621 of 861 |

Accepting only the audit's 45 auto-proposals resolves 526 and lands at **5.57%** —
short of the gate. The frozen batch below is therefore the **whole** unresolved
population, not a slice sized to clear it.

---

## 2. FROZEN REVIEW MANIFEST

### 2.1 Selection criterion

**All 198 unresolved prefixes in the canonical census — the complete population.**

Selecting the whole population is strictly stronger than the ranked-cutoff rule the
brief permits, and it removes the failure mode the freeze exists to prevent: with no
cutoff there is no cutoff to move, and no prefix can be admitted or dropped because
of which side of 5% it would put the census on. The 43/45 evidence-backed
auto-proposals are a **subset** of this batch and are re-reviewed on the same footing
as the rest, not inherited.

Ordering is `candidate-market count DESC, then prefix ASC` (deterministic, ties
included by construction since nothing is cut).

### 2.2 Commitments frozen with this batch

1. **Every one of the 198 prefixes is reviewed**, and review continues past the point
   at which the running census would cross 5%.
2. A prefix is classified **only** on a strong signal — Kalshi's published title and
   rules text (its `settlement_source` is empty for all 1,561 documents), with no
   conflict between strong signals and agreement among the documents inspected for
   that prefix.
3. **Weak evidence cannot decide anything.** `GAP h` (median |expiration − close|)
   and `PATH` (share still mid-book at the last tick) corroborate only.
4. **`can_close_early` has no vote.** It is set on 100% of these markets, index-close
   ones included.
5. A **bare clock time** ("at 8:10 PM EDT") is not evidence of `scheduled` — Kalshi
   writes it on in-play markets as the game's start time.
6. Ambiguous, internally inconsistent, or thin-evidence prefixes **stay unknown**.
   No catch-all default converts an unknown series into an eligible mode.
7. **No arm economics, P&L, treatment effects or model residuals** are inspected
   while deciding classifications.
8. Every proposed addition and every boundary case gets a **second independent
   review pass**.

### 2.3 The batch

Evidence for every row — Kalshi's own titles and rules text, verbatim — is in
`EVIDENCE_20260824.txt` beside this file, and in full in ops result `mmt-base-1`.

`AUDIT` is what `scripts/mmsell_taxonomy_audit.py` proposed automatically. It is a
reading aid recorded for provenance; it is **not** the decision.

| # | prefix | candidate markets | cumulative | cum. % of 6,018 | AUDIT |
|---|---|---|---|---|---|
| 1 | `KXMLSSCORE` | 46 | 46 | 0.76% | in_play |
| 2 | `KXMLBTB` | 43 | 89 | 1.48% | in_play |
| 3 | `KXLEAGUESCUPSCORE` | 42 | 131 | 2.18% | in_play |
| 4 | `KXLIGAMXSCORE` | 39 | 170 | 2.82% | in_play |
| 5 | `KXINX` | 21 | 191 | 3.17% | scheduled |
| 6 | `KXYTVIEWSW` | 21 | 212 | 3.52% | INSUFFICIENT_EVIDENCE |
| 7 | `KXKFTOUR` | 19 | 231 | 3.84% | in_play |
| 8 | `KXCOPPERD` | 18 | 249 | 4.14% | scheduled |
| 9 | `KXARGPREMDIVTOTAL` | 16 | 265 | 4.40% | in_play |
| 10 | `KXCLUBFSPREAD` | 15 | 280 | 4.65% | in_play |
| 11 | `KXKBOGAME` | 13 | 293 | 4.87% | in_play |
| 12 | `KXMLSSPREAD` | 13 | 306 | 5.08% | in_play |
| 13 | `KXSILVERD` | 13 | 319 | 5.30% | scheduled |
| 14 | `KXBRASILEIROTOTAL` | 12 | 331 | 5.50% | in_play |
| 15 | `KXCONMEBOLSUDTOTAL` | 11 | 342 | 5.68% | in_play |
| 16 | `KXENGCSSCORE` | 11 | 353 | 5.87% | in_play |
| 17 | `KXBIGBROTHERELIMINATION` | 10 | 363 | 6.03% | INSUFFICIENT_EVIDENCE |
| 18 | `KXYTVIEWSHIGH` | 10 | 373 | 6.20% | INSUFFICIENT_EVIDENCE |
| 19 | `KXALLSVENSKANTOTAL` | 9 | 382 | 6.35% | in_play |
| 20 | `KXCONMEBOLLIBTOTAL` | 9 | 391 | 6.50% | in_play |
| 21 | `KXNASDAQ100` | 9 | 400 | 6.65% | scheduled |
| 22 | `KXNFLPASSYDS` | 9 | 409 | 6.80% | in_play |
| 23 | `KXBELGIANPLTOTAL` | 8 | 417 | 6.93% | in_play |
| 24 | `KXCHAMPTOUR` | 8 | 425 | 7.06% | INSUFFICIENT_EVIDENCE |
| 25 | `KXECULPTOTAL` | 8 | 433 | 7.20% | in_play |
| 26 | `KXLALIGATOTAL` | 8 | 441 | 7.33% | in_play |
| 27 | `KXLIGAMXSPREAD` | 8 | 449 | 7.46% | in_play |
| 28 | `KXVALORANTGAME` | 8 | 457 | 7.59% | in_play |
| 29 | `KXHORMUZWEEKLY` | 7 | 464 | 7.71% | INSUFFICIENT_EVIDENCE |
| 30 | `KXLALIGASCORE` | 7 | 471 | 7.83% | in_play |
| 31 | `KXLOLMAP` | 7 | 478 | 7.94% | in_play |
| 32 | `KXNFLTD` | 7 | 485 | 8.06% | in_play |
| 33 | `KXNWSLTOTAL` | 7 | 492 | 8.18% | in_play |
| 34 | `KXEFLCHAMPIONSHIPTOTAL` | 6 | 498 | 8.28% | in_play |
| 35 | `KXLALIGASPREAD` | 6 | 504 | 8.37% | in_play |
| 36 | `KXNASCARTOP3` | 6 | 510 | 8.47% | in_play |
| 37 | `KXSUPERLIGTOTAL` | 6 | 516 | 8.57% | in_play |
| 38 | `KXTRUEV` | 6 | 522 | 8.67% | INSUFFICIENT_EVIDENCE |
| 39 | `KXWNBA1HTOTAL` | 6 | 528 | 8.77% | in_play |
| 40 | `KXASEANTOTAL` | 5 | 533 | 8.86% | in_play |
| 41 | `KXB200WS` | 5 | 538 | 8.94% | INSUFFICIENT_EVIDENCE |
| 42 | `KXBRASILEIROBTOTAL` | 5 | 543 | 9.02% | in_play |
| 43 | `KXBRENTMON` | 5 | 548 | 9.11% | scheduled |
| 44 | `KXCHNSLSPREAD` | 5 | 553 | 9.19% | in_play |
| 45 | `KXCS2MAP` | 5 | 558 | 9.27% | in_play |
| 46 | `KXEFLCHAMPIONSHIPGAME` | 5 | 563 | 9.36% | in_play |
| 47 | `KXEREDIVISIETOTAL` | 5 | 568 | 9.44% | in_play |
| 48 | `KXEURUSD` | 5 | 573 | 9.52% | INSUFFICIENT_EVIDENCE |
| 49 | `KXGOLDW` | 5 | 578 | 9.60% | scheduled |
| 50 | `KXLIGAPORTUGALGAME` | 5 | 583 | 9.69% | in_play |
| 51 | `KXLIGAPORTUGALTOTAL` | 5 | 588 | 9.77% | in_play |
| 52 | `KXNETFLIXTOPVIEWSTV` | 5 | 593 | 9.85% | INSUFFICIENT_EVIDENCE |
| 53 | `KXNPBTOTAL` | 5 | 598 | 9.94% | in_play |
| 54 | `KXSAUDIPLGAME` | 5 | 603 | 10.02% | in_play |
| 55 | `KXYTTOPVIDEOG2D` | 5 | 608 | 10.10% | INSUFFICIENT_EVIDENCE |
| 56 | `KXCHESSTOURNAMENT` | 4 | 612 | 10.17% | INSUFFICIENT_EVIDENCE |
| 57 | `KXCHLLDPGAME` | 4 | 616 | 10.24% | INSUFFICIENT_EVIDENCE |
| 58 | `KXDOTA2GAME` | 4 | 620 | 10.30% | INSUFFICIENT_EVIDENCE |
| 59 | `KXELITESERIENTOTAL` | 4 | 624 | 10.37% | INSUFFICIENT_EVIDENCE |
| 60 | `KXEREDIVISIEGAME` | 4 | 628 | 10.44% | INSUFFICIENT_EVIDENCE |
| 61 | `KXGOLDMON` | 4 | 632 | 10.50% | INSUFFICIENT_EVIDENCE |
| 62 | `KXLALIGA2GAME` | 4 | 636 | 10.57% | INSUFFICIENT_EVIDENCE |
| 63 | `KXLIGAEXPGAME` | 4 | 640 | 10.63% | INSUFFICIENT_EVIDENCE |
| 64 | `KXNASCARRACE` | 4 | 644 | 10.70% | INSUFFICIENT_EVIDENCE |
| 65 | `KXPERLIGA1TOTAL` | 4 | 648 | 10.77% | INSUFFICIENT_EVIDENCE |
| 66 | `KXUSLGAME` | 4 | 652 | 10.83% | INSUFFICIENT_EVIDENCE |
| 67 | `KXAPFDDHTOTAL` | 3 | 655 | 10.88% | INSUFFICIENT_EVIDENCE |
| 68 | `KXARGNACBGAME` | 3 | 658 | 10.93% | INSUFFICIENT_EVIDENCE |
| 69 | `KXATPGSPREAD` | 3 | 661 | 10.98% | INSUFFICIENT_EVIDENCE |
| 70 | `KXBKNUGGETS` | 3 | 664 | 11.03% | INSUFFICIENT_EVIDENCE |
| 71 | `KXBRASILEIROCGAME` | 3 | 667 | 11.08% | INSUFFICIENT_EVIDENCE |
| 72 | `KXCHLLDPTOTAL` | 3 | 670 | 11.13% | INSUFFICIENT_EVIDENCE |
| 73 | `KXCHNSLGAME` | 3 | 673 | 11.18% | INSUFFICIENT_EVIDENCE |
| 74 | `KXCHNSLTOTAL` | 3 | 676 | 11.23% | INSUFFICIENT_EVIDENCE |
| 75 | `KXCPLMATCH` | 3 | 679 | 11.28% | INSUFFICIENT_EVIDENCE |
| 76 | `KXDIESELD` | 3 | 682 | 11.33% | INSUFFICIENT_EVIDENCE |
| 77 | `KXDIMAYORTOTAL` | 3 | 685 | 11.38% | INSUFFICIENT_EVIDENCE |
| 78 | `KXEURUSDAW` | 3 | 688 | 11.43% | INSUFFICIENT_EVIDENCE |
| 79 | `KXJLEAGUEGAME` | 3 | 691 | 11.48% | INSUFFICIENT_EVIDENCE |
| 80 | `KXJLEAGUETOTAL` | 3 | 694 | 11.53% | INSUFFICIENT_EVIDENCE |
| 81 | `KXLALIGAGAME` | 3 | 697 | 11.58% | INSUFFICIENT_EVIDENCE |
| 82 | `KXNETFLIXTOPVIEWSMOVIE` | 3 | 700 | 11.63% | INSUFFICIENT_EVIDENCE |
| 83 | `KXSUPERLIGSPREAD` | 3 | 703 | 11.68% | INSUFFICIENT_EVIDENCE |
| 84 | `KXUSDJPY` | 3 | 706 | 11.73% | INSUFFICIENT_EVIDENCE |
| 85 | `KXWNBA1HSPREAD` | 3 | 709 | 11.78% | INSUFFICIENT_EVIDENCE |
| 86 | `KXWNBATEAMTOTAL` | 3 | 712 | 11.83% | INSUFFICIENT_EVIDENCE |
| 87 | `KXAFLGAME` | 2 | 714 | 11.86% | INSUFFICIENT_EVIDENCE |
| 88 | `KXALLSVENSKANSPREAD` | 2 | 716 | 11.90% | INSUFFICIENT_EVIDENCE |
| 89 | `KXAPFDDHGAME` | 2 | 718 | 11.93% | INSUFFICIENT_EVIDENCE |
| 90 | `KXBELGIANPLGAME` | 2 | 720 | 11.96% | INSUFFICIENT_EVIDENCE |
| 91 | `KXBILLBOARDRUNNERUPSONG` | 2 | 722 | 12.00% | INSUFFICIENT_EVIDENCE |
| 92 | `KXBNBMINMON` | 2 | 724 | 12.03% | INSUFFICIENT_EVIDENCE |
| 93 | `KXBUNDESLIGA2TOTAL` | 2 | 726 | 12.06% | INSUFFICIENT_EVIDENCE |
| 94 | `KXCONMEBOLSUDSPREAD` | 2 | 728 | 12.10% | INSUFFICIENT_EVIDENCE |
| 95 | `KXCOPPAITALIATOTAL` | 2 | 730 | 12.13% | INSUFFICIENT_EVIDENCE |
| 96 | `KXCZEFLGAME` | 2 | 732 | 12.16% | INSUFFICIENT_EVIDENCE |
| 97 | `KXDENSUPERLIGAGAME` | 2 | 734 | 12.20% | INSUFFICIENT_EVIDENCE |
| 98 | `KXDENSUPERLIGATOTAL` | 2 | 736 | 12.23% | INSUFFICIENT_EVIDENCE |
| 99 | `KXDROPOUTPRIMARY` | 2 | 738 | 12.26% | INSUFFICIENT_EVIDENCE |
| 100 | `KXECONSTATCORECPIYOY` | 2 | 740 | 12.30% | INSUFFICIENT_EVIDENCE |
| 101 | `KXECULPSPREAD` | 2 | 742 | 12.33% | INSUFFICIENT_EVIDENCE |
| 102 | `KXEFLL1TOTAL` | 2 | 744 | 12.36% | INSUFFICIENT_EVIDENCE |
| 103 | `KXELITESERIENGAME` | 2 | 746 | 12.40% | INSUFFICIENT_EVIDENCE |
| 104 | `KXFINYLTOTAL` | 2 | 748 | 12.43% | INSUFFICIENT_EVIDENCE |
| 105 | `KXGOOGSHARE` | 2 | 750 | 12.46% | INSUFFICIENT_EVIDENCE |
| 106 | `KXGROK` | 2 | 752 | 12.50% | INSUFFICIENT_EVIDENCE |
| 107 | `KXH100WS` | 2 | 754 | 12.53% | INSUFFICIENT_EVIDENCE |
| 108 | `KXITFDOUBLES` | 2 | 756 | 12.56% | INSUFFICIENT_EVIDENCE |
| 109 | `KXLEAGUESCUP1H` | 2 | 758 | 12.60% | INSUFFICIENT_EVIDENCE |
| 110 | `KXLEAGUESCUP1HTOTAL` | 2 | 760 | 12.63% | INSUFFICIENT_EVIDENCE |
| 111 | `KXLNBPGAME` | 2 | 762 | 12.66% | INSUFFICIENT_EVIDENCE |
| 112 | `KXMLBSB` | 2 | 764 | 12.70% | INSUFFICIENT_EVIDENCE |
| 113 | `KXNETFLIXRANKMOVIERUNNERUP` | 2 | 766 | 12.73% | INSUFFICIENT_EVIDENCE |
| 114 | `KXNFLRSHYDS` | 2 | 768 | 12.76% | INSUFFICIENT_EVIDENCE |
| 115 | `KXPGAHOLEINONE` | 2 | 770 | 12.79% | INSUFFICIENT_EVIDENCE |
| 116 | `KXPUREALBUMS` | 2 | 772 | 12.83% | INSUFFICIENT_EVIDENCE |
| 117 | `KXSUPERLIGGAME` | 2 | 774 | 12.86% | INSUFFICIENT_EVIDENCE |
| 118 | `KXTRUMPACT` | 2 | 776 | 12.89% | INSUFFICIENT_EVIDENCE |
| 119 | `KXUAEPLGAME` | 2 | 778 | 12.93% | INSUFFICIENT_EVIDENCE |
| 120 | `KXUCL1HTOTAL` | 2 | 780 | 12.96% | INSUFFICIENT_EVIDENCE |
| 121 | `KXURYPDGAME` | 2 | 782 | 12.99% | INSUFFICIENT_EVIDENCE |
| 122 | `KXWNBA1HWINNER` | 2 | 784 | 13.03% | INSUFFICIENT_EVIDENCE |
| 123 | `KXWNBA2QTOTAL` | 2 | 786 | 13.06% | INSUFFICIENT_EVIDENCE |
| 124 | `KXA100WS` | 1 | 787 | 13.08% | INSUFFICIENT_EVIDENCE |
| 125 | `KXAPFDDHSPREAD` | 1 | 788 | 13.09% | INSUFFICIENT_EVIDENCE |
| 126 | `KXARGPREMDIVSPREAD` | 1 | 789 | 13.11% | INSUFFICIENT_EVIDENCE |
| 127 | `KXASEANGAME` | 1 | 790 | 13.13% | INSUFFICIENT_EVIDENCE |
| 128 | `KXASEANSPREAD` | 1 | 791 | 13.14% | INSUFFICIENT_EVIDENCE |
| 129 | `KXBELGIANPLSPREAD` | 1 | 792 | 13.16% | INSUFFICIENT_EVIDENCE |
| 130 | `KXBOLPDIVGAME` | 1 | 793 | 13.18% | INSUFFICIENT_EVIDENCE |
| 131 | `KXBRASILEIROCTOTAL` | 1 | 794 | 13.19% | INSUFFICIENT_EVIDENCE |
| 132 | `KXBUNDESLIGA2GAME` | 1 | 795 | 13.21% | INSUFFICIENT_EVIDENCE |
| 133 | `KXCHINAAI` | 1 | 796 | 13.23% | INSUFFICIENT_EVIDENCE |
| 134 | `KXCLARITYVOTE` | 1 | 797 | 13.24% | INSUFFICIENT_EVIDENCE |
| 135 | `KXCLUBFBTTS` | 1 | 798 | 13.26% | INSUFFICIENT_EVIDENCE |
| 136 | `KXCOD` | 1 | 799 | 13.28% | INSUFFICIENT_EVIDENCE |
| 137 | `KXCOPPAITALIAGAME` | 1 | 800 | 13.29% | INSUFFICIENT_EVIDENCE |
| 138 | `KXCZEFNLTOTAL` | 1 | 801 | 13.31% | INSUFFICIENT_EVIDENCE |
| 139 | `KXDEEPSHARE` | 1 | 802 | 13.33% | INSUFFICIENT_EVIDENCE |
| 140 | `KXDIAZOUT` | 1 | 803 | 13.34% | INSUFFICIENT_EVIDENCE |
| 141 | `KXDIESELW` | 1 | 804 | 13.36% | INSUFFICIENT_EVIDENCE |
| 142 | `KXDOTA2MAP` | 1 | 805 | 13.38% | INSUFFICIENT_EVIDENCE |
| 143 | `KXEARTHQUAKEM` | 1 | 806 | 13.39% | INSUFFICIENT_EVIDENCE |
| 144 | `KXEFLCHAMPIONSHIPSPREAD` | 1 | 807 | 13.41% | INSUFFICIENT_EVIDENCE |
| 145 | `KXEFLCUPTOTAL` | 1 | 808 | 13.43% | INSUFFICIENT_EVIDENCE |
| 146 | `KXEFLL1GAME` | 1 | 809 | 13.44% | INSUFFICIENT_EVIDENCE |
| 147 | `KXEKSTRAKLASATOTAL` | 1 | 810 | 13.46% | INSUFFICIENT_EVIDENCE |
| 148 | `KXELITESERIENSPREAD` | 1 | 811 | 13.48% | INSUFFICIENT_EVIDENCE |
| 149 | `KXENGCSGAME` | 1 | 812 | 13.49% | INSUFFICIENT_EVIDENCE |
| 150 | `KXENGCSSPREAD` | 1 | 813 | 13.51% | INSUFFICIENT_EVIDENCE |
| 151 | `KXENGCSTOTAL` | 1 | 814 | 13.53% | INSUFFICIENT_EVIDENCE |
| 152 | `KXEPLGAME` | 1 | 815 | 13.54% | INSUFFICIENT_EVIDENCE |
| 153 | `KXEREDIVISIESPREAD` | 1 | 816 | 13.56% | INSUFFICIENT_EVIDENCE |
| 154 | `KXFDAANNOUNCE` | 1 | 817 | 13.58% | INSUFFICIENT_EVIDENCE |
| 155 | `KXH200MS` | 1 | 818 | 13.59% | INSUFFICIENT_EVIDENCE |
| 156 | `KXHEGSETHANNOUNCEOUT` | 1 | 819 | 13.61% | INSUFFICIENT_EVIDENCE |
| 157 | `KXHNLGAME` | 1 | 820 | 13.63% | INSUFFICIENT_EVIDENCE |
| 158 | `KXITFWDOUBLES` | 1 | 821 | 13.64% | INSUFFICIENT_EVIDENCE |
| 159 | `KXJ2LEAGUEGAME` | 1 | 822 | 13.66% | INSUFFICIENT_EVIDENCE |
| 160 | `KXKASHANNOUNCEOUT` | 1 | 823 | 13.68% | INSUFFICIENT_EVIDENCE |
| 161 | `KXKBOSPREAD` | 1 | 824 | 13.69% | INSUFFICIENT_EVIDENCE |
| 162 | `KXKLEAGUEGAME` | 1 | 825 | 13.71% | INSUFFICIENT_EVIDENCE |
| 163 | `KXLEAGUESCUPBTTS` | 1 | 826 | 13.73% | INSUFFICIENT_EVIDENCE |
| 164 | `KXLEAGUESCUPTEAMTOTAL` | 1 | 827 | 13.74% | INSUFFICIENT_EVIDENCE |
| 165 | `KXLIGAEXPTOTAL` | 1 | 828 | 13.76% | INSUFFICIENT_EVIDENCE |
| 166 | `KXLIGAMX1HTOTAL` | 1 | 829 | 13.78% | INSUFFICIENT_EVIDENCE |
| 167 | `KXLIGAMXTEAMTOTAL` | 1 | 830 | 13.79% | INSUFFICIENT_EVIDENCE |
| 168 | `KXLIGAPORTUGALSPREAD` | 1 | 831 | 13.81% | INSUFFICIENT_EVIDENCE |
| 169 | `KXMC` | 1 | 832 | 13.83% | INSUFFICIENT_EVIDENCE |
| 170 | `KXMEXCUBOIL` | 1 | 833 | 13.84% | INSUFFICIENT_EVIDENCE |
| 171 | `KXMLBF3` | 1 | 834 | 13.86% | INSUFFICIENT_EVIDENCE |
| 172 | `KXMLBRBI` | 1 | 835 | 13.88% | INSUFFICIENT_EVIDENCE |
| 173 | `KXMLS1H` | 1 | 836 | 13.89% | INSUFFICIENT_EVIDENCE |
| 174 | `KXNASCARTOP10` | 1 | 837 | 13.91% | INSUFFICIENT_EVIDENCE |
| 175 | `KXNETFLIXRANKMOVIE` | 1 | 838 | 13.92% | INSUFFICIENT_EVIDENCE |
| 176 | `KXNFL1HTOTAL` | 1 | 839 | 13.94% | INSUFFICIENT_EVIDENCE |
| 177 | `KXNPBSPREAD` | 1 | 840 | 13.96% | INSUFFICIENT_EVIDENCE |
| 178 | `KXPIRROOUT` | 1 | 841 | 13.97% | INSUFFICIENT_EVIDENCE |
| 179 | `KXPRESSSECANNOUNCE` | 1 | 842 | 13.99% | INSUFFICIENT_EVIDENCE |
| 180 | `KXR6GAME` | 1 | 843 | 14.01% | INSUFFICIENT_EVIDENCE |
| 181 | `KXSAUDIPLTOTAL` | 1 | 844 | 14.02% | INSUFFICIENT_EVIDENCE |
| 182 | `KXSCOTTISHPREMTOTAL` | 1 | 845 | 14.04% | INSUFFICIENT_EVIDENCE |
| 183 | `KXSPACEXSTARSHIP` | 1 | 846 | 14.06% | INSUFFICIENT_EVIDENCE |
| 184 | `KXSVK2LGAME` | 1 | 847 | 14.07% | INSUFFICIENT_EVIDENCE |
| 185 | `KXTRUMPMEET` | 1 | 848 | 14.09% | INSUFFICIENT_EVIDENCE |
| 186 | `KXTRUMPUFC` | 1 | 849 | 14.11% | INSUFFICIENT_EVIDENCE |
| 187 | `KXUAEPLTOTAL` | 1 | 850 | 14.12% | INSUFFICIENT_EVIDENCE |
| 188 | `KXUAPFILES` | 1 | 851 | 14.14% | INSUFFICIENT_EVIDENCE |
| 189 | `KXUCLBTTS` | 1 | 852 | 14.16% | INSUFFICIENT_EVIDENCE |
| 190 | `KXURYPDSPREAD` | 1 | 853 | 14.17% | INSUFFICIENT_EVIDENCE |
| 191 | `KXVENFUTVEGAME` | 1 | 854 | 14.19% | INSUFFICIENT_EVIDENCE |
| 192 | `KXWNBA1QTOTAL` | 1 | 855 | 14.21% | INSUFFICIENT_EVIDENCE |
| 193 | `KXWNBA2QSPREAD` | 1 | 856 | 14.22% | INSUFFICIENT_EVIDENCE |
| 194 | `KXWNBA2QWINNER` | 1 | 857 | 14.24% | INSUFFICIENT_EVIDENCE |
| 195 | `KXWNBA4QTOTAL` | 1 | 858 | 14.26% | INSUFFICIENT_EVIDENCE |
| 196 | `KXYTDAILYTOPVIDEOG` | 1 | 859 | 14.27% | INSUFFICIENT_EVIDENCE |
| 197 | `KXYTTOPSONGW` | 1 | 860 | 14.29% | INSUFFICIENT_EVIDENCE |
| 198 | `KXYTTOPVIDEO2D` | 1 | 861 | 14.31% | INSUFFICIENT_EVIDENCE |

**Total: 198 prefixes, 861 candidate markets (14.31% of the eligible population).**
