"""mmsell UNIVERSE REVIEW — what are we actually trading, and how well do we know it?

WHY THIS EXISTS
---------------
`mmsell_market_types` answers "what KIND of contract is this and what does it pay". This answers
a prior question that nothing asked before: **have we ever reviewed this contract at all?**

Measured 2026-09-05 across the whole mmsell family: 81 of 400 traded series are in NO taxonomy,
carrying 1,158 settled markets. 786 of those markets are the new season arriving —
KXNCAAFSPREAD (204), KXNCAAFTOTAL (184), KXEPLTOTAL (80), KXEPLSCORE (66), plus Serie A,
Bundesliga, Ligue 1 and NFL first-half. On the LIVE canary `Dmmsell10`, 20.2% of 30-day trades
were in unclassified series, against 0.7-6% on the older paper books — the share tracks NEW
LISTINGS, so it rises whenever Kalshi opens a season and nobody has classified it yet.

THIS IS NOT AN EDGE FILTER and must never be reported as one. The unclassified slice has been
PROFITABLE (+$45.18 all-time). A graduated series can be catastrophic: KXNFLSPREAD is
classified, has 382 settled markets, and has lost $166.55. Graduation means "we know what this
contract is and we have history on it", never "this contract makes money".

THE TIERS (kalshi_bot/mmsell/universe.py is the worker-side source of truth)

    GRADUATED     classified AND reviewed AND carrying own history -> tradeable live
    IN_REVIEW     classified but too thin to have been reviewed    -> paper only
    UNCLASSIFIED  in no taxonomy at all                            -> the review queue

WHAT TO DO WITH THE OUTPUT. The UNCLASSIFIED table is a work queue, ordered by supply: the top
rows are the series costing us the most coverage. Classifying one is a change to SERIES_TYPES,
which is shared platform semantics — it goes through Platform Change Review, using
`scripts/mmsell_taxonomy_audit.py` to gather the settlement evidence. Graduating one is a PR
adding its prefix to GRADUATED_SERIES. Neither happens automatically, deliberately: a series
that graduated itself by trading enough would defeat the review the tier exists to force.

DELIBERATE DUPLICATE of the worker's SERIES_TYPES and GRADUATED_SERIES, for the same reason
`mmsell_market_types` duplicates the first: ops-channel scripts must stay self-contained
(stdlib + psycopg only — they run on a runner that never installs this package).
`tests/test_mmsell_universe_review.py` asserts both copies match, so they cannot drift.

Read-only, self-contained; runs locally or via the ops channel:

    {"type": "script", "name": "mmsell_universe_review"}
    {"type": "script", "name": "mmsell_universe_review", "args": ["--days", "7"]}
    {"type": "script", "name": "mmsell_universe_review", "args": ["--all-strategies"]}
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

RO_OPTIONS = "-c default_transaction_read_only=on"

GRADUATED = "graduated"
IN_REVIEW = "in_review"
UNCLASSIFIED = "unclassified"

SERIES_TYPES: tuple[tuple[str, str, str], ...] = (
    ("KXMLBGAME", "h2h", "in_play"), ("KXNPBGAME", "h2h", "in_play"),
    ("KXWNBAGAME", "h2h", "in_play"), ("KXNBASUMMERGAME", "h2h", "in_play"),
    ("KXWCGAME", "h2h", "in_play"), ("KXMLSGAME", "h2h", "in_play"),
    ("KXNWSLGAME", "h2h", "in_play"), ("KXLIGAMXGAME", "h2h", "in_play"),
    ("KXCLUBFGAME", "h2h", "in_play"), ("KXUCLGAME", "h2h", "in_play"),
    ("KXECULPGAME", "h2h", "in_play"), ("KXBRASILEIROGAME", "h2h", "in_play"),
    ("KXBRASILEIROBGAME", "h2h", "in_play"), ("KXARGPREMDIVGAME", "h2h", "in_play"),
    ("KXALLSVENSKANGAME", "h2h", "in_play"), ("KXPERLIGA1GAME", "h2h", "in_play"),
    ("KXATPMATCH", "h2h", "in_play"), ("KXATPCHALLENGERMATCH", "h2h", "in_play"),
    ("KXWTAMATCH", "h2h", "in_play"), ("KXWTACHALLENGERMATCH", "h2h", "in_play"),
    ("KXITFMATCH", "h2h", "in_play"), ("KXITFWMATCH", "h2h", "in_play"),
    ("KXT20MATCH", "h2h", "in_play"), ("KXODIMATCH", "h2h", "in_play"),
    ("KXWODIMATCH", "h2h", "in_play"), ("KXTESTMATCH", "h2h", "in_play"),
    ("KXWTESTMATCH", "h2h", "in_play"), ("KXHUNDREDMATCH", "h2h", "in_play"),
    ("KXWHUNDREDMATCH", "h2h", "in_play"), ("KXUFCFIGHT", "h2h", "in_play"),
    ("KXBOXING", "h2h", "in_play"), ("KXLOLGAME", "h2h", "in_play"),
    ("KXCS2GAME", "h2h", "in_play"), ("KXCODGAME", "h2h", "in_play"),
    ("KXMLBHRDERBYMATCHUP", "h2h", "in_play"), ("KXWC1H", "h2h_period", "in_play"),
    ("KXWC2H", "h2h_period", "in_play"), ("KXATPSETWINNER", "h2h_period", "in_play"),
    ("KXMLBSPREAD", "spread", "in_play"), ("KXWCSPREAD", "spread", "in_play"),
    ("KXWNBASPREAD", "spread", "in_play"), ("KXNBASUMMERSPREAD", "spread", "in_play"),
    ("KXWCMOV", "spread", "in_play"), ("KXMLBTOTAL", "total", "in_play"),
    ("KXWNBATOTAL", "total", "in_play"), ("KXNBASUMMERTOTAL", "total", "in_play"),
    ("KXWCTOTAL", "total", "in_play"), ("KXWC1HTOTAL", "total", "in_play"),
    ("KXWCTEAMTOTAL", "total", "in_play"), ("KXLIGAMXTOTAL", "total", "in_play"),
    ("KXMLSTOTAL", "total", "in_play"), ("KXWCCORNERS", "total", "in_play"),
    ("KXWCTCORNERS", "total", "in_play"), ("KXMLBHRDERBYOU", "total", "in_play"),
    ("KXMLBHRDERBY500", "total", "in_play"), ("KXWCSCORE", "exact_score", "in_play"),
    ("KXWC1HSCORE", "exact_score", "in_play"), ("KXATPEXACTMATCH", "exact_score", "in_play"),
    ("KXMLBHR", "player_prop", "in_play"), ("KXMLBASGHR", "player_prop", "in_play"),
    ("KXWCGOAL", "player_prop", "in_play"), ("KXWCAST", "player_prop", "in_play"),
    ("KXWCSOA", "player_prop", "in_play"), ("KXMLBHRDERBYDISTANCE", "player_prop", "in_play"),
    ("KXMLBHRDERBYLONGEST", "player_prop", "in_play"), ("KXMLBHRDERBYFORECAST", "player_prop", "in_play"),
    ("KXWCFIRSTGOAL", "game_prop", "in_play"), ("KXWCFTTS", "game_prop", "in_play"),
    ("KXWCBTTS", "game_prop", "in_play"), ("KXWC1HBTTS", "game_prop", "in_play"),
    ("KXWC2HBTTS", "game_prop", "in_play"), ("KXWCMOF", "game_prop", "in_play"),
    ("KXUFCMOV", "game_prop", "in_play"), ("KXUFCVICROUND", "game_prop", "in_play"),
    ("KXPGATOUR", "outright", "in_play"), ("KXPGATOP5", "outright", "in_play"),
    ("KXPGATOP10", "outright", "in_play"), ("KXPGATOP20", "outright", "in_play"),
    ("KXPGAR3LEAD", "outright", "in_play"), ("KXLPGATOUR", "outright", "in_play"),
    ("KXLIVTOUR", "outright", "in_play"), ("KXDPWORLDTOUR", "outright", "in_play"),
    ("KXWTA", "outright", "in_play"), ("KXMLBHRDERBY", "outright", "in_play"),
    ("KXMLBHRDERBYSEMI", "outright", "in_play"), ("KXMLBHRDERBYR1LEAD", "outright", "in_play"),
    ("KXMLBASGMVP", "outright", "in_play"), ("KXWCAWARD", "outright", "discrete"),
    ("KXWCSTAGEOFELIM", "outright", "discrete"), ("KXWCMATCHUP", "outright", "discrete"),
    ("KXLIUSAELIMINATIONW", "outright", "discrete"), ("KXTRUMPSAY", "mention", "discrete"),
    ("KXTRUMPSAYMONTH", "mention", "discrete"), ("KXTRUMPSAYCOMPANY", "mention", "discrete"),
    ("KXFEDMENTION", "mention", "discrete"), ("KXWCMENTION", "mention", "discrete"),
    ("KXWCFIRSTSONG", "mention", "discrete"), ("KXBTCD", "price_strike", "scheduled"),
    ("KXBTCMAXMON", "price_strike", "scheduled"), ("KXWTI", "price_strike", "scheduled"),
    ("KXWTIW", "price_strike", "scheduled"), ("KXBRENTW", "price_strike", "scheduled"),
    ("KXAAAGASM", "price_strike", "scheduled"), ("KXAAAGASW", "price_strike", "scheduled"),
    ("KXFED", "econ_release", "scheduled"), ("KXFEDDECISION", "econ_release", "scheduled"),
    ("KXCPIYOY", "econ_release", "scheduled"), ("KXECONSTATCPIYOY", "econ_release", "scheduled"),
    ("KXRT", "rank_culture", "scheduled"), ("KXRANKLISTSONGSPOTUSA", "rank_culture", "scheduled"),
    ("KXNETFLIXRANKSHOW", "rank_culture", "scheduled"), ("KXTOPMODEL", "rank_culture", "discrete"),
    ("KXWCATTEND", "event_stat", "scheduled"), ("KXSPACEXCOUNT", "event_stat", "scheduled"),
    ("KXPLATNERDROPOUT", "politics", "discrete"), ("KXKASHOUT", "politics", "discrete"),
    ("KXMEDNOMJUL", "politics", "discrete"), ("KXNBATEAMANNOUNCE", "announcement", "discrete"),
    ("KXNFLSPREAD", "spread", "in_play"), ("KXNFLTOTAL", "total", "in_play"),
    ("KXNFLGAME", "h2h", "in_play"), ("KXLEAGUESCUPGAME", "h2h", "in_play"),
    ("KXLEAGUESCUPTOTAL", "total", "in_play"), ("KXLEAGUESCUPSPREAD", "spread", "in_play"),
    ("KXMLBKS", "player_prop", "in_play"), ("KXMLBHIT", "player_prop", "in_play"),
    ("KXMLBTEAMTOTAL", "total", "in_play"), ("KXMLBF5TOTAL", "total", "in_play"),
    ("KXMLBF5", "h2h_period", "in_play"), ("KXWNBAPTS", "player_prop", "in_play"),
    ("KXUEFASCSCORE", "exact_score", "in_play"), ("KXUEFASCGAME", "h2h", "in_play"),
    ("KXUEFASCTOTAL", "total", "in_play"), ("KXUCLTOTAL", "total", "in_play"),
    ("KXUCLSPREAD", "spread", "in_play"), ("KXCLUBFTOTAL", "total", "in_play"),
    ("KXDIMAYORGAME", "h2h", "in_play"), ("KXCONMEBOLLIBGAME", "h2h", "in_play"),
    ("KXCONMEBOLSUDGAME", "h2h", "in_play"), ("KXATPDOUBLES", "h2h", "in_play"),
    ("KXATPGTOTAL", "total", "in_play"), ("KXBTC", "price_strike", "scheduled"),
    ("KXETHD", "price_strike", "scheduled"), ("KXGOLDD", "price_strike", "scheduled"),
    ("KXNATGASD", "price_strike", "scheduled"), ("KXBRENTD", "price_strike", "scheduled"),
    ("KXNASDAQ100U", "price_strike", "scheduled"), ("KXINXU", "price_strike", "scheduled"),
    ("KXAAAGASD", "price_strike", "scheduled"), ("KXH200WS", "price_strike", "scheduled"),
    ("KXCPICOMBO", "econ_release", "scheduled"), ("KXECONSTATCPI", "econ_release", "scheduled"),
    ("KXCPINDEX", "econ_release", "scheduled"), ("KXCPI", "econ_release", "scheduled"),
    ("KXUSGASCPI", "econ_release", "scheduled"), ("KXARMOMINF", "econ_release", "scheduled"),
    ("KXUE", "econ_release", "scheduled"), ("KXALBUMEQUIV", "rank_culture", "discrete"),
    ("KXTRUTHSOCIAL", "mention", "discrete"), ("KXGEMINI", "announcement", "discrete"),
    ("KXGPT", "announcement", "discrete"), ("KXAPRPOTUS", "politics", "scheduled"),
    ("KXRAIN", "event_stat", "scheduled"), ("KXHMONTH", "event_stat", "scheduled"),
    ("KXENGCSSCORE", "exact_score", "in_play"), ("KXLALIGASCORE", "exact_score", "in_play"),
    ("KXLEAGUESCUPSCORE", "exact_score", "in_play"), ("KXLIGAMXSCORE", "exact_score", "in_play"),
    ("KXMLSSCORE", "exact_score", "in_play"), ("KXCLUBFBTTS", "game_prop", "in_play"),
    ("KXLEAGUESCUPBTTS", "game_prop", "in_play"), ("KXPGAHOLEINONE", "game_prop", "in_play"),
    ("KXUCLBTTS", "game_prop", "in_play"), ("KXAFLGAME", "h2h", "in_play"),
    ("KXAPFDDHGAME", "h2h", "in_play"), ("KXARGNACBGAME", "h2h", "in_play"),
    ("KXASEANGAME", "h2h", "in_play"), ("KXBELGIANPLGAME", "h2h", "in_play"),
    ("KXBOLPDIVGAME", "h2h", "in_play"), ("KXBRASILEIROCGAME", "h2h", "in_play"),
    ("KXBUNDESLIGA2GAME", "h2h", "in_play"), ("KXCHLLDPGAME", "h2h", "in_play"),
    ("KXCHNSLGAME", "h2h", "in_play"), ("KXCOPPAITALIAGAME", "h2h", "in_play"),
    ("KXCPLMATCH", "h2h", "in_play"), ("KXCZEFLGAME", "h2h", "in_play"),
    ("KXDENSUPERLIGAGAME", "h2h", "in_play"), ("KXDOTA2GAME", "h2h", "in_play"),
    ("KXEFLCHAMPIONSHIPGAME", "h2h", "in_play"), ("KXEFLL1GAME", "h2h", "in_play"),
    ("KXELITESERIENGAME", "h2h", "in_play"), ("KXENGCSGAME", "h2h", "in_play"),
    ("KXEPLGAME", "h2h", "in_play"), ("KXEREDIVISIEGAME", "h2h", "in_play"),
    ("KXHNLGAME", "h2h", "in_play"), ("KXITFDOUBLES", "h2h", "in_play"),
    ("KXITFWDOUBLES", "h2h", "in_play"), ("KXJ2LEAGUEGAME", "h2h", "in_play"),
    ("KXJLEAGUEGAME", "h2h", "in_play"), ("KXKBOGAME", "h2h", "in_play"),
    ("KXKLEAGUEGAME", "h2h", "in_play"), ("KXLALIGA2GAME", "h2h", "in_play"),
    ("KXLALIGAGAME", "h2h", "in_play"), ("KXLIGAEXPGAME", "h2h", "in_play"),
    ("KXLIGAPORTUGALGAME", "h2h", "in_play"), ("KXLNBPGAME", "h2h", "in_play"),
    ("KXR6GAME", "h2h", "in_play"), ("KXSAUDIPLGAME", "h2h", "in_play"),
    ("KXSUPERLIGGAME", "h2h", "in_play"), ("KXSVK2LGAME", "h2h", "in_play"),
    ("KXUAEPLGAME", "h2h", "in_play"), ("KXURYPDGAME", "h2h", "in_play"),
    ("KXUSLGAME", "h2h", "in_play"), ("KXVALORANTGAME", "h2h", "in_play"),
    ("KXVENFUTVEGAME", "h2h", "in_play"), ("KXCS2MAP", "h2h_period", "in_play"),
    ("KXDOTA2MAP", "h2h_period", "in_play"), ("KXLEAGUESCUP1H", "h2h_period", "in_play"),
    ("KXLOLMAP", "h2h_period", "in_play"), ("KXMLBF3", "h2h_period", "in_play"),
    ("KXMLS1H", "h2h_period", "in_play"), ("KXWNBA1HWINNER", "h2h_period", "in_play"),
    ("KXWNBA2QWINNER", "h2h_period", "in_play"), ("KXCHAMPTOUR", "outright", "in_play"),
    ("KXCHESSTOURNAMENT", "outright", "in_play"), ("KXCOD", "outright", "in_play"),
    ("KXKFTOUR", "outright", "in_play"), ("KXNASCARRACE", "outright", "in_play"),
    ("KXNASCARTOP10", "outright", "in_play"), ("KXNASCARTOP3", "outright", "in_play"),
    ("KXMLBRBI", "player_prop", "in_play"), ("KXMLBSB", "player_prop", "in_play"),
    ("KXMLBTB", "player_prop", "in_play"), ("KXNFLPASSYDS", "player_prop", "in_play"),
    ("KXNFLRSHYDS", "player_prop", "in_play"), ("KXNFLTD", "player_prop", "in_play"),
    ("KXALLSVENSKANSPREAD", "spread", "in_play"), ("KXAPFDDHSPREAD", "spread", "in_play"),
    ("KXARGPREMDIVSPREAD", "spread", "in_play"), ("KXASEANSPREAD", "spread", "in_play"),
    ("KXATPGSPREAD", "spread", "in_play"), ("KXBELGIANPLSPREAD", "spread", "in_play"),
    ("KXCHNSLSPREAD", "spread", "in_play"), ("KXCLUBFSPREAD", "spread", "in_play"),
    ("KXCONMEBOLSUDSPREAD", "spread", "in_play"), ("KXECULPSPREAD", "spread", "in_play"),
    ("KXEFLCHAMPIONSHIPSPREAD", "spread", "in_play"), ("KXELITESERIENSPREAD", "spread", "in_play"),
    ("KXENGCSSPREAD", "spread", "in_play"), ("KXEREDIVISIESPREAD", "spread", "in_play"),
    ("KXKBOSPREAD", "spread", "in_play"), ("KXLALIGASPREAD", "spread", "in_play"),
    ("KXLIGAMXSPREAD", "spread", "in_play"), ("KXLIGAPORTUGALSPREAD", "spread", "in_play"),
    ("KXMLSSPREAD", "spread", "in_play"), ("KXNPBSPREAD", "spread", "in_play"),
    ("KXSUPERLIGSPREAD", "spread", "in_play"), ("KXURYPDSPREAD", "spread", "in_play"),
    ("KXWNBA1HSPREAD", "spread", "in_play"), ("KXWNBA2QSPREAD", "spread", "in_play"),
    ("KXALLSVENSKANTOTAL", "total", "in_play"), ("KXAPFDDHTOTAL", "total", "in_play"),
    ("KXARGPREMDIVTOTAL", "total", "in_play"), ("KXASEANTOTAL", "total", "in_play"),
    ("KXBELGIANPLTOTAL", "total", "in_play"), ("KXBRASILEIROBTOTAL", "total", "in_play"),
    ("KXBRASILEIROCTOTAL", "total", "in_play"), ("KXBRASILEIROTOTAL", "total", "in_play"),
    ("KXBUNDESLIGA2TOTAL", "total", "in_play"), ("KXCHLLDPTOTAL", "total", "in_play"),
    ("KXCHNSLTOTAL", "total", "in_play"), ("KXCONMEBOLLIBTOTAL", "total", "in_play"),
    ("KXCONMEBOLSUDTOTAL", "total", "in_play"), ("KXCOPPAITALIATOTAL", "total", "in_play"),
    ("KXCZEFNLTOTAL", "total", "in_play"), ("KXDENSUPERLIGATOTAL", "total", "in_play"),
    ("KXDIMAYORTOTAL", "total", "in_play"), ("KXECULPTOTAL", "total", "in_play"),
    ("KXEFLCHAMPIONSHIPTOTAL", "total", "in_play"), ("KXEFLCUPTOTAL", "total", "in_play"),
    ("KXEFLL1TOTAL", "total", "in_play"), ("KXEKSTRAKLASATOTAL", "total", "in_play"),
    ("KXELITESERIENTOTAL", "total", "in_play"), ("KXENGCSTOTAL", "total", "in_play"),
    ("KXEREDIVISIETOTAL", "total", "in_play"), ("KXFINYLTOTAL", "total", "in_play"),
    ("KXJLEAGUETOTAL", "total", "in_play"), ("KXLALIGATOTAL", "total", "in_play"),
    ("KXLEAGUESCUP1HTOTAL", "total", "in_play"), ("KXLEAGUESCUPTEAMTOTAL", "total", "in_play"),
    ("KXLIGAEXPTOTAL", "total", "in_play"), ("KXLIGAMX1HTOTAL", "total", "in_play"),
    ("KXLIGAMXTEAMTOTAL", "total", "in_play"), ("KXLIGAPORTUGALTOTAL", "total", "in_play"),
    ("KXNFL1HTOTAL", "total", "in_play"), ("KXNPBTOTAL", "total", "in_play"),
    ("KXNWSLTOTAL", "total", "in_play"), ("KXPERLIGA1TOTAL", "total", "in_play"),
    ("KXSAUDIPLTOTAL", "total", "in_play"), ("KXSCOTTISHPREMTOTAL", "total", "in_play"),
    ("KXSUPERLIGTOTAL", "total", "in_play"), ("KXUAEPLTOTAL", "total", "in_play"),
    ("KXUCL1HTOTAL", "total", "in_play"), ("KXWNBA1HTOTAL", "total", "in_play"),
    ("KXWNBA1QTOTAL", "total", "in_play"), ("KXWNBA2QTOTAL", "total", "in_play"),
    ("KXWNBA4QTOTAL", "total", "in_play"), ("KXWNBATEAMTOTAL", "total", "in_play"),
    ("KXBKNUGGETS", "econ_release", "scheduled"), ("KXECONSTATCORECPIYOY", "econ_release", "scheduled"),
    ("KXDEEPSHARE", "event_stat", "scheduled"), ("KXGOOGSHARE", "event_stat", "scheduled"),
    ("KXHORMUZWEEKLY", "event_stat", "scheduled"), ("KXTRUMPACT", "event_stat", "scheduled"),
    ("KXA100WS", "price_strike", "scheduled"), ("KXB200WS", "price_strike", "scheduled"),
    ("KXBRENTMON", "price_strike", "scheduled"), ("KXCOPPERD", "price_strike", "scheduled"),
    ("KXEURUSD", "price_strike", "scheduled"), ("KXEURUSDAW", "price_strike", "scheduled"),
    ("KXGOLDMON", "price_strike", "scheduled"), ("KXGOLDW", "price_strike", "scheduled"),
    ("KXH100WS", "price_strike", "scheduled"), ("KXH200MS", "price_strike", "scheduled"),
    ("KXINX", "price_strike", "scheduled"), ("KXNASDAQ100", "price_strike", "scheduled"),
    ("KXSILVERD", "price_strike", "scheduled"), ("KXUSDJPY", "price_strike", "scheduled"),
    ("KXBILLBOARDRUNNERUPSONG", "rank_culture", "scheduled"), ("KXCHINAAI", "rank_culture", "scheduled"),
    ("KXNETFLIXRANKMOVIE", "rank_culture", "scheduled"), ("KXNETFLIXRANKMOVIERUNNERUP", "rank_culture", "scheduled"),
    ("KXNETFLIXTOPVIEWSMOVIE", "rank_culture", "scheduled"), ("KXNETFLIXTOPVIEWSTV", "rank_culture", "scheduled"),
    ("KXPUREALBUMS", "rank_culture", "scheduled"), ("KXYTDAILYTOPVIDEOG", "rank_culture", "scheduled"),
    ("KXYTTOPSONGW", "rank_culture", "scheduled"), ("KXYTTOPVIDEO2D", "rank_culture", "scheduled"),
    ("KXYTTOPVIDEOG2D", "rank_culture", "scheduled"), ("KXFDAANNOUNCE", "announcement", "discrete"),
    ("KXGROK", "announcement", "discrete"), ("KXPRESSSECANNOUNCE", "announcement", "discrete"),
    ("KXEARTHQUAKEM", "event_stat", "discrete"), ("KXSPACEXSTARSHIP", "event_stat", "discrete"),
    ("KXYTVIEWSHIGH", "event_stat", "discrete"), ("KXYTVIEWSW", "event_stat", "discrete"),
    ("KXBIGBROTHERELIMINATION", "outright", "discrete"), ("KXCLARITYVOTE", "politics", "discrete"),
    ("KXDIAZOUT", "politics", "discrete"), ("KXDROPOUTPRIMARY", "politics", "discrete"),
    ("KXHEGSETHANNOUNCEOUT", "politics", "discrete"), ("KXKASHANNOUNCEOUT", "politics", "discrete"),
    ("KXMEXCUBOIL", "politics", "discrete"), ("KXPIRROOUT", "politics", "discrete"),
    ("KXTRUMPMEET", "politics", "discrete"), ("KXTRUMPUFC", "politics", "discrete"),
    ("KXUAPFILES", "politics", "discrete"), ("KXBNBMINMON", "price_strike", "discrete"),
)

GRADUATED_SERIES: frozenset[str] = frozenset({
    "KXAAAGASD", "KXAAAGASW", "KXALBUMEQUIV",
    "KXALLSVENSKANGAME", "KXALLSVENSKANTOTAL", "KXAPFDDHTOTAL",
    "KXARGPREMDIVGAME", "KXARGPREMDIVTOTAL", "KXATPCHALLENGERMATCH",
    "KXATPEXACTMATCH", "KXATPGSPREAD", "KXATPMATCH",
    "KXATPSETWINNER", "KXBRASILEIROGAME", "KXBRASILEIROTOTAL",
    "KXBRENTD", "KXBRENTW", "KXBTC",
    "KXBTCD", "KXCHAMPTOUR", "KXCHNSLGAME",
    "KXCHNSLTOTAL", "KXCLUBFGAME", "KXCLUBFSPREAD",
    "KXCLUBFTOTAL", "KXCONMEBOLSUDTOTAL", "KXCOPPERD",
    "KXCPLMATCH", "KXCS2GAME", "KXCS2MAP",
    "KXDIMAYORGAME", "KXDOTA2GAME", "KXDPWORLDTOUR",
    "KXECULPGAME", "KXECULPTOTAL", "KXEFLCHAMPIONSHIPGAME",
    "KXEFLCHAMPIONSHIPTOTAL", "KXEPLGAME", "KXETHD",
    "KXFEDMENTION", "KXGOLDD", "KXGOLDW",
    "KXINX", "KXINXU", "KXITFMATCH",
    "KXITFWMATCH", "KXJLEAGUEGAME", "KXJLEAGUETOTAL",
    "KXKBOGAME", "KXKFTOUR", "KXLALIGAGAME",
    "KXLALIGASCORE", "KXLALIGASPREAD", "KXLALIGATOTAL",
    "KXLEAGUESCUPGAME", "KXLEAGUESCUPSCORE", "KXLEAGUESCUPSPREAD",
    "KXLEAGUESCUPTOTAL", "KXLIGAMXGAME", "KXLIGAMXSCORE",
    "KXLIGAMXSPREAD", "KXLIGAMXTOTAL", "KXLOLGAME",
    "KXLOLMAP", "KXLPGATOUR", "KXMLBASGHR",
    "KXMLBF5", "KXMLBF5SPREAD", "KXMLBF5TOTAL",
    "KXMLBGAME", "KXMLBHIT", "KXMLBHR",
    "KXMLBHRR", "KXMLBKS", "KXMLBSPREAD",
    "KXMLBTB", "KXMLBTEAMTOTAL", "KXMLBTOTAL",
    "KXMLSGAME", "KXMLSSCORE", "KXMLSSPREAD",
    "KXMLSTOTAL", "KXNASDAQ100U", "KXNATGASD",
    "KXNBASUMMERGAME", "KXNFLGAME", "KXNFLPASSYDS",
    "KXNFLSPREAD", "KXNFLTOTAL", "KXNPBGAME",
    "KXNPBTOTAL", "KXNWSLGAME", "KXODIMATCH",
    "KXPERLIGA1GAME", "KXPGATOUR", "KXRAIN",
    "KXRT", "KXSAUDIPLGAME", "KXT20MATCH",
    "KXTESTMATCH", "KXTRUMPSAY", "KXTRUTHSOCIAL",
    "KXUCLGAME", "KXUCLTOTAL", "KXUECLTOTAL",
    "KXUFCFIGHT", "KXUFCMOV", "KXUFCVICROUND",
    "KXVALORANTGAME", "KXWC1H", "KXWC1HSCORE",
    "KXWC1HTOTAL", "KXWC2H", "KXWCAST",
    "KXWCCORNERS", "KXWCFIRSTGOAL", "KXWCGAME",
    "KXWCGOAL", "KXWCMENTION", "KXWCMOV",
    "KXWCSCORE", "KXWCSOA", "KXWCSPREAD",
    "KXWCTCORNERS", "KXWCTEAMTOTAL", "KXWCTOTAL",
    "KXWNBA1HTOTAL", "KXWNBAGAME", "KXWNBAPTS",
    "KXWNBASPREAD", "KXWNBATOTAL", "KXWTACHALLENGERMATCH",
    "KXWTAMATCH", "KXWTASETWINNER", "KXWTI",
    "KXWTIW", "KXYTVIEWSHIGH", "KXYTVIEWSW",
})


def classify(series: str) -> tuple[str, str]:
    """(market_type, settle_mode); ("unclassified", "unknown") when the table has no entry.
    Longest-prefix match, exactly as the worker's copy does."""
    s = (series or "").upper()
    best = None
    for prefix, mtype, mode in SERIES_TYPES:
        if s.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, mtype, mode)
    return (best[1], best[2]) if best else ("unclassified", "unknown")


