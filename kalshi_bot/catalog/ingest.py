"""Bounded read-only source import and resumable public REST reconciliation."""

import json
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

from kalshi_bot.mmsell.market_types import classify

from .store import now

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
PAGE_SIZE = 1000
SOURCE_QUERIES = {
    "paper": """SELECT p.*, EXISTS(SELECT 1 FROM live_paper_twins t
                  WHERE t.twin_tag=p.strategy) AS is_twin FROM paper_trades p
                WHERE p.id > %s AND p.strategy ILIKE '%%mmsell%%' ORDER BY p.id LIMIT %s""",
    "live": """SELECT f.*, o.strategy,o.event_ticker,o.experiment_deployment_arm_id
                FROM fills f JOIN live_orders o ON o.kalshi_order_id=f.kalshi_order_id
                AND o.id=(SELECT min(x.id) FROM live_orders x
                         WHERE x.kalshi_order_id=f.kalshi_order_id)
                WHERE f.id > %s AND o.strategy ILIKE '%%mmsell%%' ORDER BY f.id LIMIT %s""",
}


def seed(store):
    manifest = json.loads((ROOT / "registry/series_manifest.json").read_text())
    for row in manifest["series"]:
        ticker = row["series"]
        current = store.get("series", ticker)
        if not current:
            store.upsert("series", ticker, {}, source="legacy_registry")
        with store.connect() as db:
            record = db.execute(
                "SELECT document FROM objects WHERE kind=? AND ticker=?", ("series", ticker)
            ).fetchone()
            doc = json.loads(record[0])
            doc["legacy_registry"] = row
            doc["legacy_classification"] = dict(
                zip(("contract_type", "settlement_mode"), classify(ticker), strict=True)
            )
            db.execute(
                "UPDATE objects SET document=? WHERE kind=? AND ticker=?",
                (json.dumps(doc), "series", ticker),
            )
    store.set_state("registry_seed", {"last_success_at": now(), "series": len(manifest["series"])})


def source_page(store, url, source, batch_size=PAGE_SIZE):
    cursor = store.state("cursor:" + source, {"after": 0, "initial_complete": False})
    # SET TRANSACTION READ ONLY is issued by psycopg before the first SELECT.
    with psycopg.connect(
        url.replace("postgresql+psycopg://", "postgresql://"),
        connect_timeout=10,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=15000",
    ) as conn:
        conn.read_only = True
        role = conn.execute(
            "SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls "
            "FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        writable = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND has_table_privilege("
            "current_user,quote_ident(table_schema)||'.'||quote_ident(table_name),"
            "'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER')) AS writable"
        ).fetchone()
        if not role or any(role.values()) or writable["writable"]:
            raise PermissionError("Source role must have only SELECT privileges")
        records = conn.execute(SOURCE_QUERIES[source], (cursor["after"], batch_size)).fetchall()
    next_cursor = {**cursor, "last_success_at": now()}
    if records:
        next_cursor["after"] = records[-1]["id"]
    else:
        next_cursor["initial_complete"] = True
        next_cursor["last_complete_at"] = now()
    store.evidence_page(source, records, next_cursor)
    return len(records)


def reset_source_reconciliation(store):
    for source in SOURCE_QUERIES:
        cursor = store.state("cursor:" + source, {"initial_complete": False})
        store.set_state("cursor:" + source, {**cursor, "after": 0})


class Discovery:
    def __init__(self, store, client=None):
        self.store = store
        self.client = client or httpx.Client(base_url=BASE_URL, timeout=30)

    def page(self, job):
        state = self.store.state("discovery:" + job, {"cursor": None, "complete": False})
        if state["complete"]:
            return 0
        if job == "series":
            path, key, params = "/series", "series", {}
        else:
            path, key, params = "/events", "events", {"limit": 200, "with_nested_markets": "true"}
        if state["cursor"]:
            params["cursor"] = state["cursor"]
        response = self.client.get(path, params=params)
        response.raise_for_status()
        data = response.json()
        for row in data[key]:
            ticker = row["ticker"] if job == "series" else row["event_ticker"]
            series = ticker if job == "series" else row["series_ticker"]
            self.store.upsert(
                "series" if job == "series" else "event",
                ticker,
                {k: v for k, v in row.items() if k != "markets"},
                series,
            )
            if job != "series":
                for market in row.get("markets", []):
                    self.store.upsert("market", market["ticker"], market, series)
        self.store.set_state(
            "discovery:" + job,
            {
                "cursor": data.get("cursor"),
                "complete": not bool(data.get("cursor")),
                "last_success_at": now(),
            },
        )
        return len(data[key])

    def updates(self):
        """Incremental updates include new listings/outcomes; daily event sweep repairs gaps."""
        from datetime import datetime, timezone

        initial_since = int(datetime.now(timezone.utc).timestamp()) - 86400
        state = self.store.state("discovery:updates", {"cursor": None, "since": initial_since})
        if not state["cursor"]:
            state["scan_started_at"] = int(datetime.now(timezone.utc).timestamp())
        params = {
            "limit": 1000,
            "mve_filter": "exclude",
            "min_updated_ts": state["since"],
            "max_updated_ts": state["scan_started_at"],
        }
        if state["cursor"]:
            params["cursor"] = state["cursor"]
        response = self.client.get("/markets", params=params)
        response.raise_for_status()
        data = response.json()
        for market in data["markets"]:
            series = market.get("series_ticker") or market["ticker"].split("-", 1)[0]
            self.store.upsert("market", market["ticker"], market, series)
        next_state = {**state, "cursor": data.get("cursor"), "last_success_at": now()}
        if not next_state["cursor"]:
            # overlap avoids boundary precision races
            next_state["since"] = max(0, next_state["scan_started_at"] - 60)
        self.store.set_state("discovery:updates", next_state)
        return len(data["markets"])

    def reset(self):
        for job in ("series", "events"):
            self.store.set_state("discovery:" + job, {"cursor": None, "complete": False})
