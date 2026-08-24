"""The 2026-08-24 settlement-taxonomy repair, as a set of falsifiable claims.

`docs/mmsell_taxonomy_repair/` carries the census, the frozen review manifest and the
evidence. This file carries the parts a reviewer should not have to take on trust: that the
two copies of `SERIES_TYPES` really are identical, that every prefix the review accepted maps
to the mode the review recorded, that longest-prefix matching still protects the specific
entries the new short prefixes now sit under, and — most of all — that the three traps the
audit fell into on its first pass cannot decide a classification here.

The traps are tested as PROPERTIES of the shipped table rather than of the audit script,
because the table is what the books read. A bare clock time and `can_close_early` are not
inputs to `classify()` at all; the test that matters is that no series whose ONLY
scheduled-looking feature is one of those two ended up `scheduled`.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re

from kalshi_bot.config import Settings
from kalshi_bot.mmsell.market_types import (
    DISCRETE,
    IN_PLAY,
    KNOWN_MODES,
    KNOWN_TYPES,
    SCHEDULED,
    SERIES_TYPES,
    UNCLASSIFIED,
    classify,
)

REPO = pathlib.Path(__file__).resolve().parent.parent
WORKER_PATH = REPO / "kalshi_bot" / "mmsell" / "market_types.py"
OPS_PATH = REPO / "scripts" / "mmsell_market_types.py"

#: The block header both copies must carry, byte for byte.
BLOCK_MARK = "# ===== 2026-08-24 SETTLEMENT-TAXONOMY REPAIR (Platform Change Review) ====="


def _ops_module():
    spec = importlib.util.spec_from_file_location("mmsell_market_types_ops", OPS_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _repair_block(path: pathlib.Path) -> str:
    """The added region of one copy, verbatim, from its header to the tuple's closing paren."""
    src = path.read_text()
    i = src.index(BLOCK_MARK)
    return src[i:src.index("\n)\n", i)]


# --------------------------------------------------------------- the two copies are one table


def test_the_two_series_types_copies_are_byte_identical():
    """Not merely 'equal as data' — byte-identical in the source, so a reviewer diffing the two
    files sees nothing and a future editor cannot 'fix' one copy's formatting into a silent
    semantic difference. `test_mmsell_type_books` already asserts tuple equality; this asserts
    the stronger property the module docstrings actually promise."""
    assert _repair_block(WORKER_PATH) == _repair_block(OPS_PATH)


def test_both_copies_agree_entry_for_entry_after_the_repair():
    ops = _ops_module()
    assert SERIES_TYPES == ops.SERIES_TYPES
    for series, _t, _m in SERIES_TYPES:
        assert classify(series) == ops.classify(series)


def test_no_duplicate_prefix_in_the_table():
    """Two rows for one prefix is a silent coin-flip: longest-prefix match keeps whichever it
    reaches first among equals, so a duplicate with a different mode is undefined behaviour."""
    prefixes = [p for p, _t, _m in SERIES_TYPES]
    assert len(prefixes) == len(set(prefixes)), sorted(
        p for p in prefixes if prefixes.count(p) > 1)


# ------------------------------------------------------------------- the reviewed classifications