def tier_of(series: str) -> str:
    s = (series or "").upper()
    if classify(s) == ("unclassified", "unknown"):
        return UNCLASSIFIED
    if any(s.startswith(p) for p in GRADUATED_SERIES):
        return GRADUATED
    return IN_REVIEW


def _to_libpq_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgres://", "postgresql://")


def load_rows(cur, days: int, all_strategies: bool):
    where = "" if all_strategies else "and pt.strategy like '%%mmsell%%'"
    cur.execute(f"""
        select pt.strategy,
               coalesce(m.series_ticker, split_part(pt.market_ticker, '-', 1)) as series,
               pt.market_ticker, pt.status, pt.pnl
          from paper_trades pt
          left join mmsell_settlement_meta m on m.market_ticker = pt.market_ticker
         where pt.created_at >= now() - make_interval(days => %s) {where}
    """, (days,))
    return cur.fetchall()


def report(rows, days: int) -> None:
    settled = ("settled", "closed_sl")
    by_strategy = defaultdict(lambda: defaultdict(lambda: [0, 0.0, set()]))
    by_series = defaultdict(lambda: [0, 0.0, set()])
    for strategy, series, _ticker, status, pnl in rows:
        tier = tier_of(series)
        cell = by_strategy[strategy][tier]
        cell[0] += 1
        cell[2].add(series)
        s_cell = by_series[(series, tier)]
        s_cell[0] += 1
        s_cell[2].add(strategy)
        if status in settled and pnl is not None:
            cell[1] += float(pnl)
            s_cell[1] += float(pnl)

    print(f"=== UNIVERSE REVIEW — last {days} days ===\n")
    print("Tier share per strategy. `unclass%` is the share of trades in series NO taxonomy")
    print("covers — the book cannot say what kind of contract it sold.\n")
    hdr = f"{'strategy':<18} {'trades':>7} {'grad%':>7} {'rev%':>7} {'unclass%':>9} {'unclass_series':>15}"
    print(hdr)
    print("-" * len(hdr))
    for strategy in sorted(by_strategy, key=lambda k: -sum(c[0] for c in by_strategy[k].values())):
        tiers = by_strategy[strategy]
        total = sum(c[0] for c in tiers.values())
        if total < 20:
            continue
        g = tiers.get(GRADUATED, [0, 0.0, set()])
        r = tiers.get(IN_REVIEW, [0, 0.0, set()])
        u = tiers.get(UNCLASSIFIED, [0, 0.0, set()])
        print(f"{strategy:<18} {total:>7} {100 * g[0] / total:>6.1f}% {100 * r[0] / total:>6.1f}% "
              f"{100 * u[0] / total:>8.1f}% {len(u[2]):>15}")

    for tier, title, note in (
        (UNCLASSIFIED, "UNCLASSIFIED — the review queue",
         "In no taxonomy. Not tradeable live. Classify via mmsell_taxonomy_audit -> "
         "Platform Change Review."),
        (IN_REVIEW, "IN_REVIEW — classified, too thin to graduate",
         "Paper only. Graduates by PR to GRADUATED_SERIES once its history is reviewed."),
    ):
        cells = [(s, c) for (s, t), c in by_series.items() if t == tier]
        cells.sort(key=lambda kv: -kv[1][0])
        print(f"\n--- {title} ({len(cells)} series) ---")
        print(f"    {note}")
        if not cells:
            print("    (none)")
            continue
        print(f"\n    {'series':<28} {'trades':>7} {'pnl':>9}  books")
        for series, c in cells[:40]:
            books = ",".join(sorted(c[2])[:3]) + ("..." if len(c[2]) > 3 else "")
            print(f"    {series:<28} {c[0]:>7} {c[1]:>9.2f}  {books}")
        if len(cells) > 40:
            print(f"    ... and {len(cells) - 40} more")

    print("\nREAD THIS BEFORE ACTING ON THE P&L COLUMN: it is not why the tiers exist. An")
    print("unclassified series can be profitable and a graduated one can bleed (KXNFLSPREAD is")
    print("graduated and has lost $166.55 all-time). The tier says how well we KNOW the")
    print("contract, never how well it pays.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=30, help="lookback window (default 30)")
    ap.add_argument("--all-strategies", action="store_true",
                    help="include non-mmsell books (their series may be covered by a "
                         "DIFFERENT taxonomy, so unclassified there is not necessarily a gap)")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            rows = load_rows(cur, args.days, args.all_strategies)
    report(rows, args.days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
