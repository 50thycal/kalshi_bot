"""Universe review tiers — what mmsell is allowed to trade, and how well we know it.

THE PROBLEM. mmsell sells any cheap tail it finds, and Kalshi lists new series faster than
anyone classifies them. Measured 2026-09-05 across the whole mmsell family: **81 of 400 traded
series are in no taxonomy at all**, carrying 1,158 settled markets — and **786 of those are the
new season arriving** (KXNCAAFSPREAD 204, KXNCAAFTOTAL 184, KXEPLTOTAL 80, KXEPLSCORE 66, plus
Serie A, Bundesliga, Ligue 1 and NFL first-half markets). On the LIVE canary `Dmmsell10`, 20.2%
of trades over 30 days were in unclassified series. The live book has been selling tails in
contracts nobody has ever reviewed, and the share is RISING because it tracks new listings:
older books read 0.7-6%, the newest live book reads 20%.

WHAT THIS IS NOT. It is not an edge filter and must never be sold as one. Over the last 30 days
the unclassified slice was PROFITABLE (+$45.18 all-time across the family), and a graduated
series can be catastrophic — `KXNFLSPREAD` is classified, has 382 settled markets, and has lost
$166.55. Graduation says "we know what this contract is and we have history on it", never "this
contract makes money". The two are independent and conflating them is how a governance rule
turns into an unvalidated strategy.

THE TIERS.

    GRADUATED     in the market-type taxonomy AND carrying enough of our own settled history to
                  have been reviewed. Tradeable anywhere, live included.
    IN_REVIEW     classified, but too thin for anyone to have reviewed it yet. Paper only: it
                  keeps collecting the history that would graduate it, and risks no real money
                  doing so.
    UNCLASSIFIED  in no taxonomy. Not traded by any book that opts into tiering, and surfaced
                  by `scripts/mmsell_universe_review.py` as the review queue.

WHY A STATIC MANIFEST rather than a live count. Graduation is a REVIEWED act — someone looked at
the series and said we understand how it settles. Deriving it from a row count at entry time
would make it automatic, which is precisely what it must not be: a series would silently
graduate itself by trading enough, and the review the tier exists to force would never happen.
It is the same argument `market_types.SERIES_TYPES` makes for being a hand-audited table rather
than a regex — the classification IS the claim, so it has to be reviewable in a diff.

Seeded 2026-09-05 from every series the mmsell family has traded with >= 20 settled markets of
own history AND a market-type classification: 138 series, 87.5% of all settled markets.
Everything below that bar starts at IN_REVIEW and graduates by PR, never by accumulation.
"""

from __future__ import annotations

from .market_types import UNCLASSIFIED as UNCLASSIFIED_TYPE
from .market_types import classify

GRADUATED = "graduated"
IN_REVIEW = "in_review"
UNCLASSIFIED = "unclassified"

#: Ordered weakest-to-strongest. A book naming a minimum tier admits that tier and everything
#: above it, so the ordering is the whole semantics of `admits`.
TIER_ORDER: tuple[str, ...] = (UNCLASSIFIED, IN_REVIEW, GRADUATED)

#: Series prefixes that have been REVIEWED and carry enough own history to trade live.
#: Longest-prefix match, same convention as SERIES_TYPES. Adding one is a deliberate PR.
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


def tier_of(series: str) -> str:
    """The review tier for a series ticker.

    UNCLASSIFIED wins over everything: a series with no market-type entry cannot be graduated
    even if somebody adds its prefix to the manifest by mistake, because we would still not know
    how it settles. The two tables have to agree before a series is tradeable live, and this is
    where that is enforced."""
    s = (series or "").upper()
    if classify(s) == UNCLASSIFIED_TYPE:
        return UNCLASSIFIED
    if any(s.startswith(p) for p in GRADUATED_SERIES):
        return GRADUATED
    return IN_REVIEW


def admits(series: str, min_tier: str | None) -> bool:
    """Whether a book requiring `min_tier` may trade this series.

    `None` (or an unknown tier) admits everything, which is every book that has not opted in —
    so tiering is inert for the existing cohort rather than silently narrowing it."""
    if not min_tier or min_tier not in TIER_ORDER:
        return True
    return TIER_ORDER.index(tier_of(series)) >= TIER_ORDER.index(min_tier)