#: Every prefix the 2026-08-24 review ACCEPTED, with the mode it accepted.
#: 194 rows; the review's own record is docs/mmsell_taxonomy_repair/REVIEW_20260824.md.
ACCEPTED: dict[str, tuple[str, str]] = {
    # --- scheduled: settles to a figure PUBLISHED by a named external source ---------------
    "KXA100WS": ("price_strike", SCHEDULED),
    "KXB200WS": ("price_strike", SCHEDULED),
    "KXBILLBOARDRUNNERUPSONG": ("rank_culture", SCHEDULED),
    "KXBKNUGGETS": ("econ_release", SCHEDULED),
    "KXBRENTMON": ("price_strike", SCHEDULED),
    "KXCHINAAI": ("rank_culture", SCHEDULED),
    "KXCOPPERD": ("price_strike", SCHEDULED),
    "KXDEEPSHARE": ("event_stat", SCHEDULED),
    "KXECONSTATCORECPIYOY": ("econ_release", SCHEDULED),
    "KXEURUSD": ("price_strike", SCHEDULED),
    "KXEURUSDAW": ("price_strike", SCHEDULED),
    "KXGOLDMON": ("price_strike", SCHEDULED),
    "KXGOLDW": ("price_strike", SCHEDULED),
    "KXGOOGSHARE": ("event_stat", SCHEDULED),
    "KXH100WS": ("price_strike", SCHEDULED),
    "KXH200MS": ("price_strike", SCHEDULED),
    "KXHORMUZWEEKLY": ("event_stat", SCHEDULED),
    "KXINX": ("price_strike", SCHEDULED),
    "KXNASDAQ100": ("price_strike", SCHEDULED),
    "KXNETFLIXRANKMOVIE": ("rank_culture", SCHEDULED),
    "KXNETFLIXRANKMOVIERUNNERUP": ("rank_culture", SCHEDULED),
    "KXNETFLIXTOPVIEWSMOVIE": ("rank_culture", SCHEDULED),
    "KXNETFLIXTOPVIEWSTV": ("rank_culture", SCHEDULED),
    "KXPUREALBUMS": ("rank_culture", SCHEDULED),
    "KXSILVERD": ("price_strike", SCHEDULED),
    "KXTRUMPACT": ("event_stat", SCHEDULED),
    "KXUSDJPY": ("price_strike", SCHEDULED),
    "KXYTDAILYTOPVIDEOG": ("rank_culture", SCHEDULED),
    "KXYTTOPSONGW": ("rank_culture", SCHEDULED),
    "KXYTTOPVIDEO2D": ("rank_culture", SCHEDULED),
    "KXYTTOPVIDEOG2D": ("rank_culture", SCHEDULED),
    # --- discrete: resolves on an OCCURRENCE inside a window ------------------------------
    "KXBIGBROTHERELIMINATION": ("outright", DISCRETE),
    "KXBNBMINMON": ("price_strike", DISCRETE),
    "KXCLARITYVOTE": ("politics", DISCRETE),
    "KXDIAZOUT": ("politics", DISCRETE),
    "KXDROPOUTPRIMARY": ("politics", DISCRETE),
    "KXEARTHQUAKEM": ("event_stat", DISCRETE),
    "KXFDAANNOUNCE": ("announcement", DISCRETE),
    "KXGROK": ("announcement", DISCRETE),
    "KXHEGSETHANNOUNCEOUT": ("politics", DISCRETE),
    "KXKASHANNOUNCEOUT": ("politics", DISCRETE),
    "KXMEXCUBOIL": ("politics", DISCRETE),
    "KXPIRROOUT": ("politics", DISCRETE),
    "KXPRESSSECANNOUNCE": ("announcement", DISCRETE),
    "KXSPACEXSTARSHIP": ("event_stat", DISCRETE),
    "KXTRUMPMEET": ("politics", DISCRETE),
    "KXTRUMPUFC": ("politics", DISCRETE),
    "KXUAPFILES": ("politics", DISCRETE),
    "KXYTVIEWSHIGH": ("event_stat", DISCRETE),
    "KXYTVIEWSW": ("event_stat", DISCRETE),
    # --- in_play: resolves through a live contest -----------------------------------------
    "KXAFLGAME": ("h2h", IN_PLAY),
    "KXALLSVENSKANSPREAD": ("spread", IN_PLAY),
    "KXALLSVENSKANTOTAL": ("total", IN_PLAY),
    "KXAPFDDHGAME": ("h2h", IN_PLAY),
    "KXAPFDDHSPREAD": ("spread", IN_PLAY),
    "KXAPFDDHTOTAL": ("total", IN_PLAY),
    "KXARGNACBGAME": ("h2h", IN_PLAY),
    "KXARGPREMDIVSPREAD": ("spread", IN_PLAY),
    "KXARGPREMDIVTOTAL": ("total", IN_PLAY),
    "KXASEANGAME": ("h2h", IN_PLAY),
    "KXASEANSPREAD": ("spread", IN_PLAY),
    "KXASEANTOTAL": ("total", IN_PLAY),
    "KXATPGSPREAD": ("spread", IN_PLAY),
    "KXBELGIANPLGAME": ("h2h", IN_PLAY),
    "KXBELGIANPLSPREAD": ("spread", IN_PLAY),
    "KXBELGIANPLTOTAL": ("total", IN_PLAY),
    "KXBOLPDIVGAME": ("h2h", IN_PLAY),
    "KXBRASILEIROBTOTAL": ("total", IN_PLAY),
    "KXBRASILEIROCGAME": ("h2h", IN_PLAY),
    "KXBRASILEIROCTOTAL": ("total", IN_PLAY),
    "KXBRASILEIROTOTAL": ("total", IN_PLAY),
    "KXBUNDESLIGA2GAME": ("h2h", IN_PLAY),
    "KXBUNDESLIGA2TOTAL": ("total", IN_PLAY),
    "KXCHAMPTOUR": ("outright", IN_PLAY),
    "KXCHESSTOURNAMENT": ("outright", IN_PLAY),
    "KXCHLLDPGAME": ("h2h", IN_PLAY),
    "KXCHLLDPTOTAL": ("total", IN_PLAY),
    "KXCHNSLGAME": ("h2h", IN_PLAY),
    "KXCHNSLSPREAD": ("spread", IN_PLAY),
    "KXCHNSLTOTAL": ("total", IN_PLAY),
    "KXCLUBFBTTS": ("game_prop", IN_PLAY),
    "KXCLUBFSPREAD": ("spread", IN_PLAY),
    "KXCOD": ("outright", IN_PLAY),
    "KXCONMEBOLLIBTOTAL": ("total", IN_PLAY),
    "KXCONMEBOLSUDSPREAD": ("spread", IN_PLAY),
    "KXCONMEBOLSUDTOTAL": ("total", IN_PLAY),
    "KXCOPPAITALIAGAME": ("h2h", IN_PLAY),
    "KXCOPPAITALIATOTAL": ("total", IN_PLAY),
    "KXCPLMATCH": ("h2h", IN_PLAY),
    "KXCS2MAP": ("h2h_period", IN_PLAY),
    "KXCZEFLGAME": ("h2h", IN_PLAY),
    "KXCZEFNLTOTAL": ("total", IN_PLAY),
    "KXDENSUPERLIGAGAME": ("h2h", IN_PLAY),
    "KXDENSUPERLIGATOTAL": ("total", IN_PLAY),
    "KXDIMAYORTOTAL": ("total", IN_PLAY),
    "KXDOTA2GAME": ("h2h", IN_PLAY),
    "KXDOTA2MAP": ("h2h_period", IN_PLAY),
    "KXECULPSPREAD": ("spread", IN_PLAY),
    "KXECULPTOTAL": ("total", IN_PLAY),
    "KXEFLCHAMPIONSHIPGAME": ("h2h", IN_PLAY),
    "KXEFLCHAMPIONSHIPSPREAD": ("spread", IN_PLAY),
    "KXEFLCHAMPIONSHIPTOTAL": ("total", IN_PLAY),
    "KXEFLCUPTOTAL": ("total", IN_PLAY),
    "KXEFLL1GAME": ("h2h", IN_PLAY),
    "KXEFLL1TOTAL": ("total", IN_PLAY),
    "KXEKSTRAKLASATOTAL": ("total", IN_PLAY),
    "KXELITESERIENGAME": ("h2h", IN_PLAY),
    "KXELITESERIENSPREAD": ("spread", IN_PLAY),
    "KXELITESERIENTOTAL": ("total", IN_PLAY),
    "KXENGCSGAME": ("h2h", IN_PLAY),
    "KXENGCSSCORE": ("exact_score", IN_PLAY),
    "KXENGCSSPREAD": ("spread", IN_PLAY),
    "KXENGCSTOTAL": ("total", IN_PLAY),
    "KXEPLGAME": ("h2h", IN_PLAY),
    "KXEREDIVISIEGAME": ("h2h", IN_PLAY),
    "KXEREDIVISIESPREAD": ("spread", IN_PLAY),
    "KXEREDIVISIETOTAL": ("total", IN_PLAY),
    "KXFINYLTOTAL": ("total", IN_PLAY),
    "KXHNLGAME": ("h2h", IN_PLAY),
    "KXITFDOUBLES": ("h2h", IN_PLAY),
    "KXITFWDOUBLES": ("h2h", IN_PLAY),
    "KXJ2LEAGUEGAME": ("h2h", IN_PLAY),
    "KXJLEAGUEGAME": ("h2h", IN_PLAY),
    "KXJLEAGUETOTAL": ("total", IN_PLAY),
    "KXKBOGAME": ("h2h", IN_PLAY),
    "KXKBOSPREAD": ("spread", IN_PLAY),
    "KXKFTOUR": ("outright", IN_PLAY),
    "KXKLEAGUEGAME": ("h2h", IN_PLAY),
    "KXLALIGA2GAME": ("h2h", IN_PLAY),
    "KXLALIGAGAME": ("h2h", IN_PLAY),
    "KXLALIGASCORE": ("exact_score", IN_PLAY),
    "KXLALIGASPREAD": ("spread", IN_PLAY),
    "KXLALIGATOTAL": ("total", IN_PLAY),
    "KXLEAGUESCUP1H": ("h2h_period", IN_PLAY),
    "KXLEAGUESCUP1HTOTAL": ("total", IN_PLAY),
    "KXLEAGUESCUPBTTS": ("game_prop", IN_PLAY),
    "KXLEAGUESCUPSCORE": ("exact_score", IN_PLAY),
    "KXLEAGUESCUPTEAMTOTAL": ("total", IN_PLAY),
    "KXLIGAEXPGAME": ("h2h", IN_PLAY),
    "KXLIGAEXPTOTAL": ("total", IN_PLAY),
    "KXLIGAMX1HTOTAL": ("total", IN_PLAY),
    "KXLIGAMXSCORE": ("exact_score", IN_PLAY),
    "KXLIGAMXSPREAD": ("spread", IN_PLAY),
    "KXLIGAMXTEAMTOTAL": ("total", IN_PLAY),
    "KXLIGAPORTUGALGAME": ("h2h", IN_PLAY),
    "KXLIGAPORTUGALSPREAD": ("spread", IN_PLAY),
    "KXLIGAPORTUGALTOTAL": ("total", IN_PLAY),
    "KXLNBPGAME": ("h2h", IN_PLAY),
    "KXLOLMAP": ("h2h_period", IN_PLAY),
    "KXMLBF3": ("h2h_period", IN_PLAY),
    "KXMLBRBI": ("player_prop", IN_PLAY),
    "KXMLBSB": ("player_prop", IN_PLAY),
    "KXMLBTB": ("player_prop", IN_PLAY),
    "KXMLS1H": ("h2h_period", IN_PLAY),
    "KXMLSSCORE": ("exact_score", IN_PLAY),
    "KXMLSSPREAD": ("spread", IN_PLAY),
    "KXNASCARRACE": ("outright", IN_PLAY),
    "KXNASCARTOP10": ("outright", IN_PLAY),
    "KXNASCARTOP3": ("outright", IN_PLAY),
    "KXNFL1HTOTAL": ("total", IN_PLAY),
    "KXNFLPASSYDS": ("player_prop", IN_PLAY),
    "KXNFLRSHYDS": ("player_prop", IN_PLAY),
    "KXNFLTD": ("player_prop", IN_PLAY),
    "KXNPBSPREAD": ("spread", IN_PLAY),
    "KXNPBTOTAL": ("total", IN_PLAY),
    "KXNWSLTOTAL": ("total", IN_PLAY),
    "KXPERLIGA1TOTAL": ("total", IN_PLAY),
    "KXPGAHOLEINONE": ("game_prop", IN_PLAY),
    "KXR6GAME": ("h2h", IN_PLAY),
    "KXSAUDIPLGAME": ("h2h", IN_PLAY),
    "KXSAUDIPLTOTAL": ("total", IN_PLAY),
    "KXSCOTTISHPREMTOTAL": ("total", IN_PLAY),
    "KXSUPERLIGGAME": ("h2h", IN_PLAY),
    "KXSUPERLIGSPREAD": ("spread", IN_PLAY),
    "KXSUPERLIGTOTAL": ("total", IN_PLAY),
    "KXSVK2LGAME": ("h2h", IN_PLAY),
    "KXUAEPLGAME": ("h2h", IN_PLAY),
    "KXUAEPLTOTAL": ("total", IN_PLAY),
    "KXUCL1HTOTAL": ("total", IN_PLAY),
    "KXUCLBTTS": ("game_prop", IN_PLAY),
    "KXURYPDGAME": ("h2h", IN_PLAY),
    "KXURYPDSPREAD": ("spread", IN_PLAY),
    "KXUSLGAME": ("h2h", IN_PLAY),
    "KXVALORANTGAME": ("h2h", IN_PLAY),
    "KXVENFUTVEGAME": ("h2h", IN_PLAY),
    "KXWNBA1HSPREAD": ("spread", IN_PLAY),
    "KXWNBA1HTOTAL": ("total", IN_PLAY),
    "KXWNBA1HWINNER": ("h2h_period", IN_PLAY),
    "KXWNBA1QTOTAL": ("total", IN_PLAY),
    "KXWNBA2QSPREAD": ("spread", IN_PLAY),
    "KXWNBA2QTOTAL": ("total", IN_PLAY),
    "KXWNBA2QWINNER": ("h2h_period", IN_PLAY),
    "KXWNBA4QTOTAL": ("total", IN_PLAY),
    "KXWNBATEAMTOTAL": ("total", IN_PLAY),
}

