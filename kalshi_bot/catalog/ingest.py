"""Bounded read-only source import and resumable public REST reconciliation."""

import json
import logging
import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

from kalshi_bot.mmsell.market_types import classify

from .store import now, pack, unpack

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
PAGE_SIZE = 1000
SOURCE_RELATIONS = {
    "paper": "FROM paper_trades p WHERE p.strategy ILIKE '%%mmsell%%'",
    "live": """FROM fills f JOIN live_orders o ON o.kalshi_order_id=f.kalshi_order_id
                AND o.id=(SELECT min(x.id) FROM live_orders x
                         WHERE x.kalshi_order_id=f.kalshi_order_id)
                WHERE o.strategy ILIKE '%%mmsell%%'""",
}
SOURCE_PROJECTIONS = {
    "paper": """p.*, EXISTS(SELECT 1 FROM live_paper_twins t
                              WHERE t.twin_tag=p.strategy) AS is_twin""",
    "live": "f.*, o.strategy,o.event_ticker,o.experiment_deployment_arm_id",
}
SOURCE_IDS = {"paper": "p.id", "live": "f.id"}
SOURCE_QUERIES = {
    source: f"SELECT {SOURCE_PROJECTIONS[source]} {relation} "
    f"AND {SOURCE_IDS[source]} > %s ORDER BY {SOURCE_IDS[source]} LIMIT %s"
    for source, relation in SOURCE_RELATIONS.items()
}
SOURCE_COVERAGE_QUERIES = {
    source: "SELECT count(*) AS source_records,encode(sha256(convert_to("
    f"coalesce(string_agg({SOURCE_IDS[source]}::text,',' ORDER BY {SOURCE_IDS[source]}),''),"
    f"'UTF8')),'hex') AS source_ids_fingerprint {relation} AND {SOURCE_IDS[source]} <= %s"
    for source, relation in SOURCE_RELATIONS.items()
}


class DiscoveryDeferred(Exception):
    def __init__(self, retry_at):
        self.retry_at = retry_at
        super().__init__("Public discovery provider backoff")


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
            doc = unpack(record[0])
            doc["legacy_registry"] = row
            doc["legacy_classification"] = dict(
                zip(("contract_type", "settlement_mode"), classify(ticker), strict=True)
            )
            db.execute(
                "UPDATE objects SET document=? WHERE kind=? AND ticker=?",
                (pack(doc), "series", ticker),
            )
    store.set_state("registry_seed", {"last_success_at": now(), "series": len(manifest["series"])})


def source_page(store, url, source, batch_size=PAGE_SIZE):
    cursor = store.state("cursor:" + source, {"after": 0, "initial_complete": False})
    coverage = None
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
        permissions = {
            "checked_at": now(),
            "role_found": bool(role),
            "elevated_flags": [key for key, enabled in (role or {}).items() if enabled],
            "public_table_write_privileges": bool(writable["writable"]),
        }
        permissions["accepted"] = (
            permissions["role_found"]
            and not permissions["elevated_flags"]
            and not permissions["public_table_write_privileges"]
        )
        store.set_state("source_permissions", permissions)
        if not permissions["accepted"]:
            # Fixed catalog flags only: no role name, URL or exception message.
            logging.getLogger("market_catalog").error(
                "source_permissions=%s", json.dumps(permissions)
            )
            raise PermissionError("Source role must have only SELECT privileges")
        records = conn.execute(SOURCE_QUERIES[source], (cursor["after"], batch_size)).fetchall()
        last_check = cursor.get("last_coverage_check_at")
        check_due = (
            not last_check
            or (datetime.now(timezone.utc) - datetime.fromisoformat(last_check)).total_seconds()
            >= 300
        )
        if not records and check_due:
            coverage = {
                **conn.execute(SOURCE_COVERAGE_QUERIES[source], (cursor["after"],)).fetchone(),
                "through_source_id": cursor["after"],
                "checked_at": now(),
                "definition": "sorted-source-ids-sha256-v1",
            }
    next_cursor = {
        **cursor,
        "last_success_at": now(),
        "initial_complete": bool(cursor.get("initial_coverage_verified")),
    }
    if records:
        next_cursor["after"] = records[-1]["id"]
        next_cursor["reconciliation_complete"] = False
    result = store.evidence_page(source, records, next_cursor, coverage)
    if result:
        logging.getLogger("market_catalog").info(
            "source_coverage=%s", json.dumps({"source": source, **result})
        )
    return len(records)


def reset_source_reconciliation(store):
    for source in SOURCE_QUERIES:
        cursor = store.state("cursor:" + source, {"initial_complete": False})
        store.set_state(
            "cursor:" + source,
            {
                **cursor,
                "after": 0,
                "reconciliation_complete": False,
                "last_coverage_check_at": None,
            },
        )


class Discovery:
    def __init__(self, store, client=None):
        self.store = store
        self.client = client or httpx.Client(base_url=BASE_URL, timeout=30)

    def get_json(self, path, params):
        stamp = time.time()
        backoff = self.store.state("discovery:backoff", {})
        if backoff.get("not_before_unix", 0) > stamp:
            raise DiscoveryDeferred(backoff["not_before_unix"])
        response = self.client.get(path, params=params)
        if response.status_code == 429 or response.status_code >= 500:
            stamp = time.time()  # Retry-After is measured from response receipt.
            failures = min(16, backoff.get("failures", 0) + 1)
            delay = min(900, 60 * 2 ** min(failures - 1, 4))
            hint = response.headers.get("Retry-After")
            try:
                retry_delay = float(hint)
            except (TypeError, ValueError):
                try:
                    date = parsedate_to_datetime(hint)
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=timezone.utc)
                    retry_delay = date.timestamp() - stamp
                except (TypeError, ValueError, OverflowError):
                    retry_delay = 0
            if math.isfinite(retry_delay) and retry_delay > 0:
                delay = max(delay, retry_delay)
            self.store.set_state(
                "discovery:backoff",
                {
                    "failures": failures,
                    "not_before_unix": stamp + delay,
                    "http_status": response.status_code,
                    "recorded_at": now(),
                },
            )
        response.raise_for_status()
        self.store.set_state("discovery:backoff", {"failures": 0, "not_before_unix": 0})
        return response.json()

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
        data = self.get_json(path, params)
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
        data = self.get_json("/markets", params)
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
