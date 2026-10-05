"""Single-writer volume store. Revisions and assessments are immutable snapshots."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SEMANTIC_FIELDS = (
    "resolution_mechanism",
    "observation_start",
    "observation_end",
    "alternatives",
    "alternative_operator",
    "exclusions",
    "payout_relationship",
    "shared_exposure_key",
    "settlement_source",
    "risk_components",
)
RULE_FIELDS = (
    "rules_primary",
    "rules_secondary",
    "early_close_condition",
    "can_close_early",
    "settlement_timer_seconds",
    "strike_type",
    "floor_strike",
    "cap_strike",
    "functional_strike",
    "custom_strike",
    "title",
    "yes_sub_title",
    "no_sub_title",
    "open_time",
    "close_time",
    "expected_expiration_time",
    "settlement_sources",
    "contract_url",
    "contract_terms_url",
    "fee_type",
    "fee_multiplier",
    "mutually_exclusive",
)


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def rule_hash(raw):
    return digest({key: raw.get(key) for key in RULE_FIELDS})


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS objects (
              kind TEXT, ticker TEXT, series TEXT, rules_hash TEXT, updated_at TEXT,
              document TEXT NOT NULL, PRIMARY KEY(kind,ticker));
            CREATE INDEX IF NOT EXISTS objects_series ON objects(kind,series);
            CREATE TABLE IF NOT EXISTS revisions (
              kind TEXT, ticker TEXT, hash TEXT, captured_at TEXT, document TEXT,
              PRIMARY KEY(kind,ticker,hash));
            CREATE TABLE IF NOT EXISTS reviews (
              id TEXT PRIMARY KEY, kind TEXT, ticker TEXT, rules_hash TEXT,
              template INTEGER, created_at TEXT, document TEXT);
            CREATE INDEX IF NOT EXISTS reviews_lookup ON reviews(kind,ticker,rules_hash);
            CREATE TABLE IF NOT EXISTS evidence (
              source TEXT, source_id INTEGER, series TEXT, document TEXT,
              PRIMARY KEY(source,source_id));
            CREATE INDEX IF NOT EXISTS evidence_series ON evidence(series);
            CREATE TABLE IF NOT EXISTS assessments (
              id TEXT PRIMARY KEY, scope TEXT, as_of TEXT, document TEXT);
            CREATE TABLE IF NOT EXISTS current_assessments (
              scope TEXT PRIMARY KEY, assessment_id TEXT);
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, document TEXT);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def put_state(db, key, value):
        db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, encode(value)))

    def state(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT document FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_state(self, key, value):
        with self.connect() as db:
            self.put_state(db, key, value)

    def upsert(self, kind, ticker, raw, series=None, source="kalshi_rest"):
        with self.connect() as db:
            previous = db.execute(
                "SELECT document FROM objects WHERE kind=? AND ticker=?", (kind, ticker)
            ).fetchone()
            old = json.loads(previous[0]) if previous else {}
            stamp = now()
            parents = {}
            for parent_kind, parent_ticker in (
                ("series", series),
                ("event", raw.get("event_ticker")),
            ):
                if parent_ticker and parent_kind != kind:
                    parent = db.execute(
                        "SELECT rules_hash FROM objects WHERE kind=? AND ticker=?",
                        (parent_kind, parent_ticker),
                    ).fetchone()
                    parents[parent_kind + ":" + parent_ticker] = parent[0] if parent else None
            document = {
                "schema_version": 1,
                "kind": kind,
                "ticker": ticker,
                "series_ticker": series or ticker,
                "source": source,
                "first_seen_at": old.get("first_seen_at", stamp),
                "last_seen_at": stamp,
                "exchange_created_at": raw.get("created_time"),
                "rules_hash": digest({"rules": rule_hash(raw), "parents": parents}),
                "parent_rules_hashes": parents,
                "raw": raw,
                "legacy_registry": old.get("legacy_registry"),
                "legacy_classification": old.get("legacy_classification"),
            }
            semantic = digest(
                {
                    "rules_hash": document["rules_hash"],
                    "source": source,
                    "status": raw.get("status"),
                    "result": raw.get("result"),
                    "settlement_ts": raw.get("settlement_ts"),
                }
            )
            db.execute(
                "INSERT OR IGNORE INTO revisions VALUES (?,?,?,?,?)",
                (kind, ticker, semantic, stamp, encode(document)),
            )
            db.execute(
                "INSERT OR REPLACE INTO objects VALUES (?,?,?,?,?,?)",
                (
                    kind,
                    ticker,
                    document["series_ticker"],
                    document["rules_hash"],
                    stamp,
                    encode(document),
                ),
            )
        return document

    def get(self, kind, ticker):
        with self.connect() as db:
            row = db.execute(
                "SELECT document FROM objects WHERE kind=? AND ticker=?", (kind, ticker)
            ).fetchone()
        return self.decorate(json.loads(row[0])) if row else None

    def decorate(self, doc):
        with self.connect() as db:
            review = db.execute(
                "SELECT document FROM reviews WHERE kind=? AND ticker=? "
                "AND rules_hash=? ORDER BY created_at DESC LIMIT 1",
                (doc["kind"], doc["ticker"], doc["rules_hash"]),
            ).fetchone()
            if not review and doc["kind"] == "market":
                review = db.execute(
                    "SELECT r.document FROM reviews r JOIN objects o "
                    "ON o.kind=r.kind AND o.ticker=r.ticker "
                    "WHERE r.kind=? AND o.series=? AND r.rules_hash=? "
                    "AND o.rules_hash=r.rules_hash AND r.template=1 "
                    "ORDER BY r.created_at DESC LIMIT 1",
                    ("market", doc["series_ticker"], doc["rules_hash"]),
                ).fetchone()
        parent_changed = False
        with self.connect() as db:
            for identity, captured_hash in doc.get("parent_rules_hashes", {}).items():
                parent_kind, parent_ticker = identity.split(":", 1)
                parent = db.execute(
                    "SELECT rules_hash FROM objects WHERE kind=? AND ticker=?",
                    (parent_kind, parent_ticker),
                ).fetchone()
                if (parent[0] if parent else None) != captured_hash:
                    parent_changed = True
        facts = json.loads(review[0]) if review and not parent_changed else None
        doc["review"] = facts
        doc["review_status"] = "reviewed" if facts else "needs_review"
        doc["semantics"] = facts["semantics"] if facts else dict.fromkeys(SEMANTIC_FIELDS)
        doc["review_completion_pct"] = 100 if facts else 0
        raw = doc["raw"]
        from .evaluators import timestamp

        stamp = timestamp(now())
        expected = timestamp(raw.get("expected_expiration_time"))
        close = timestamp(raw.get("close_time"))
        doc["timing"] = {
            "trading_close_at": raw.get("close_time"),
            "expected_determination_at": raw.get("expected_expiration_time"),
            "latest_expiration_at": raw.get("latest_expiration_time") or raw.get("expiration_time"),
            "settlement_timer_seconds": raw.get("settlement_timer_seconds"),
            "observed_settlement_at": raw.get("settlement_ts"),
            "expected_payout_at": None,
            "hours_to_close": (close - stamp).total_seconds() / 3600 if close else None,
            "hours_to_expected_determination": (expected - stamp).total_seconds() / 3600
            if expected
            else None,
            "observation_start": doc["semantics"].get("observation_start"),
            "observation_end": doc["semantics"].get("observation_end"),
        }
        doc["contract_type"] = raw.get("market_type")
        doc["settlement_type"] = doc["semantics"].get("resolution_mechanism")
        doc["parent_rules_changed"] = parent_changed
        doc["risk_score"] = None
        return doc

    def list_objects(self, kind, limit=100, offset=0, series=None, needs_review=False):
        sql, args = "SELECT document FROM objects WHERE kind=?", [kind]
        if series:
            sql += " AND series=?"
            args.append(series)
        # Review queue pagination is applied AFTER review classification.
        sql += " ORDER BY ticker"
        if not needs_review:
            sql += " LIMIT ? OFFSET ?"
            args.extend([limit, offset])
        with self.connect() as db:
            docs = [json.loads(row[0]) for row in db.execute(sql, args)]
        docs = [self.decorate(doc) for doc in docs]
        return (
            [d for d in docs if d["review_status"] == "needs_review"][offset : offset + limit]
            if needs_review
            else docs
        )

    def review(self, payload):
        required = ("kind", "ticker", "rules_hash", "actor", "rationale", "semantics")
        if any(not payload.get(key) for key in required):
            raise ValueError(
                "Review requires identity, current hash, actor, rationale and semantics"
            )
        semantics = payload["semantics"]
        if not isinstance(semantics, dict) or any(key not in semantics for key in SEMANTIC_FIELDS):
            raise ValueError("All semantic fields must be explicit; null means unknown")
        if any(
            semantics.get(key) is None
            for key in ("resolution_mechanism", "settlement_source", "shared_exposure_key")
        ):
            raise ValueError("Resolution, settlement source and exposure identity must be known")
        if payload.get("template") and any(
            semantics.get(key) is not None for key in ("observation_start", "observation_end")
        ):
            raise ValueError("Reusable templates cannot carry market-specific observation times")
        current = self.get(payload["kind"], payload["ticker"])
        if current and current["parent_rules_changed"]:
            raise LookupError("Parent rules changed; wait for the current listing refresh")
        with self.connect() as db:
            row = db.execute(
                "SELECT rules_hash FROM objects WHERE kind=? AND ticker=?",
                (payload["kind"], payload["ticker"]),
            ).fetchone()
            if not row or row[0] != payload["rules_hash"]:
                raise LookupError("Rules changed or object missing; review the current version")
            result = {**payload, "created_at": now()}
            result["id"] = digest(result)
            db.execute(
                "INSERT INTO reviews VALUES (?,?,?,?,?,?,?)",
                (
                    result["id"],
                    payload["kind"],
                    payload["ticker"],
                    payload["rules_hash"],
                    bool(payload.get("template")),
                    result["created_at"],
                    encode(result),
                ),
            )
        return result

    def evidence_page(self, source, records, cursor):
        with self.connect() as db:
            for record in records:
                series = record["market_ticker"].split("-", 1)[0]
                db.execute(
                    "INSERT OR REPLACE INTO evidence VALUES (?,?,?,?)",
                    (source, record["id"], series, encode(record)),
                )
            self.put_state(db, "cursor:" + source, cursor)

    def evidence(self):
        with self.connect() as db:
            return [
                (row["source"], json.loads(row["document"]))
                for row in db.execute("SELECT source,document FROM evidence")
            ]

    def assessment(self, scope, result):
        assessment_id = digest(result)
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO assessments VALUES (?,?,?,?)",
                (assessment_id, scope, result["as_of"], encode(result)),
            )
            db.execute(
                "INSERT OR REPLACE INTO current_assessments VALUES (?,?)", (scope, assessment_id)
            )
        return assessment_id

    def assessments(self, strategy=None, series=None, qualified=False, min_edge=None):
        with self.connect() as db:
            rows = db.execute(
                "SELECT a.id,a.document FROM assessments a "
                "JOIN current_assessments c ON c.assessment_id=a.id"
            )
            results = [{"assessment_id": row[0], **json.loads(row[1])} for row in rows]
        return [
            r
            for r in results
            if (not strategy or r["strategy_id"] == strategy)
            and (not series or r["series_ticker"] == series)
            and (not qualified or r["qualified"])
            and (
                min_edge is None
                or (
                    r["edge_cents_per_contract"] is not None
                    and r["edge_cents_per_contract"] >= min_edge
                )
            )
        ]

    def history(self, table, kind, ticker, limit=100):
        if table not in ("revisions", "reviews"):
            raise ValueError("Invalid history table")
        with self.connect() as db:
            rows = db.execute(
                f"SELECT document FROM {table} WHERE kind=? AND ticker=? "
                "ORDER BY rowid DESC LIMIT ?",
                (kind, ticker, limit),
            )
            return [json.loads(row[0]) for row in rows]

    def status(self):
        with self.connect() as db:
            counts = {
                row[0]: row[1]
                for row in db.execute("SELECT kind,count(*) FROM objects GROUP BY kind")
            }
            evidence = {
                row[0]: row[1]
                for row in db.execute("SELECT source,count(*) FROM evidence GROUP BY source")
            }
            states = {row[0]: json.loads(row[1]) for row in db.execute("SELECT * FROM state")}
            assessments = db.execute("SELECT count(*) FROM current_assessments").fetchone()[0]
        return {
            "objects": counts,
            "evidence": evidence,
            "assessment_contexts": assessments,
            "jobs": states,
            "consumer_cutover": False,
            "confidence_calibrated": False,
        }