#: Reviewed and deliberately NOT classified. Each stays `unknown`, so it is admitted by no
#: `mtype=`/`mode=` book and enters NEITHER arm of the MMSELL 2x2. The reasons are recorded in
#: docs/mmsell_taxonomy_repair/REVIEW_20260824.md; the test is that they did not creep in.
DEFERRED = {
    "KXTRUEV": "rules name the index and the date but no publisher and no publication instant",
    "KXDIESELD": "rules say only 'the Diesel Price on <date>' -- no source, no instant",
    "KXDIESELW": "same bare text as KXDIESELD",
    "KXMC": "evidence is clear, but a 4-character prefix mapping to `scheduled` would sweep "
            "any future KXMC* series into the treatment-eligible mode",
}


def test_every_accepted_prefix_maps_to_the_reviewed_type_and_mode():
    assert len(ACCEPTED) == 194
    for prefix, expected in ACCEPTED.items():
        assert classify(prefix) == expected, prefix
        # and for a real ticker under it, not just the bare prefix
        assert classify(f"{prefix}-26AUG24-T") == expected, prefix


def test_deferred_prefixes_stay_unknown():
    """The whole point of the frozen review is that a prefix the evidence did not decide is
    still excluded. An `unknown` series is admitted by no allowlist filter, so these enter
    neither the treatment nor the control arm — which is the correct, conservative outcome."""
    for prefix in DEFERRED:
        assert classify(prefix) == UNCLASSIFIED, prefix
        assert classify(f"{prefix}-26AUG24-T") == UNCLASSIFIED, prefix


