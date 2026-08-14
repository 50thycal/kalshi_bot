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
