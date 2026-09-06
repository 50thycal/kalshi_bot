"""Market-type vocabulary for the mmsell books (worker side).

DELIBERATE DUPLICATE of `scripts/mmsell_market_types.py`'s SERIES_TYPES table, for the same
reason `kalshi_bot/mmsell/regimes.py` duplicates `scripts/mmsell_seasonal.py`: the ops-channel
analysis scripts must stay self-contained (stdlib + psycopg only — they run on a GitHub Actions
runner that never installs this package), so the worker cannot import them and they cannot
import the worker. `tests/test_mmsell_market_type_books.py` asserts the two tables are
identical, so the copy cannot drift silently; change one and the test tells you to change the
other.

Why the worker needs the taxonomy at all: the W/T book families (docs/MMSELL_TYPE_BOOKS.md)
select candidates by CONTRACT STRUCTURE rather than by series substring. The pre-existing
`only=`/`skip=` filters match raw series substrings, which cannot express "every in-play
market" without enumerating 60-odd series prefixes by hand and silently missing every new one
Kalshi lists. A book filtering on `mtype=`/`mode=` picks up a brand-new series the moment it is
added to the table here.

A series with no entry classifies as `unclassified`/`unknown` and is admitted by NO type or
mode filter — an unknown contract is never silently swept into a book that did not ask for it.
The census script prints unclassified series with their volume, which is how a new one gets
noticed and added.
"""

from __future__ import annotations

# Settle modes. Orthogonal to type, and the axis any entry-timing rule has to respect:
#   in_play    resolves through a live contest; the clock runs and the outcome is progressively
#              revealed, so "1 hour to close" means the outcome is nearly determined
#   scheduled  resolves at a fixed instant off an external print; nothing is revealed early, so
#              "1 hour to close" means nothing has happened yet
#   discrete   resolves whenever an event does or does not occur in a window; no monotone path
IN_PLAY = "in_play"
SCHEDULED = "scheduled"
DISCRETE = "discrete"

UNCLASSIFIED = ("unclassified", "unknown")