def test_the_review_covered_every_prefix_in_the_census_and_nothing_else():
    """194 accepted + 4 deferred = the 198 unclassified prefixes the canonical census found.
    A number that drifts here means the manifest and the table have parted company."""
    assert len(ACCEPTED) + len(DEFERRED) == 198
    assert not (set(ACCEPTED) & set(DEFERRED))


def test_the_repair_added_no_new_type_or_mode_NAME():
    """`KNOWN_TYPES`/`KNOWN_MODES` gate every book spec through config validation. Widening
    them would be a second, separate platform change riding along with this one: a `mtype=`
    name that is a typo today would silently become legal."""
    assert KNOWN_MODES == {IN_PLAY, SCHEDULED, DISCRETE}
    assert KNOWN_TYPES == {
        "h2h", "h2h_period", "spread", "total", "exact_score", "player_prop", "game_prop",
        "outright", "mention", "price_strike", "econ_release", "rank_culture", "event_stat",
        "politics", "announcement",
    }
    for _p, mtype, mode in SERIES_TYPES:
        assert mtype in KNOWN_TYPES and mode in KNOWN_MODES


# ------------------------------------------------------------------------- prefix shadowing


def test_specific_prefixes_are_not_shadowed_by_the_broader_ones_the_repair_adds():
    """Three of the new prefixes sit UNDER an entry that already existed, and three sit under
    another new one. Longest-prefix match must keep the specific entry winning for its own
    tickers — otherwise adding `KXINX` would silently reclassify every `KXINXU` market."""
    pairs = [
        # (broad prefix added by this repair, specific entry that must still win)
        ("KXINX", "KXINXU"),
        ("KXNASDAQ100", "KXNASDAQ100U"),
        ("KXCOD", "KXCODGAME"),
        ("KXEURUSD", "KXEURUSDAW"),
        ("KXLEAGUESCUP1H", "KXLEAGUESCUP1HTOTAL"),
        ("KXNETFLIXRANKMOVIE", "KXNETFLIXRANKMOVIERUNNERUP"),
    ]
    table = {p: (t, m) for p, t, m in SERIES_TYPES}
    for broad, specific in pairs:
        assert specific.startswith(broad) and specific != broad
        assert broad in table and specific in table
        assert classify(f"{specific}-26AUG24-T") == table[specific], specific
        assert classify(f"{broad}-26AUG24-T") == table[broad], broad
    # KXCODGAME is the sharpest case: a DIFFERENT type from the broader KXCOD.
    assert classify("KXCODGAME-26AUG24-X") == ("h2h", IN_PLAY)
    assert classify("KXCOD-26AUG24-X") == ("outright", IN_PLAY)


