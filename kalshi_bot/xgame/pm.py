"""Polymarket clients for the XGAME tape collector (public, read-only, no trading).

Gamma API: discover per-game markets ("Will <team> win on <YYYY-MM-DD>?") under sports
tag slugs — the same question format scripts/xvenue_game.py matches on. data-api: the
public trade tape per market (conditionId), newest first, limit/offset paged. Polymarket
is geofenced and never traded; its tape is consumed purely as the leading signal in the
XGAME lead-lag thesis (docs/IDEA_MODEL_20260704.md).
"""

from __future__ import annotations

import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"
UA = (  # both hosts sit behind Cloudflare; a browser UA avoids the default-client block
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
LIMIT = 100        # Gamma page cap
TRADES_LIMIT = 500  # data-api page cap

# Same market-question shape scripts/xvenue_game.py matches: 'Will <team> win on <date>?'
PM_WIN = re.compile(r"will\s+(.+?)\s+win on\s+(\d{4}-\d{2}-\d{2})", re.I)


def norm_team(s: str) -> str:
    """Lowercase, letters+spaces only — the venue-agnostic team key."""
    return re.sub(r"[^a-z ]", "", (s or "").lower()).strip()


class PmGamesClient:
    """Thin wrapper over the two public Polymarket APIs the collector needs."""

    def __init__(self, *, timeout: float = 20.0, transport: httpx.BaseTransport | None = None):
        headers = {"User-Agent": UA, "Accept": "application/json"}
        self._gamma = httpx.Client(
            base_url=GAMMA, timeout=timeout, headers=headers, transport=transport
        )
        self._data = httpx.Client(
            base_url=DATA, timeout=timeout, headers=headers, transport=transport
        )

    def close(self) -> None:
        self._gamma.close()
        self._data.close()

    def __enter__(self) -> PmGamesClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def game_markets(self, tags: list[str], pages: int) -> list[dict]:
        """Open per-game win markets under the tag slugs. Returns
        {day, team, condition_id, token_id, question} — token_id is the FULL
        clobTokenId of the YES outcome (never truncated)."""
        out: list[dict] = []
        seen: set[str] = set()
        for tag in tags:
            for page in range(pages):
                resp = self._gamma.get(
                    "/events",
                    params={"closed": "false", "tag_slug": tag,
                            "limit": LIMIT, "offset": page * LIMIT},
                )
                resp.raise_for_status()
                batch = resp.json()
                if not isinstance(batch, list) or not batch:
                    break
                for ev in batch:
                    for mk in ev.get("markets") or []:
                        parsed = self._parse_market(mk)
                        if parsed and parsed["token_id"] not in seen:
                            seen.add(parsed["token_id"])
                            out.append(parsed)
                if len(batch) < LIMIT:
                    break
        return out

    @staticmethod
    def _parse_market(mk: dict) -> dict | None:
        mq = PM_WIN.search(mk.get("question") or "")
        if not mq:
            return None
        toks = mk.get("clobTokenIds")
        if isinstance(toks, str):
            try:
                toks = json.loads(toks)
            except json.JSONDecodeError:
                toks = None
        if not (isinstance(toks, list) and toks):
            return None
        team = norm_team(mq.group(1))
        if not team:
            return None
        return {
            "day": mq.group(2),
            "team": team,
            "condition_id": str(mk.get("conditionId") or ""),
            "token_id": str(toks[0]),  # YES outcome token, full id
            "question": (mk.get("question") or "").strip(),
        }

    def trades(self, condition_id: str, *, limit: int = TRADES_LIMIT, offset: int = 0) -> list[dict]:
        """One page of the public tape for a market, newest first. Rows carry
        asset (token id), side BUY/SELL, price (0..1), size, timestamp (unix s),
        transactionHash."""
        resp = self._data.get(
            "/trades",
            params={"market": condition_id, "limit": limit, "offset": offset},
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return (data or {}).get("trades") or []
