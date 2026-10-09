"""Single-writer volume store. Revisions and assessments are immutable snapshots."""

import hashlib
import json
import shutil
import sqlite3
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DOCUMENT_TABLES = ("objects", "revisions", "reviews", "evidence", "assessments")
COMPRESSED_PREFIX = b"catalog:zlib:1\x00"

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


def pack(value):
    """Lossless storage encoding; content hashes always use canonical JSON."""
    text = encode(value)
    raw = text.encode()
    if len(raw) >= 1024:
        compressed = COMPRESSED_PREFIX + zlib.compress(raw)
        if len(compressed) + 32 < len(raw):
            return compressed
    return text


def unpack(document):
    """Read both the original JSON TEXT and the versioned compressed BLOB."""
    if isinstance(document, bytes) and document.startswith(COMPRESSED_PREFIX):
        document = zlib.decompress(document[len(COMPRESSED_PREFIX) :])
    return json.loads(document)


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
            CREATE TABLE IF NOT EXISTS review_migrations (
              id TEXT PRIMARY KEY, series TEXT, document BLOB);
            CREATE TABLE IF NOT EXISTS current_review_migrations (
              series TEXT PRIMARY KEY, migration_id TEXT);
            CREATE TABLE IF NOT EXISTS live_economics (
              id TEXT PRIMARY KEY, ticker TEXT, document BLOB);
            CREATE TABLE IF NOT EXISTS current_live_economics (
              ticker TEXT PRIMARY KEY, economics_id TEXT);
            CREATE TABLE IF NOT EXISTS contract_blobs (
              sha256 TEXT PRIMARY KEY, content BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS contract_observations (
              id TEXT PRIMARY KEY, url TEXT, sha256 TEXT, retrieved_at TEXT, document BLOB);
            CREATE INDEX IF NOT EXISTS contract_observations_url
              ON contract_observations(url,retrieved_at);
            CREATE TABLE IF NOT EXISTS contract_targets (
              url TEXT PRIMARY KEY, next_check REAL NOT NULL, status TEXT NOT NULL,
              capture_id TEXT, attempted_at TEXT, error TEXT);
            CREATE INDEX IF NOT EXISTS contract_targets_due ON contract_targets(next_check,url);
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

    @staticmethod
    def _state_in(db, key):
        row = db.execute("SELECT document FROM state WHERE key=?", (key,)).fetchone()
        return unpack(row[0]) if row else None

    def state(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT document FROM state WHERE key=?", (key,)).fetchone()
        return unpack(row[0]) if row else default

    def set_state(self, key, value):
        with self.connect() as db:
            self.put_state(db, key, value)

    def upsert(self, kind, ticker, raw, series=None, source="kalshi_rest"):
        with self.connect() as db:
            previous = db.execute(
                "SELECT document FROM objects WHERE kind=? AND ticker=?", (kind, ticker)
            ).fetchone()
            old = unpack(previous[0]) if previous else {}
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
                    "settlement_value_dollars": raw.get("settlement_value_dollars"),
                }
            )
            db.execute(
                "INSERT OR IGNORE INTO revisions VALUES (?,?,?,?,?)",
                (kind, ticker, semantic, stamp, pack(document)),
            )
            db.execute(
                "INSERT OR REPLACE INTO objects VALUES (?,?,?,?,?,?)",
                (
                    kind,
                    ticker,
                    document["series_ticker"],
                    document["rules_hash"],
                    stamp,
                    pack(document),
                ),
            )
        return document

    def get(self, kind, ticker):
        with self.connect() as db:
            row = db.execute(
                "SELECT document FROM objects WHERE kind=? AND ticker=?", (kind, ticker)
            ).fetchone()
        return self.decorate(unpack(row[0])) if row else None

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
        facts = unpack(review[0]) if review and not parent_changed else None
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
        from .documents import references

        doc["contract_documents"] = references(self, doc)
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
            docs = [unpack(row[0]) for row in db.execute(sql, args)]
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
                    pack(result),
                ),
            )
        return result

    def evidence_page(self, source, records, cursor, coverage=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = 0
            for record in records:
                # A reconciliation pass re-reads rows already held. An identical row is not a
                # change: rewriting it churns pages and made every pass look like new evidence.
                existing = db.execute(
                    "SELECT document FROM evidence WHERE source=? AND source_id=?",
                    (source, record["id"]),
                ).fetchone()
                if existing is not None and encode(unpack(existing[0])) == encode(record):
                    continue
                series = record["market_ticker"].split("-", 1)[0]
                db.execute(
                    "INSERT OR REPLACE INTO evidence VALUES (?,?,?,?)",
                    (source, record["id"], series, pack(record)),
                )
                changed += 1
            if changed:
                self.put_state(
                    db, "evidence:changes", (self._state_in(db, "evidence:changes") or 0) + changed
                )
            if coverage is not None:
                fingerprint = hashlib.sha256()
                local_records = 0
                for row in db.execute(
                    "SELECT source_id FROM evidence WHERE source=? AND source_id<=? "
                    "ORDER BY source_id",
                    (source, coverage["through_source_id"]),
                ):
                    if local_records:
                        fingerprint.update(b",")
                    fingerprint.update(str(row[0]).encode())
                    local_records += 1
                matched = (
                    local_records == coverage["source_records"]
                    and fingerprint.hexdigest() == coverage["source_ids_fingerprint"]
                )
                coverage = {
                    **coverage,
                    "local_records": local_records,
                    "local_ids_fingerprint": fingerprint.hexdigest(),
                    "ids_match": matched,
                    "payload_parity_verified": False,
                }
                cursor = {
                    **cursor,
                    "initial_coverage_verified": bool(
                        cursor.get("initial_coverage_verified") or matched
                    ),
                    "initial_complete": bool(cursor.get("initial_coverage_verified") or matched),
                    "reconciliation_complete": matched,
                    "last_coverage_check_at": coverage["checked_at"],
                }
                if matched:
                    cursor["last_complete_at"] = coverage["checked_at"]
                self.put_state(db, "source_coverage:" + source, coverage)
            self.put_state(db, "cursor:" + source, cursor)
        return coverage

    def evidence(self):
        with self.connect() as db:
            return [
                (row["source"], unpack(row["document"]))
                for row in db.execute("SELECT source,document FROM evidence")
            ]

    def migrate_review(self, document):
        migration_id = digest(document)
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO review_migrations VALUES (?,?,?)",
                (migration_id, document["series_ticker"], pack(document)),
            )
            db.execute(
                "INSERT OR REPLACE INTO current_review_migrations VALUES (?,?)",
                (document["series_ticker"], migration_id),
            )
        return migration_id

    def pipeline_items(self, kind, limit=100, offset=0, series=None):
        items, total = [], 0
        for record in self.iter_pipeline_items(kind, series):
            if offset <= total < offset + limit:
                items.append(record)
            total += 1
        return {"items": items, "total": total, "advisory_only": True}

    def iter_pipeline_items(self, kind, series=None):
        """Stream current pipeline documents without materialising their history."""
        tables = {
            "review-migrations": (
                "review_migrations",
                "current_review_migrations",
                "migration_id",
                "series",
            ),
            "live-economics": (
                "live_economics",
                "current_live_economics",
                "economics_id",
                "ticker",
            ),
        }
        table, current, reference, key = tables[kind]
        with self.connect() as db:
            rows = db.execute(
                f"SELECT a.id,a.document FROM {table} a "
                f"JOIN {current} c ON c.{reference}=a.id ORDER BY a.{key}"
            )
            for row in rows:
                record = {"record_id": row[0], **unpack(row[1])}
                if not series or record["series_ticker"] == series:
                    yield record

    def save_live_economics(self, documents):
        active = set()
        with self.connect() as db:
            for document in documents:
                ticker = document["market_ticker"]
                active.add(ticker)
                record_id = digest(document)
                db.execute(
                    "INSERT OR IGNORE INTO live_economics VALUES (?,?,?)",
                    (record_id, ticker, pack(document)),
                )
                db.execute(
                    "INSERT OR REPLACE INTO current_live_economics VALUES (?,?)",
                    (ticker, record_id),
                )
            for row in db.execute("SELECT ticker FROM current_live_economics").fetchall():
                if row[0] not in active:
                    db.execute("DELETE FROM current_live_economics WHERE ticker=?", (row[0],))

    def assessment_batch(self, results):
        # One transaction for snapshots, fingerprints and selection: no half-written refresh.
        active = set()
        with self.connect() as db:
            previous = {
                r[0]: unpack(r[1])
                for r in db.execute(
                    "SELECT key,document FROM state WHERE key LIKE 'assessment_input:%'"
                )
            }
            for scope, result in results:
                active.add(scope)
                key = "assessment_input:" + scope
                if previous.get(key) != result["input_fingerprint"]:
                    assessment_id = digest(result)
                    db.execute(
                        "INSERT OR IGNORE INTO assessments VALUES (?,?,?,?)",
                        (assessment_id, scope, result["as_of"], pack(result)),
                    )
                    db.execute(
                        "INSERT OR REPLACE INTO current_assessments VALUES (?,?)",
                        (scope, assessment_id),
                    )
                    self.put_state(db, key, result["input_fingerprint"])
            for row in db.execute("SELECT scope FROM current_assessments").fetchall():
                if row[0] not in active:
                    db.execute("DELETE FROM current_assessments WHERE scope=?", (row[0],))
                    db.execute("DELETE FROM state WHERE key=?", ("assessment_input:" + row[0],))
        return len(active)

    def assessment(self, scope, result):
        assessment_id = digest(result)
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO assessments VALUES (?,?,?,?)",
                (assessment_id, scope, result["as_of"], pack(result)),
            )
            db.execute(
                "INSERT OR REPLACE INTO current_assessments VALUES (?,?)", (scope, assessment_id)
            )
        return assessment_id

    def iter_assessments(self, strategy=None, series=None, qualified=False, min_edge=None):
        with self.connect() as db:
            rows = db.execute(
                "SELECT a.id,a.document FROM assessments a "
                "JOIN current_assessments c ON c.assessment_id=a.id ORDER BY c.scope"
            )
            for row in rows:
                record = {"assessment_id": row[0], **unpack(row[1])}
                if (
                    (not strategy or record["strategy_id"] == strategy)
                    and (not series or record["series_ticker"] == series)
                    and (not qualified or record["qualified"])
                    and (
                        min_edge is None
                        or (
                            record["edge_cents_per_contract"] is not None
                            and record["edge_cents_per_contract"] >= min_edge
                        )
                    )
                ):
                    yield record

    def assessments(self, strategy=None, series=None, qualified=False, min_edge=None):
        return list(self.iter_assessments(strategy, series, qualified, min_edge))

    def assessment_page(
        self,
        strategy=None,
        series=None,
        qualified=False,
        min_edge=None,
        source=None,
        minimum=None,
        settlement_type=None,
        limit=100,
        offset=0,
    ):
        # Decode one snapshot at a time; do not hold the full historical universe in RAM.
        items, total, facts = [], 0, {}
        for record in self.iter_assessments(strategy, series, qualified, min_edge):
            if source and record["evidence_source"] != source:
                continue
            if minimum is not None and (
                record["confidence_score"] is None or record["confidence_score"] < minimum
            ):
                continue
            if settlement_type:
                ticker = record["series_ticker"]
                if ticker not in facts:
                    facts[ticker] = self.get("series", ticker)
                fact = facts[ticker]
                if (
                    not fact
                    or fact["review_status"] != "reviewed"
                    or fact["settlement_type"] != settlement_type
                ):
                    continue
            if offset <= total < offset + limit:
                items.append(record)
            total += 1
        return {"items": items, "total": total, "advisory_only": True}

    def history(self, table, kind, ticker, limit=100):
        if table not in ("revisions", "reviews"):
            raise ValueError("Invalid history table")
        with self.connect() as db:
            rows = db.execute(
                f"SELECT document FROM {table} WHERE kind=? AND ticker=? "
                "ORDER BY rowid DESC LIMIT ?",
                (kind, ticker, limit),
            )
            return [unpack(row[0]) for row in rows]

    def backup_before_compression(self):
        """Keep one consistent pre-upgrade backup; publish only a complete copy."""
        destination = Path(self.path + ".before-compression-v1")
        if destination.exists():
            return destination
        required = Path(self.path).stat().st_size + 64 * 1024 * 1024
        if shutil.disk_usage(destination.parent).free < required:
            raise RuntimeError("Insufficient storage for catalog backup")
        temporary = Path(str(destination) + ".incomplete")
        with self.connect() as source:
            target = sqlite3.connect(temporary)
            try:
                source.backup(target, pages=256)
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("Catalog backup integrity check failed")
            finally:
                target.close()
        temporary.replace(destination)
        return destination

    def compress_page(self, batch_size=1000):
        """Re-encode old documents in bounded, atomic, resumable pages; delete nothing."""
        progress = self.state("storage:compression", {"table_index": 0, "after": 0})
        index = progress["table_index"]
        if index >= len(DOCUMENT_TABLES):
            return 0
        table = DOCUMENT_TABLES[index]
        saved = changed = 0
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                f"SELECT rowid,document FROM {table} WHERE rowid>? ORDER BY rowid LIMIT ?",
                (progress["after"], batch_size),
            ).fetchall()
            for row in rows:
                original = row["document"]
                if not isinstance(original, str):
                    continue
                packed = pack(unpack(original))
                if isinstance(packed, bytes):
                    db.execute(f"UPDATE {table} SET document=? WHERE rowid=?", (packed, row[0]))
                    saved += len(original.encode()) - len(packed)
                    changed += 1
            self.put_state(
                db,
                "storage:compression",
                {
                    "table_index": index if rows else index + 1,
                    "after": rows[-1][0] if rows else 0,
                    "complete": not rows and index + 1 == len(DOCUMENT_TABLES),
                    "documents_compressed": progress.get("documents_compressed", 0) + changed,
                    "document_bytes_saved": progress.get("document_bytes_saved", 0) + saved,
                    "last_success_at": now(),
                },
            )
        return len(rows)

    def storage(self, detail=True, previous=None):
        """Measure stored payloads and reusable pages without inflating documents.

        `detail=True` reads every stored document to sum its bytes: a full pass over the file.
        `detail=False` measures only the file, pages and volume, and carries the per-table
        figures (and when they were taken) from `previous` — run every few minutes, the full
        pass kept the whole file hot in memory."""
        with self.connect() as db:
            tables = {}
            if detail:
                for table in DOCUMENT_TABLES:
                    row = db.execute(
                        f"SELECT count(*),coalesce(sum(length(CAST(document AS BLOB))),0),"
                        f"coalesce(sum(typeof(document)='blob'),0) FROM {table}"
                    ).fetchone()
                    tables[table] = {
                        "rows": row[0], "document_bytes": row[1], "compressed": row[2]
                    }
            else:
                tables = dict((previous or {}).get("tables") or {})
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            pages = db.execute("PRAGMA page_count").fetchone()[0]
            free_pages = db.execute("PRAGMA freelist_count").fetchone()[0]
            volume = shutil.disk_usage(Path(self.path).parent)
            files = {}
            for suffix in ("", "-wal", "-shm"):
                path = Path(self.path + suffix)
                try:
                    files[suffix or "database"] = path.stat().st_size
                except FileNotFoundError:
                    files[suffix or "database"] = 0
        captured = now()
        return {
            "captured_at": captured,
            "tables_captured_at": captured if detail else (previous or {}).get(
                "tables_captured_at", (previous or {}).get("captured_at")
            ),
            "tables": tables,
            "file_bytes": files,
            "allocated_bytes": pages * page_size,
            "reusable_bytes": free_pages * page_size,
            "volume_free_bytes": volume.free,
            "volume_total_bytes": volume.total,
        }

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
            states = {
                row[0]: json.loads(row[1])
                for row in db.execute("SELECT * FROM state WHERE key NOT LIKE 'assessment_input:%'")
            }
            assessments = db.execute("SELECT count(*) FROM current_assessments").fetchone()[0]
            reviewed = db.execute(
                "SELECT count(*) FROM objects o WHERE o.kind='series' AND EXISTS "
                "(SELECT 1 FROM reviews r WHERE r.kind=o.kind AND r.ticker=o.ticker "
                "AND r.rules_hash=o.rules_hash)"
            ).fetchone()[0]
            archive = {
                "blobs": db.execute("SELECT count(*) FROM contract_blobs").fetchone()[0],
                "bytes": db.execute(
                    "SELECT coalesce(sum(length(content)),0) FROM contract_blobs"
                ).fetchone()[0],
                "observations": db.execute("SELECT count(*) FROM contract_observations").fetchone()[
                    0
                ],
                "targets_by_status": dict(
                    db.execute(
                        "SELECT status,count(*) FROM contract_targets GROUP BY status"
                    ).fetchall()
                ),
                "historical_versions_verified": False,
            }
            series_reviews = {
                "reviewed": reviewed,
                "needs_review": counts.get("series", 0) - reviewed,
            }
        return {
            "captured_at": now(),
            "objects": counts,
            "series_reviews": series_reviews,
            "evidence": evidence,
            "assessment_contexts": assessments,
            "contract_archive": archive,
            "jobs": states,
            "backfill": {
                source: {
                    "local_records": evidence.get(source, 0),
                    "initial_complete": states.get("cursor:" + source, {}).get(
                        "initial_coverage_verified", False
                    ),
                    "reconciliation_complete": states.get("cursor:" + source, {}).get(
                        "reconciliation_complete", False
                    ),
                    "coverage": states.get("source_coverage:" + source),
                }
                for source in ("paper", "live")
            },
            "consumer_cutover": False,
            "confidence_calibrated": False,
        }