def test_no_pre_existing_prefix_changed_its_classification():
    """The repair is additive. Every prefix that classified as X before must still classify as
    X — otherwise this is not a taxonomy EXPANSION but a redefinition, and the impact review
    (docs/mmsell_taxonomy_repair/PLATFORM_IMPACT_20260824.md) would be describing the wrong
    change."""
    pre_existing = {
        "KXMLBGAME": ("h2h", IN_PLAY), "KXINXU": ("price_strike", SCHEDULED),
        "KXNASDAQ100U": ("price_strike", SCHEDULED), "KXCODGAME": ("h2h", IN_PLAY),
        "KXBTCD": ("price_strike", SCHEDULED), "KXFED": ("econ_release", SCHEDULED),
        "KXTRUMPSAY": ("mention", DISCRETE), "KXWCAWARD": ("outright", DISCRETE),
        "KXITFMATCH": ("h2h", IN_PLAY), "KXITFWMATCH": ("h2h", IN_PLAY),
        "KXWNBATOTAL": ("total", IN_PLAY), "KXLIGAMXTOTAL": ("total", IN_PLAY),
        "KXECONSTATCPIYOY": ("econ_release", SCHEDULED), "KXKASHOUT": ("politics", DISCRETE),
        "KXH200WS": ("price_strike", SCHEDULED), "KXSPACEXCOUNT": ("event_stat", SCHEDULED),
        "KXMLBF5": ("h2h_period", IN_PLAY), "KXMLBF5TOTAL": ("total", IN_PLAY),
        "KXNETFLIXRANKSHOW": ("rank_culture", SCHEDULED),
    }
    for prefix, expected in pre_existing.items():
        assert classify(f"{prefix}-26AUG24-T") == expected, prefix