# (series_prefix, market_type, settle_mode). LONGEST prefix wins, so ordering is for human
# reading only. Keep byte-identical to scripts/mmsell_market_types.py's SERIES_TYPES.
SERIES_TYPES: tuple[tuple[str, str, str], ...] = (
    # --- head-to-head winners -------------------------------------------------------------
    ("KXMLBGAME", "h2h", IN_PLAY),
    ("KXNPBGAME", "h2h", IN_PLAY),
    ("KXWNBAGAME", "h2h", IN_PLAY),
    ("KXNBASUMMERGAME", "h2h", IN_PLAY),
    ("KXWCGAME", "h2h", IN_PLAY),
    ("KXMLSGAME", "h2h", IN_PLAY),
    ("KXNWSLGAME", "h2h", IN_PLAY),
    ("KXLIGAMXGAME", "h2h", IN_PLAY),
    ("KXCLUBFGAME", "h2h", IN_PLAY),
    ("KXUCLGAME", "h2h", IN_PLAY),
    ("KXECULPGAME", "h2h", IN_PLAY),
    ("KXBRASILEIROGAME", "h2h", IN_PLAY),
    ("KXBRASILEIROBGAME", "h2h", IN_PLAY),
    ("KXARGPREMDIVGAME", "h2h", IN_PLAY),
    ("KXALLSVENSKANGAME", "h2h", IN_PLAY),
    ("KXPERLIGA1GAME", "h2h", IN_PLAY),
    ("KXATPMATCH", "h2h", IN_PLAY),
    ("KXATPCHALLENGERMATCH", "h2h", IN_PLAY),
    ("KXWTAMATCH", "h2h", IN_PLAY),
    ("KXWTACHALLENGERMATCH", "h2h", IN_PLAY),
    ("KXITFMATCH", "h2h", IN_PLAY),
    ("KXITFWMATCH", "h2h", IN_PLAY),
    ("KXT20MATCH", "h2h", IN_PLAY),
    ("KXODIMATCH", "h2h", IN_PLAY),
    ("KXWODIMATCH", "h2h", IN_PLAY),
    ("KXTESTMATCH", "h2h", IN_PLAY),
    ("KXWTESTMATCH", "h2h", IN_PLAY),
    ("KXHUNDREDMATCH", "h2h", IN_PLAY),
    ("KXWHUNDREDMATCH", "h2h", IN_PLAY),
    ("KXUFCFIGHT", "h2h", IN_PLAY),
    ("KXBOXING", "h2h", IN_PLAY),
    ("KXLOLGAME", "h2h", IN_PLAY),
    ("KXCS2GAME", "h2h", IN_PLAY),
    ("KXCODGAME", "h2h", IN_PLAY),
    ("KXMLBHRDERBYMATCHUP", "h2h", IN_PLAY),
    # --- period winners -------------------------------------------------------------------
    ("KXWC1H", "h2h_period", IN_PLAY),
    ("KXWC2H", "h2h_period", IN_PLAY),
    ("KXATPSETWINNER", "h2h_period", IN_PLAY),
    # --- spreads / handicaps --------------------------------------------------------------
    ("KXMLBSPREAD", "spread", IN_PLAY),
    ("KXWCSPREAD", "spread", IN_PLAY),
    ("KXWNBASPREAD", "spread", IN_PLAY),
    ("KXNBASUMMERSPREAD", "spread", IN_PLAY),
    ("KXWCMOV", "spread", IN_PLAY),
    # --- totals ---------------------------------------------------------------------------
    ("KXMLBTOTAL", "total", IN_PLAY),
    ("KXWNBATOTAL", "total", IN_PLAY),
    ("KXNBASUMMERTOTAL", "total", IN_PLAY),
    ("KXWCTOTAL", "total", IN_PLAY),
    ("KXWC1HTOTAL", "total", IN_PLAY),
    ("KXWCTEAMTOTAL", "total", IN_PLAY),
    ("KXLIGAMXTOTAL", "total", IN_PLAY),
    ("KXMLSTOTAL", "total", IN_PLAY),
    ("KXWCCORNERS", "total", IN_PLAY),
    ("KXWCTCORNERS", "total", IN_PLAY),
    ("KXMLBHRDERBYOU", "total", IN_PLAY),
    ("KXMLBHRDERBY500", "total", IN_PLAY),
    # --- exact score ----------------------------------------------------------------------
    ("KXWCSCORE", "exact_score", IN_PLAY),
    ("KXWC1HSCORE", "exact_score", IN_PLAY),
    ("KXATPEXACTMATCH", "exact_score", IN_PLAY),
    # --- player props ---------------------------------------------------------------------
    ("KXMLBHR", "player_prop", IN_PLAY),
    ("KXMLBASGHR", "player_prop", IN_PLAY),
    ("KXWCGOAL", "player_prop", IN_PLAY),
    ("KXWCAST", "player_prop", IN_PLAY),
    ("KXWCSOA", "player_prop", IN_PLAY),
    ("KXMLBHRDERBYDISTANCE", "player_prop", IN_PLAY),
    ("KXMLBHRDERBYLONGEST", "player_prop", IN_PLAY),
    ("KXMLBHRDERBYFORECAST", "player_prop", IN_PLAY),
    # --- game props -----------------------------------------------------------------------
    ("KXWCFIRSTGOAL", "game_prop", IN_PLAY),
    ("KXWCFTTS", "game_prop", IN_PLAY),
    ("KXWCBTTS", "game_prop", IN_PLAY),
    ("KXWC1HBTTS", "game_prop", IN_PLAY),
    ("KXWC2HBTTS", "game_prop", IN_PLAY),
    ("KXWCMOF", "game_prop", IN_PLAY),
    ("KXUFCMOV", "game_prop", IN_PLAY),
    ("KXUFCVICROUND", "game_prop", IN_PLAY),
    # --- outrights / futures --------------------------------------------------------------
    ("KXPGATOUR", "outright", IN_PLAY),
    ("KXPGATOP5", "outright", IN_PLAY),
    ("KXPGATOP10", "outright", IN_PLAY),
    ("KXPGATOP20", "outright", IN_PLAY),
    ("KXPGAR3LEAD", "outright", IN_PLAY),
    ("KXLPGATOUR", "outright", IN_PLAY),
    ("KXLIVTOUR", "outright", IN_PLAY),
    ("KXDPWORLDTOUR", "outright", IN_PLAY),
    ("KXWTA", "outright", IN_PLAY),
    ("KXMLBHRDERBY", "outright", IN_PLAY),
    ("KXMLBHRDERBYSEMI", "outright", IN_PLAY),
    ("KXMLBHRDERBYR1LEAD", "outright", IN_PLAY),
    ("KXMLBASGMVP", "outright", IN_PLAY),
    ("KXWCAWARD", "outright", DISCRETE),
    ("KXWCSTAGEOFELIM", "outright", DISCRETE),
    ("KXWCMATCHUP", "outright", DISCRETE),
    ("KXLIUSAELIMINATIONW", "outright", DISCRETE),
    # --- mention / speech -----------------------------------------------------------------
    ("KXTRUMPSAY", "mention", DISCRETE),
    ("KXTRUMPSAYMONTH", "mention", DISCRETE),
    ("KXTRUMPSAYCOMPANY", "mention", DISCRETE),
    ("KXFEDMENTION", "mention", DISCRETE),
    ("KXWCMENTION", "mention", DISCRETE),
    ("KXWCFIRSTSONG", "mention", DISCRETE),
    # --- scheduled price strikes ----------------------------------------------------------
    ("KXBTCD", "price_strike", SCHEDULED),
    ("KXBTCMAXMON", "price_strike", SCHEDULED),
    ("KXWTI", "price_strike", SCHEDULED),
    ("KXWTIW", "price_strike", SCHEDULED),
    ("KXBRENTW", "price_strike", SCHEDULED),
    ("KXAAAGASM", "price_strike", SCHEDULED),
    ("KXAAAGASW", "price_strike", SCHEDULED),
    # --- scheduled economic prints --------------------------------------------------------
    ("KXFED", "econ_release", SCHEDULED),
    ("KXFEDDECISION", "econ_release", SCHEDULED),
    ("KXCPIYOY", "econ_release", SCHEDULED),
    ("KXECONSTATCPIYOY", "econ_release", SCHEDULED),
    # --- rank / culture -------------------------------------------------------------------
    ("KXRT", "rank_culture", SCHEDULED),
    ("KXRANKLISTSONGSPOTUSA", "rank_culture", SCHEDULED),
    ("KXNETFLIXRANKSHOW", "rank_culture", SCHEDULED),
    ("KXTOPMODEL", "rank_culture", DISCRETE),
    # --- event stats ----------------------------------------------------------------------
    ("KXWCATTEND", "event_stat", SCHEDULED),
    ("KXSPACEXCOUNT", "event_stat", SCHEDULED),
    # --- discrete political events --------------------------------------------------------
    ("KXPLATNERDROPOUT", "politics", DISCRETE),
    ("KXKASHOUT", "politics", DISCRETE),
    ("KXMEDNOMJUL", "politics", DISCRETE),
    # --- announcements --------------------------------------------------------------------
    ("KXNBATEAMANNOUNCE", "announcement", DISCRETE),
    # ===== 2026-08-13 EXTENSION ==========================================================
    # Half of all candidate flow was unclassified, so every mtype=/mode= book had been
    # selecting from ~50% of the universe without that being visible anywhere. Each entry
    # below is classified from the series' OWN live subtitle (quoted after it), fetched
    # from production rather than guessed from the ticker — a wrong guess here silently
    # sweeps markets into books that did not ask for them.
    # --- in-play sports. These were HALF the candidate flow and invisible to every mtype=/mode=
    #     book, because an unclassified series is admitted by no allowlist filter.
    ("KXNFLSPREAD", "spread", IN_PLAY),  # Tennessee wins by over 9.5 points
    ("KXNFLTOTAL", "total", IN_PLAY),  # Over 60.5 points scored
    ("KXNFLGAME", "h2h", IN_PLAY),  # Washington
    ("KXLEAGUESCUPGAME", "h2h", IN_PLAY),  # Vancouver
    ("KXLEAGUESCUPTOTAL", "total", IN_PLAY),  # Over 6.5 goals scored
    ("KXLEAGUESCUPSPREAD", "spread", IN_PLAY),  # Vancouver wins by more than 1.5 goals
    ("KXMLBKS", "player_prop", IN_PLAY),  # Zebby Matthews: 9+ (strikeouts)
    ("KXMLBHIT", "player_prop", IN_PLAY),  # Zach Neto: 2+ (hits)
    ("KXMLBTEAMTOTAL", "total", IN_PLAY),  # Washington over 7.5 runs scored
    ("KXMLBF5TOTAL", "total", IN_PLAY),  # Over 6.5 runs in the first 5 innings
    ("KXMLBF5", "h2h_period", IN_PLAY),  # Washington wins first 5 innings
    ("KXWNBAPTS", "player_prop", IN_PLAY),  # Veronica Burton: 20+
    ("KXUEFASCSCORE", "exact_score", IN_PLAY),  # Reg Time: PSG wins 4-1
    ("KXUEFASCGAME", "h2h", IN_PLAY),  # Reg Time: Tie
    ("KXUEFASCTOTAL", "total", IN_PLAY),  # Reg Time: Over 6.5 goals scored
    # UEFA Europa League and Europa Conference League. These need EXPLICIT entries because the
    # bare "KXUE" econ prefix below (unemployment) would otherwise swallow them by prefix match
    # and classify live European football as a scheduled economic release. It did: measured
    # 2026-09-06, 270 settled paper trades across six KXUE* soccer series were carrying
    # `econ_release`/`scheduled`, so every `mode=scheduled` book took them as if they printed
    # off a statistical release and every `mode=in_play` book missed them. `KXUEFASC*` above is
    # the same collision, worked around once already for the Super Cup.
    ("KXUECLGAME", "h2h", IN_PLAY),          # Europa Conference League, match winner
    ("KXUECLTOTAL", "total", IN_PLAY),       # UECL goals total
    ("KXUECL1HTOTAL", "total", IN_PLAY),     # UECL first-half goals
    ("KXUELGAME", "h2h", IN_PLAY),           # Europa League, match winner
    ("KXUELTOTAL", "total", IN_PLAY),        # UEL goals total
    ("KXUEFASCSPREAD", "spread", IN_PLAY),   # Super Cup handicap; sibling of the entries above
    ("KXUCLTOTAL", "total", IN_PLAY),  # Reg Time: Over 6.5 goals scored
    ("KXUCLSPREAD", "spread", IN_PLAY),  # Goal Diff Reg Time: wins by more than 1.5 goals
    ("KXCLUBFTOTAL", "total", IN_PLAY),  # Over 8.5 goals scored
    ("KXDIMAYORGAME", "h2h", IN_PLAY),  # Tie
    ("KXCONMEBOLLIBGAME", "h2h", IN_PLAY),  # Universidad Catolica
    ("KXCONMEBOLSUDGAME", "h2h", IN_PLAY),  # Tigre
    ("KXATPDOUBLES", "h2h", IN_PLAY),  # Guido Andreozzi / Manuel Guinard
    ("KXATPGTOTAL", "total", IN_PLAY),  # Over 29.5 games
    # --- price strikes — a level printed at a fixed instant; nothing is revealed early
    ("KXBTC", "price_strike", SCHEDULED),  # $66,000 to 66,499.99 (hourly BTC)
    ("KXETHD", "price_strike", SCHEDULED),  # $1,980 or above
    ("KXGOLDD", "price_strike", SCHEDULED),  # Above $4496
    ("KXNATGASD", "price_strike", SCHEDULED),  # Above $2.895
    ("KXBRENTD", "price_strike", SCHEDULED),  # Above $90.50
    ("KXNASDAQ100U", "price_strike", SCHEDULED),  # 30,190 or above
    ("KXINXU", "price_strike", SCHEDULED),  # 7,815 or above
    ("KXAAAGASD", "price_strike", SCHEDULED),  # Above 4.090 (AAA gas)
    ("KXH200WS", "price_strike", SCHEDULED),  # Above $5.50
    # --- economic releases
    ("KXCPICOMBO", "econ_release", SCHEDULED),  # Headline: Exactly 0.2%, Core: 0.3% or above
    ("KXECONSTATCPI", "econ_release", SCHEDULED),  # Exactly 0.3%
    ("KXCPINDEX", "econ_release", SCHEDULED),  # Above 334.3
    ("KXCPI", "econ_release", SCHEDULED),  # Above 0.2%
    ("KXUSGASCPI", "econ_release", SCHEDULED),  # Above 333
    ("KXARMOMINF", "econ_release", SCHEDULED),  # Above 2.4% (Argentina monthly inflation)
    ("KXUE", "econ_release", SCHEDULED),  # Above 4.6% (unemployment)
    # --- culture / mentions / announcements / politics / measured statistics
    ("KXALBUMEQUIV", "rank_culture", DISCRETE),  # Above 90K album-equivalent units
    ("KXTRUTHSOCIAL", "mention", DISCRETE),  # >240 posts
    ("KXGEMINI", "announcement", DISCRETE),  # Before Aug 21, 2026 (model release)
    ("KXGPT", "announcement", DISCRETE),  # Before Aug 21, 2026 (model release)
    ("KXAPRPOTUS", "politics", SCHEDULED),  # Below 38.8 (approval rating)
    ("KXRAIN", "event_stat", SCHEDULED),  # per-city rainfall
    ("KXHMONTH", "event_stat", SCHEDULED),  # Hottest ever
    # ===== 2026-08-24 SETTLEMENT-TAXONOMY REPAIR (Platform Change Review) ===============
    # 861 markets across 198 series prefixes -- 14.31% of the eligible non-crypto 5-7c
    # population -- classified as `unknown`, against a 5% bar. Every one of the 198 was
    # reviewed against Kalshi's OWN published title and rules text (1,561 documents,
    # 8 markets per prefix from settled+open). 194 are classified below; 4 stay unknown
    # and are listed in docs/mmsell_taxonomy_repair/REVIEW_20260824.md with their reason.
    #
    # The discriminator applied, stated before the case-by-case rather than after it:
    #   scheduled  settles to a figure PUBLISHED by a named external source; resolution
    #              cannot happen before that publication instant
    #   discrete   resolves on an OCCURRENCE inside a window, whenever it occurs; no
    #              publication instant is waited on
    #   in_play    resolves through a live contest, progressively revealed by play
    #
    # A bare clock time ('at 8:10 PM EDT') decided NOTHING: Kalshi writes it as a game
    # START time on in-play markets. `can_close_early` decided nothing either -- it is
    # set on 100% of these markets, index-close ones included. Expiration-to-close
    # distance and price-path shape corroborate only; neither can name a mode alone.
    # --- in_play ------------------------------------------------------------------------
    ("KXENGCSSCORE", "exact_score", IN_PLAY),  # final score, after 90 min + stoppage
    ("KXLALIGASCORE", "exact_score", IN_PLAY),  # final score, after 90 min + stoppage
    ("KXLEAGUESCUPSCORE", "exact_score", IN_PLAY),  # final score, after 90 min + stoppage
    ("KXLIGAMXSCORE", "exact_score", IN_PLAY),  # final score, after 90 min + stoppage
    ("KXMLSSCORE", "exact_score", IN_PLAY),  # final score, after 90 min + stoppage
    ("KXCLUBFBTTS", "game_prop", IN_PLAY),  # both teams to score in the game
    ("KXLEAGUESCUPBTTS", "game_prop", IN_PLAY),  # both teams to score in the game
    ("KXPGAHOLEINONE", "game_prop", IN_PLAY),  # count produced by tournament play
    ("KXUCLBTTS", "game_prop", IN_PLAY),  # both teams to score in the game
    ("KXAFLGAME", "h2h", IN_PLAY),  # game winner; tie resolves 50/50
    ("KXAPFDDHGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXARGNACBGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXASEANGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXBELGIANPLGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXBOLPDIVGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXBRASILEIROCGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXBUNDESLIGA2GAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXCHLLDPGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXCHNSLGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXCOPPAITALIAGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXCPLMATCH", "h2h", IN_PLAY),  # match winner; tie/draw handled in rules
    ("KXCZEFLGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXDENSUPERLIGAGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXDOTA2GAME", "h2h", IN_PLAY),  # full-match winner
    ("KXEFLCHAMPIONSHIPGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXEFLL1GAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXELITESERIENGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXENGCSGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXEPLGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXEREDIVISIEGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXHNLGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXITFDOUBLES", "h2h", IN_PLAY),  # match winner, after a ball has been played
    ("KXITFWDOUBLES", "h2h", IN_PLAY),  # match winner, after a ball has been played
    ("KXJ2LEAGUEGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXJLEAGUEGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXKBOGAME", "h2h", IN_PLAY),  # game winner; postponed -> reschedule
    ("KXKLEAGUEGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXLALIGA2GAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXLALIGAGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXLIGAEXPGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXLIGAPORTUGALGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXLNBPGAME", "h2h", IN_PLAY),  # game winner; official final result
    ("KXR6GAME", "h2h", IN_PLAY),  # full-match winner
    ("KXSAUDIPLGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXSUPERLIGGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXSVK2LGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXUAEPLGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXURYPDGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXUSLGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXVALORANTGAME", "h2h", IN_PLAY),  # full-match winner
    ("KXVENFUTVEGAME", "h2h", IN_PLAY),  # game winner, after 90 min + stoppage
    ("KXCS2MAP", "h2h_period", IN_PLAY),  # winner of one map inside the match
    ("KXDOTA2MAP", "h2h_period", IN_PLAY),  # winner of one map inside the match
    ("KXLEAGUESCUP1H", "h2h_period", IN_PLAY),  # first-half winner, after 45 min + stoppage
    ("KXLOLMAP", "h2h_period", IN_PLAY),  # winner of one map inside the match
    ("KXMLBF3", "h2h_period", IN_PLAY),  # first-3-innings winner
    ("KXMLS1H", "h2h_period", IN_PLAY),  # first-half winner, after 45 min + stoppage
    ("KXWNBA1HWINNER", "h2h_period", IN_PLAY),  # winner of the stated period
    ("KXWNBA2QWINNER", "h2h_period", IN_PLAY),  # winner of the stated period
    ("KXCHAMPTOUR", "outright", IN_PLAY),  # tournament winner
    ("KXCHESSTOURNAMENT", "outright", IN_PLAY),  # tournament winner over its playing dates
    ("KXCOD", "outright", IN_PLAY),  # tournament champion
    ("KXKFTOUR", "outright", IN_PLAY),  # tournament winner
    ("KXNASCARRACE", "outright", IN_PLAY),  # finishing position in the main race
    ("KXNASCARTOP10", "outright", IN_PLAY),  # finishing position in the main race
    ("KXNASCARTOP3", "outright", IN_PLAY),  # finishing position in the main race
    ("KXMLBRBI", "player_prop", IN_PLAY),  # named player's in-game stat line
    ("KXMLBSB", "player_prop", IN_PLAY),  # named player's in-game stat line
    ("KXMLBTB", "player_prop", IN_PLAY),  # named player's in-game stat line
    ("KXNFLPASSYDS", "player_prop", IN_PLAY),  # named player's in-game stat line
    ("KXNFLRSHYDS", "player_prop", IN_PLAY),  # named player's in-game stat line
    ("KXNFLTD", "player_prop", IN_PLAY),  # named player's in-game stat line
    ("KXALLSVENSKANSPREAD", "spread", IN_PLAY),  # margin in the game, after 90 min + stoppage
    ("KXAPFDDHSPREAD", "spread", IN_PLAY),  # margin in the game, after 90 min + stoppage
    ("KXARGPREMDIVSPREAD", "spread", IN_PLAY),  # margin in the game, after 90 min + stoppage
    ("KXASEANSPREAD", "spread", IN_PLAY),  # margin in the game, after 90 min + stoppage
    ("KXATPGSPREAD", "spread", IN_PLAY),  # game differential across the full match
    ("KXBELGIANPLSPREAD", "spread", IN_PLAY),  # margin in the game, after 90 min + stoppage
    ("KXCHNSLSPREAD", "spread", IN_PLAY),  # margin in the game, after 90 min + stoppage
    ("KXCLUBFSPREAD", "spread", IN_PLAY),  # margin in the game, after 90 min + stoppage
    ("KXCONMEBOLSUDSPREAD", "spread", IN_PLAY),  # margin in the game, after 90 min + stoppage
    ("KXECULPSPREAD", "spread", IN_PLAY),  # margin in the game, after 90 min + stoppage
    ("KXEFLCHAMPIONSHIPSPREAD", "spread", IN_PLAY),  # margin in the game, after 90 min + stoppage
    ("KXELITESERIENSPREAD", "spread", IN_PLAY),  # margin in the game, after 90 min + stoppage
    ("KXENGCSSPREAD", "spread", IN_PLAY),  # margin in the game, after 90 min + stoppage
    ("KXEREDIVISIESPREAD", "spread", IN_PLAY),  # margin in the game, after 90 min + stoppage
    ("KXKBOSPREAD", "spread", IN_PLAY),  # run margin in the game
    ("KXLALIGASPREAD", "spread", IN_PLAY),  # margin in the game, after 90 min + stoppage
    ("KXLIGAMXSPREAD", "spread", IN_PLAY),  # margin in the game, after 90 min + stoppage
    ("KXLIGAPORTUGALSPREAD", "spread", IN_PLAY),  # margin in the game, after 90 min + stoppage
    ("KXMLSSPREAD", "spread", IN_PLAY),  # margin in the game, after 90 min + stoppage
    ("KXNPBSPREAD", "spread", IN_PLAY),  # run margin in the game
    ("KXSUPERLIGSPREAD", "spread", IN_PLAY),  # margin in the game, after 90 min + stoppage
    ("KXURYPDSPREAD", "spread", IN_PLAY),  # margin in the game, after 90 min + stoppage
    ("KXWNBA1HSPREAD", "spread", IN_PLAY),  # margin in the stated period
    ("KXWNBA2QSPREAD", "spread", IN_PLAY),  # margin in the stated period
    ("KXALLSVENSKANTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXAPFDDHTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXARGPREMDIVTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXASEANTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXBELGIANPLTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXBRASILEIROBTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXBRASILEIROCTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXBRASILEIROTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXBUNDESLIGA2TOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXCHLLDPTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXCHNSLTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXCONMEBOLLIBTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXCONMEBOLSUDTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXCOPPAITALIATOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXCZEFNLTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXDENSUPERLIGATOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXDIMAYORTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXECULPTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXEFLCHAMPIONSHIPTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXEFLCUPTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXEFLL1TOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXEKSTRAKLASATOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXELITESERIENTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXENGCSTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXEREDIVISIETOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXFINYLTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXJLEAGUETOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXLALIGATOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXLEAGUESCUP1HTOTAL", "total", IN_PLAY),  # first-half goals
    ("KXLEAGUESCUPTEAMTOTAL", "total", IN_PLAY),  # one team's goals in the game
    ("KXLIGAEXPTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXLIGAMX1HTOTAL", "total", IN_PLAY),  # first-half goals
    ("KXLIGAMXTEAMTOTAL", "total", IN_PLAY),  # one team's goals in the game
    ("KXLIGAPORTUGALTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXNFL1HTOTAL", "total", IN_PLAY),  # first-half points in the game
    ("KXNPBTOTAL", "total", IN_PLAY),  # runs collectively scored in the game
    ("KXNWSLTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXPERLIGA1TOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXSAUDIPLTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXSCOTTISHPREMTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXSUPERLIGTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXUAEPLTOTAL", "total", IN_PLAY),  # goals in the game, after 90 min + stoppage
    ("KXUCL1HTOTAL", "total", IN_PLAY),  # first-half goals
    ("KXWNBA1HTOTAL", "total", IN_PLAY),  # points in the stated period
    ("KXWNBA1QTOTAL", "total", IN_PLAY),  # points in the stated period
    ("KXWNBA2QTOTAL", "total", IN_PLAY),  # points in the stated period
    ("KXWNBA4QTOTAL", "total", IN_PLAY),  # points in the stated period
    ("KXWNBATEAMTOTAL", "total", IN_PLAY),  # one team's points in the game
    # --- scheduled ------------------------------------------------------------------------
    ("KXBKNUGGETS", "econ_release", SCHEDULED),  # figure published by a NAMED source on a stated release
    ("KXECONSTATCORECPIYOY", "econ_release", SCHEDULED),  # the economic print itself
    ("KXDEEPSHARE", "event_stat", SCHEDULED),  # figure published by a NAMED source on a stated release
    ("KXGOOGSHARE", "event_stat", SCHEDULED),  # figure published by a NAMED source on a stated release
    ("KXHORMUZWEEKLY", "event_stat", SCHEDULED),  # figure published by a NAMED source on a stated release
    ("KXTRUMPACT", "event_stat", SCHEDULED),  # value published by a NAMED source, read at a stated instant
    ("KXA100WS", "price_strike", SCHEDULED),  # value published by a NAMED source, read at a stated instant
    ("KXB200WS", "price_strike", SCHEDULED),  # value published by a NAMED source, read at a stated instant
    ("KXBRENTMON", "price_strike", SCHEDULED),  # close price of a 1-minute candlestick at a stated instant
    ("KXCOPPERD", "price_strike", SCHEDULED),  # close price of a 1-minute candlestick at a stated instant
    ("KXEURUSD", "price_strike", SCHEDULED),  # open price of the pair at a stated instant
    ("KXEURUSDAW", "price_strike", SCHEDULED),  # value published by a NAMED source, read at a stated instant
    ("KXGOLDMON", "price_strike", SCHEDULED),  # close price of a 1-minute candlestick at a stated instant
    ("KXGOLDW", "price_strike", SCHEDULED),  # close price of a 1-minute candlestick at a stated instant
    ("KXH100WS", "price_strike", SCHEDULED),  # value published by a NAMED source, read at a stated instant
    ("KXH200MS", "price_strike", SCHEDULED),  # figure published by a NAMED source on a stated release
    ("KXINX", "price_strike", SCHEDULED),  # end-of-day index value on a stated date
    ("KXNASDAQ100", "price_strike", SCHEDULED),  # end-of-day index value on a stated date
    ("KXSILVERD", "price_strike", SCHEDULED),  # close price of a 1-minute candlestick at a stated instant
    ("KXUSDJPY", "price_strike", SCHEDULED),  # open price of the pair at a stated instant
    ("KXBILLBOARDRUNNERUPSONG", "rank_culture", SCHEDULED),  # position on a chart PUBLISHED on a stated date
    ("KXCHINAAI", "rank_culture", SCHEDULED),  # value published by a NAMED source, read at a stated instant
    ("KXNETFLIXRANKMOVIE", "rank_culture", SCHEDULED),  # position on a chart PUBLISHED on a stated date
    ("KXNETFLIXRANKMOVIERUNNERUP", "rank_culture", SCHEDULED),  # position on a chart PUBLISHED on a stated date
    ("KXNETFLIXTOPVIEWSMOVIE", "rank_culture", SCHEDULED),  # position on a chart PUBLISHED on a stated date
    ("KXNETFLIXTOPVIEWSTV", "rank_culture", SCHEDULED),  # position on a chart PUBLISHED on a stated date
    ("KXPUREALBUMS", "rank_culture", SCHEDULED),  # figure published by a NAMED source on a stated release
    ("KXYTDAILYTOPVIDEOG", "rank_culture", SCHEDULED),  # position on a chart PUBLISHED on a stated date
    ("KXYTTOPSONGW", "rank_culture", SCHEDULED),  # position on a chart PUBLISHED on a stated date
    ("KXYTTOPVIDEO2D", "rank_culture", SCHEDULED),  # position on a chart PUBLISHED on a stated date
    ("KXYTTOPVIDEOG2D", "rank_culture", SCHEDULED),  # position on a chart PUBLISHED on a stated date
    # --- discrete ------------------------------------------------------------------------
    ("KXFDAANNOUNCE", "announcement", DISCRETE),  # the departure/announcement, whenever it is made
    ("KXGROK", "announcement", DISCRETE),  # public release, whenever it happens in the window
    ("KXPRESSSECANNOUNCE", "announcement", DISCRETE),  # the departure/announcement, whenever it is made
    ("KXEARTHQUAKEM", "event_stat", DISCRETE),  # the event occurs, or does not, inside a window
    ("KXSPACEXSTARSHIP", "event_stat", DISCRETE),  # the event occurs, or does not, inside a window
    ("KXYTVIEWSHIGH", "event_stat", DISCRETE),  # the event occurs, or does not, inside a window
    ("KXYTVIEWSW", "event_stat", DISCRETE),  # the event occurs, or does not, inside a window
    ("KXBIGBROTHERELIMINATION", "outright", DISCRETE),  # elimination declared via official broadcast/press
    ("KXCLARITYVOTE", "politics", DISCRETE),  # the event occurs, or does not, inside a window
    ("KXDIAZOUT", "politics", DISCRETE),  # the departure/announcement, whenever it is made
    ("KXDROPOUTPRIMARY", "politics", DISCRETE),  # the departure/announcement, whenever it is made
    ("KXHEGSETHANNOUNCEOUT", "politics", DISCRETE),  # the departure/announcement, whenever it is made
    ("KXKASHANNOUNCEOUT", "politics", DISCRETE),  # the departure/announcement, whenever it is made
    ("KXMEXCUBOIL", "politics", DISCRETE),  # the event occurs, or does not, inside a window
    ("KXPIRROOUT", "politics", DISCRETE),  # the departure/announcement, whenever it is made
    ("KXTRUMPMEET", "politics", DISCRETE),  # the event occurs, or does not, inside a window
    ("KXTRUMPUFC", "politics", DISCRETE),  # the event occurs, or does not, inside a window
    ("KXUAPFILES", "politics", DISCRETE),  # the event occurs, or does not, inside a window
    ("KXBNBMINMON", "price_strike", DISCRETE),  # resolves the instant a minute-by-minute barrier is EVER touched
)

# Every type/mode name a book may legally name. A variant spec referencing anything outside
# these is a typo that would silently trade nothing (an allowlist of a non-existent type admits
# no market at all), so config validation rejects the whole spec rather than running a dead book.
KNOWN_TYPES = frozenset(t for _, t, _ in SERIES_TYPES)
KNOWN_MODES = frozenset(m for _, _, m in SERIES_TYPES)


def classify(series: str) -> tuple[str, str]:
    """(market_type, settle_mode) for a series ticker; UNCLASSIFIED when the table has no entry.

    Longest-prefix match so a more specific series always beats a shorter one that happens to
    prefix it (KXMLBHRDERBYMATCHUP over KXMLBHRDERBY, KXWC1HTOTAL over KXWC1H)."""
    s = (series or "").upper()
    best: tuple[str, str, str] | None = None
    for prefix, mtype, mode in SERIES_TYPES:
        if s.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, mtype, mode)
    return (best[1], best[2]) if best else UNCLASSIFIED