# ---------------------------------------------------------------------------- the known traps


#: Kalshi's own rules text, verbatim, from the 2026-08-24 evidence run. One per trap.
#: These are the sentences that must NOT be read the way the audit's first pass read them.
CLOCK_TIME_IN_PLAY_TEXTS = {
    "KXMLBTB": "If Weston Wilson records 5+ total bases in the Chicago C vs Seattle "
               "professional baseball game originally scheduled for Aug 23, 2026 at 4:10 PM "
               "EDT, then the market resolves to Yes.",
    "KXKBOGAME": "If LG Twins wins the LG Twins vs Hanwha Eagles Korea KBO game originally "
                 "scheduled for Aug 23, 2026 at 6:00 AM EDT, then the market resolves to Yes.",
    "KXVALORANTGAME": "If Fire Flux Esports wins the VCT EMEA 2026: Fire Flux Esports vs. BBL "
                      "Esports Valorant match originally scheduled for Aug 23, 2026 at 1:00 "
                      "PM EDT, then the market resolves to Yes.",
    "KXLNBPGAME": "If Soles de Mexicali wins the Mineros de Zacatecas vs Soles de Mexicali "
                  "men's professional Mexico LNBP basketball game originally scheduled for "
                  "Aug 21, 2026 at 11:00 PM EDT, then the market resolves to Yes.",
}

GENUINELY_SCHEDULED_TEXTS = {
    "KXINX": "If the end-of-day S&P 500 index value on August 20, 2026 is above 8024.9999, "
             "then the market resolves to Yes.",
    "KXCOPPERD": "If the close price of the 1-minute candlestick for copper using the CCU6 "
                 "contract on August 20, 2026 at 5:00 PM EDT is above 6.58 USD/Lbs, then the "
                 "market resolves to Yes.",
    "KXEURUSDAW": "The market resolves based on the open value of the EURUSD exchange rate as "
                  "published by Pyth at 5pm ET on Aug 21, 2026.",
    "KXNETFLIXTOPVIEWSTV": "If the #1 Show on Netflix has at least 9 million views on the "
                           "chart published on Aug 18, 2026, then the market resolves to Yes. "
                           "The Netflix charts are updated on Tuesday.",
}

GENUINELY_DISCRETE_TEXTS = {
    "KXGROK": "If SpaceXAI releases Grok 4.6 before Aug 15, 2026, then the market resolves to "
              "Yes. Release must be to the public, outside of a closed beta.",
    "KXEARTHQUAKEM": "If an earthquake with a magnitude at least 7.7 on the Moment Magnitude "
                     "Scale occurs with its epicenter in worldwide before Sep 1, 2026, then "
                     "the market resolves to Yes.",
    "KXBNBMINMON": "If the price of BNB after issuance and through 11:59 PM ET on Jul 31, "
                   "2026 is ever below $550.00, then the market resolves to Yes.",
    "KXYTVIEWSW": "If YoungBoy Never Broke Again has above 9M Global daily views on YouTube "
                  "at any point during August 10, 2026 - August 16, 2026, then the market "
                  "resolves to Yes.",
}

_BARE_CLOCK = re.compile(r"\bat \d{1,2}:\d{2} ?(AM|PM) ?(EDT|EST|ET)\b", re.I)


def test_a_bare_clock_time_did_not_imply_scheduled_settlement():
    """The trap that put MLB player props and KBO baseball into `scheduled` on the audit's
    first pass. Every text below CONTAINS a clock time and is nonetheless in-play — Kalshi
    writes the clock as the GAME START, not as a settlement instant. The assertion is on the
    shipped table: these prefixes must be `in_play`, clock time notwithstanding."""
    for prefix, text in CLOCK_TIME_IN_PLAY_TEXTS.items():
        assert _BARE_CLOCK.search(text), f"{prefix}: fixture no longer contains a clock time"
        assert classify(f"{prefix}-26AUG24-T")[1] == IN_PLAY, prefix
    # ...and the genuinely scheduled ones do NOT rest on a clock time: each names the published
    # figure it settles to.
    for prefix, text in GENUINELY_SCHEDULED_TEXTS.items():
        assert classify(f"{prefix}-26AUG24-T")[1] == SCHEDULED, prefix
        assert re.search(
            r"end-of-day|close price|open value|chart published", text, re.I), prefix


def test_representative_tricky_documents_land_in_the_right_mode():
    """One text per mode, chosen because a lazier rule would put it somewhere else:
    an index close that a clock-time rule would also match; an FX rate whose settlement source
    is a third party; a chart whose publication date is what it waits for; a barrier that
    resolves the instant it is touched; a threshold reached 'at any point during' a window."""
    for prefix in GENUINELY_SCHEDULED_TEXTS:
        assert classify(f"{prefix}-26AUG24-T")[1] == SCHEDULED, prefix
    for prefix in GENUINELY_DISCRETE_TEXTS:
        assert classify(f"{prefix}-26AUG24-T")[1] == DISCRETE, prefix
    for prefix in CLOCK_TIME_IN_PLAY_TEXTS:
        assert classify(f"{prefix}-26AUG24-T")[1] == IN_PLAY, prefix


def test_can_close_early_has_no_vote_because_it_is_not_an_input_at_all():
    """`can_close_early` is set on 100% of the unclassified population, index-close markets
    included; the audit's first pass let it propose `in_play` for KXINX and KXNASDAQ100.
    The structural guarantee is stronger than 'we weighted it low': `classify()` takes a series
    string and nothing else, so no market-level flag can reach it."""
    import inspect
    sig = inspect.signature(classify)
    assert list(sig.parameters) == ["series"]
    src = inspect.getsource(classify)
    assert "can_close_early" not in src
    # The two prefixes the flag mis-proposed are `scheduled` in the shipped table.
    assert classify("KXINX-26AUG20-B8025")[1] == SCHEDULED
    assert classify("KXNASDAQ100-26AUG20-B30800")[1] == SCHEDULED


#: Prefixes shorter than five characters that were already in the table before this repair.
#: They carry the property this repair refuses to add to: `KXRT`/`KXUE` would sweep any future
#: series starting with those four characters into `scheduled`. Recorded rather than changed —
#: re-deciding a grandfathered entry is a separate review with its own impact analysis
#: (docs/mmsell_taxonomy_repair/PLATFORM_IMPACT_20260824.md §6).
PRE_EXISTING_SHORT_PREFIXES = {"KXRT", "KXUE"}


def test_the_repair_added_no_catch_all_default():
    """A prefix short enough to swallow unrelated series would be a default by another name.
    `KXMC` is the case this rule cost us: its settlement evidence is unambiguous, and it stays
    unknown anyway because a four-character prefix mapping to `scheduled` would admit any
    future `KXMC*` series to the treatment arm sight unseen."""
    short = {p for p, _t, _m in SERIES_TYPES if len(p) < 5}
    assert short == PRE_EXISTING_SHORT_PREFIXES, sorted(short - PRE_EXISTING_SHORT_PREFIXES)
    assert not (set(ACCEPTED) & PRE_EXISTING_SHORT_PREFIXES)
    assert min(len(p) for p in ACCEPTED) >= 5
    assert "KXMC" in DEFERRED and classify("KXMC-26AUG24-T") == UNCLASSIFIED
    assert classify("KXBRANDNEWTHING") == UNCLASSIFIED
    assert classify("") == UNCLASSIFIED
    assert classify("NOTAKALSHISERIES") == UNCLASSIFIED


# --------------------------------------------------------------- the design's own guardrails


def test_mode_still_defines_the_treatment_and_no_only_proxy_was_introduced():
    """The MMSELL 2x2's treatment is `mode=scheduled`, selecting through this taxonomy. The
    failure this guards against is the one docs/RESEARCH_MMSELL_2X2_PAPER_DESIGN.md §1 names:
    substituting `only=<series substrings>`, which is 88% crypto and reaches none of the
    non-crypto scheduled population. No book in the shipped config may define a
    settlement-mode treatment through `only=`."""
    settings = Settings(_env_file=None, kalshi_api_key_id="k", kalshi_private_key="p",
                        database_url="sqlite://", bot_mode="weather")
    books = {b["tag"]: b for b in settings.mmsell_variant_list}
    # `mode=` is still a real, parsed filter and still names taxonomy modes.
    mode_books = {t: b["mode"] for t, b in books.items() if b["mode"]}
    assert mode_books, "no book filters on mode= any more — the treatment path is gone"
    for tag, modes in mode_books.items():
        assert set(modes) <= KNOWN_MODES, tag
        assert not books[tag]["only"], (
            f"{tag} defines a settlement-mode book AND an only= series allowlist")
    # No MMSELL 2x2 arm exists yet: this repair registers, arms and starts nothing.
    assert not any(t.lower().startswith(("t2x2", "mmsell2x2")) for t in books)


def test_the_treatment_arm_spec_selects_through_the_taxonomy_not_a_series_list():
    """The pre-registered arms, parsed but NOT registered. Asserting them here keeps the design
    honest against a later edit that quietly swaps `mode=` for `only=`."""
    settings = Settings(
        _env_file=None, kalshi_api_key_id="k", kalshi_private_key="p",
        database_url="sqlite://", bot_mode="weather",
        mmsell_variants=(
            "mmsellT2X2t:lo=5,hi=10,maxyes=7,mode=scheduled,"
            "skip=BTC+ETH+SOL+DOGE+XRP+CRYPTO;"
            "mmsellT2X2c:lo=5,hi=10,maxyes=7,mode=in_play+discrete,"
            "skip=BTC+ETH+SOL+DOGE+XRP+CRYPTO"),
    )
    books = {b["tag"]: b for b in settings.mmsell_variant_list}
    t, c = books["mmsellT2X2t"], books["mmsellT2X2c"]
    assert t["mode"] == ["scheduled"] and c["mode"] == ["in_play", "discrete"]
    assert t["only"] == [] and c["only"] == []
    assert t["skip"] == c["skip"] == ["BTC", "ETH", "SOL", "DOGE", "XRP", "CRYPTO"]
    # The arms partition the taxonomy: every mode is in exactly one of them, and `unknown`
    # is in neither.
    assert set(t["mode"]) | set(c["mode"]) == KNOWN_MODES
    assert not (set(t["mode"]) & set(c["mode"]))
    assert UNCLASSIFIED[1] not in KNOWN_MODES
