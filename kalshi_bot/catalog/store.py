"""Single-writer volume store. Revisions and assessments are immutable snapshots."""

import hashlib
import json
import os
import shutil
import sqlite3
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import dictionary, templates

# A reviews row approving one series' contract wording for every matching market.
SERIES_TEMPLATE = "series_template"
DOCUMENT_TABLES = ("objects", "revisions", "reviews", "evidence", "assessments")
COMPRESSED_PREFIX = b"catalog:zlib:1\x00"
# Reading v2 is always on. Writing it is opt-in until a deploy with this reader is live:
# an image older than the reader cannot open v2 rows, so the writer must not outrun it.
DICTIONARY_WRITES = os.environ.get("CATALOG_DICTIONARY_COMPRESSION") == "1"

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


def pack(value, minimum=1024):
    """Lossless storage encoding; content hashes always use canonical JSON. Readers decode
    any size, so a caller may lower `minimum` for documents known to compress well."""
    text = encode(value)
    raw = text.encode()
    if len(raw) >= minimum:
        compressed = COMPRESSED_PREFIX + zlib.compress(raw)
        if DICTIONARY_WRITES:
            # Keep whichever is smaller, so a document unlike the dictionary never grows.
            encoder = zlib.compressobj(zdict=dictionary.DICTIONARY)
            preset = dictionary.PREFIX + encoder.compress(raw) + encoder.flush()
            compressed = min(compressed, preset, key=len)
        if len(compressed) + 32 < len(raw):
            return compressed
    return text


def unpack(document):
    """Read the original JSON TEXT and every versioned compressed BLOB."""
    if isinstance(document, bytes):
        if document.startswith(COMPRESSED_PREFIX):
            document = zlib.decompress(document[len(COMPRESSED_PREFIX) :])
        elif document.startswith(dictionary.PREFIX):
            decoder = zlib.decompressobj(zdict=dictionary.DICTIONARY)
            document = decoder.decompress(document[len(dictionary.PREFIX) :]) + decoder.flush()
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
            CREATE TABLE IF NOT EXISTS selection_members (
              scope TEXT, ticker TEXT, reason TEXT, provider TEXT, active INTEGER NOT NULL,
              selected_at TEXT, effective_at TEXT, updated_at TEXT, document BLOB,
              PRIMARY KEY(scope,ticker,reason,provider));
            CREATE INDEX IF NOT EXISTS selection_active ON selection_members(scope,ticker,active);
            CREATE TABLE IF NOT EXISTS selection_events (
              id TEXT PRIMARY KEY, scope TEXT, ticker TEXT, recorded_at TEXT, document BLOB);
            CREATE INDEX IF NOT EXISTS selection_events_lookup
              ON selection_events(scope,ticker,recorded_at);
            CREATE TABLE IF NOT EXISTS hydration_targets (
              scope TEXT, ticker TEXT, status TEXT NOT NULL, next_check REAL NOT NULL,
              cursor TEXT, enqueued_at TEXT, attempted_at TEXT, completed_at TEXT, error TEXT,
              attempts INTEGER NOT NULL DEFAULT 0, records INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(scope,ticker));
            CREATE INDEX IF NOT EXISTS hydration_due ON hydration_targets(status,next_check);
            -- Per-market index of evidence rows. A separate table, not a column on evidence,
            -- so an older image's positional evidence INSERT still works after a rollback.
            CREATE TABLE IF NOT EXISTS evidence_markets (
              source TEXT, source_id INTEGER, market TEXT, PRIMARY KEY(source,source_id));
            CREATE INDEX IF NOT EXISTS evidence_markets_market ON evidence_markets(market);
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
        return self.upsert_revision(kind, ticker, raw, series, source)[0]

    def upsert_revision(self, kind, ticker, raw, series=None, source="kalshi_rest"):
        """`upsert`, plus whether it wrote a new semantic revision (rules, status, result or
        settlement differ from every revision already stored)."""
        from . import selection

        with self.connect() as db:
            # One write transaction: the selection check below and the revision write cannot
            # be split by a concurrent membership publish (DEC-034).
            db.execute("BEGIN IMMEDIATE")
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
            mode, _reason = selection.history_mode(
                db, kind, ticker, document["series_ticker"], raw.get("event_ticker")
            )
            if mode == "compact":
                # Inventory depth: rules, lifecycle and provenance. Current objects stay full.
                revision = selection.compact_observation(
                    document, semantic, include_rules=old.get("rules_hash") != document["rules_hash"]
                )
                key = selection.compact_key(semantic)
            else:
                revision, key = document, semantic
            revised = db.execute(
                "INSERT OR IGNORE INTO revisions VALUES (?,?,?,?,?)",
                (
                    kind,
                    ticker,
                    key,
                    stamp,
                    pack(revision, selection.COMPACT_PACK_MINIMUM if mode == "compact" else 1024),
                ),
            ).rowcount == 1
            if revised:
                selection.count_write(db, mode)
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
        return document, revised

    def get(self, kind, ticker):
        with self.connect() as db:
            row = db.execute(
                "SELECT document FROM objects WHERE kind=? AND ticker=?", (kind, ticker)
            ).fetchone()
        return self.decorate(unpack(row[0])) if row else None

    def decorate(self, doc):
        basis = "exact"
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
                basis = "market_template"
            if not review and doc["kind"] == "market" and self._has_series_template(
                db, doc.get("series_ticker")
            ):
                review = db.execute(
                    "SELECT document FROM reviews WHERE kind=? AND ticker=? AND rules_hash=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (SERIES_TEMPLATE, doc["series_ticker"], templates.template_hash(doc["raw"])),
                ).fetchone()
                basis = SERIES_TEMPLATE
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
        if facts and basis == SERIES_TEMPLATE:
            facts["semantics"] = templates.bind(facts["semantics"], doc["raw"])
        doc["review"] = facts
        doc["review_basis"] = basis if facts else None
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

    @staticmethod
    def _check_semantics(semantics, template):
        if not isinstance(semantics, dict) or any(key not in semantics for key in SEMANTIC_FIELDS):
            raise ValueError("All semantic fields must be explicit; null means unknown")
        if any(
            semantics.get(key) is None
            for key in ("resolution_mechanism", "settlement_source", "shared_exposure_key")
        ):
            raise ValueError("Resolution, settlement source and exposure identity must be known")
        if template and any(
            semantics.get(key) is not None for key in ("observation_start", "observation_end")
        ):
            raise ValueError("Reusable templates cannot carry market-specific observation times")

    @staticmethod
    def _has_series_template(db, series):
        return bool(series) and db.execute(
            "SELECT 1 FROM reviews WHERE kind=? AND ticker=? LIMIT 1", (SERIES_TEMPLATE, series)
        ).fetchone() is not None

    def review(self, payload):
        if payload.get("kind") == SERIES_TEMPLATE:
            return self.review_series_template(payload)
        required = ("kind", "ticker", "rules_hash", "actor", "rationale", "semantics")
        if any(not payload.get(key) for key in required):
            raise ValueError(
                "Review requires identity, current hash, actor, rationale and semantics"
            )
        self._check_semantics(payload["semantics"], payload.get("template"))
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
            from . import selection

            if payload["kind"] in selection.SCOPES:
                # Reviewed contexts keep detailed history; published with the review itself.
                selection.select_in(
                    db, payload["kind"], payload["ticker"], payload["kind"] + "_review",
                    "catalog", source_ref="review:" + result["id"],
                )
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

    def review_series_template(self, payload):
        """Approve a series' contract wording once, as shown in named exemplar markets.

        Each exemplar names the listing rules digest the approver read. An exemplar the catalog
        still holds with that digest binds its own template. One the catalog no longer holds
        as read (Kalshi changed the listing, or it was never captured) binds the template of
        the text that was read, if the approval records it (`template_hash`, pinned by tests to
        the read listing); otherwise it is refused. A refused exemplar never blocks the others;
        the approval is refused only if nothing binds. Every market in the series with a bound
        template then reads as reviewed; reworded markets stay unreviewed. The review id
        excludes the write time, so re-applying the same approval is a no-op."""
        required = ("ticker", "exemplars", "actor", "approved_by", "rationale", "semantics")
        if any(not payload.get(key) for key in required):
            raise ValueError(
                "Series template approval requires series, exemplars, actor, approver, "
                "rationale and semantics"
            )
        series = payload["ticker"]
        exemplars = payload["exemplars"]
        if not isinstance(exemplars, list) or not all(
            isinstance(e, dict) and e.get("market") and e.get("rules_hash") for e in exemplars
        ):
            raise ValueError("Each exemplar needs a market ticker and the rules digest that was read")
        self._check_semantics(payload["semantics"], template=True)
        key = payload["semantics"]["shared_exposure_key"]
        try:
            key.format(event_ticker="E", event_suffix="S")
        except (AttributeError, KeyError, IndexError, ValueError) as error:
            raise ValueError("Exposure key may use only {event_ticker} or {event_suffix}") from error
        from . import selection

        bound, refused = [], []
        for exemplar in exemplars:
            read = exemplar.get("template_hash")
            current = self.get("market", exemplar["market"])
            if current and current.get("series_ticker") != series:
                raise LookupError(f"Exemplar {exemplar['market']} is not a {series} market")
            # The listing's own rules digest (before parent binding), as read by the approver.
            if current and rule_hash(current["raw"]) == exemplar["rules_hash"]:
                held = templates.template_hash(current["raw"])
                if read and read != held:
                    raise ValueError(
                        f"Exemplar {exemplar['market']} template_hash is stale for "
                        f"{templates.METHOD}; recompute it from the read listing"
                    )
                bound.append((exemplar["market"], held,
                              templates.template_document(current["raw"])))
            elif read:
                bound.append((exemplar["market"], read, None))
                refused.append({"market": exemplar["market"], "bound_from": "read_template",
                                "reason": "listing changed since read" if current
                                else "listing not held"})
            else:
                refused.append({"market": exemplar["market"],
                                "reason": "listing changed since read" if current
                                else "listing not held"})
        if not bound:
            raise LookupError(f"No exemplar of {series} binds: {refused}")
        results = []
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            documents = {}
            for _market, template_hash, document in bound:
                if documents.get(template_hash) is None:
                    documents[template_hash] = document
            for template_hash, document in documents.items():
                result = {
                    **payload,
                    "kind": SERIES_TEMPLATE,
                    "template_method": templates.METHOD,
                    "rules_hash": template_hash,
                    "template": True,
                    "bound_exemplars": [m for m, h, _doc in bound if h == template_hash],
                    # The approved wording as matched, so a different version can be compared.
                    "template_document": document,
                }
                result["id"] = digest(result)
                result["created_at"] = payload.get("approved_at") or now()
                if db.execute("SELECT 1 FROM reviews WHERE id=?", (result["id"],)).fetchone():
                    results.append({**result, "already_recorded": True})
                    continue
                selection.select_in(
                    db, "series", series, "series_review", "catalog",
                    source_ref="review:" + result["id"],
                )
                db.execute(
                    "INSERT INTO reviews VALUES (?,?,?,?,?,?,?)",
                    (result["id"], SERIES_TEMPLATE, series, template_hash, True,
                     result["created_at"], pack(result)),
                )
                results.append(result)
        return {"kind": SERIES_TEMPLATE, "ticker": series, "templates": results,
                "exemplars_not_held_as_read": refused}

    def series_template_reviews(self, series, full=False):
        """Approvals bound under the current template method; older methods' rows stay
        stored but no longer match any market."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT document FROM reviews WHERE kind=? AND ticker=? ORDER BY created_at",
                (SERIES_TEMPLATE, series),
            ).fetchall()
        docs = [unpack(row[0]) for row in rows]
        docs = [doc for doc in docs if doc.get("template_method") == templates.METHOD]
        if full:
            return docs
        return [
            {
                key: doc.get(key)
                for key in ("id", "approval_id", "rules_hash", "created_at", "bound_exemplars")
            }
            for doc in docs
        ]

    def template_coverage(self, series, source="live"):
        """How many traded markets of a series an approved template covers, and which
        wording versions are still unapproved (with sample markets to read)."""
        with self.connect() as db:
            tickers = [
                row[0]
                for row in db.execute(
                    "SELECT DISTINCT m.market FROM evidence_markets m JOIN evidence e "
                    "ON e.source=m.source AND e.source_id=m.source_id "
                    "WHERE m.source=? AND e.series=? ORDER BY m.market",
                    (source, series),
                )
            ]
        covered, by_basis, uncovered, missing, parent_changed = 0, {}, {}, [], []
        approved = [
            doc["template_document"]
            for doc in self.series_template_reviews(series, full=True)
            if doc.get("template_document")
        ]
        for ticker in tickers:
            document = self.get("market", ticker)
            if document is None:
                missing.append(ticker)
                continue
            if document["review_status"] == "reviewed":
                covered += 1
                by_basis[document["review_basis"]] = by_basis.get(document["review_basis"], 0) + 1
                continue
            if document["parent_rules_changed"]:
                # Not a wording question: the event or series changed after this listing was
                # captured, which invalidates any review until the listing is refreshed.
                parent_changed.append(ticker)
                continue
            group = uncovered.setdefault(
                templates.template_hash(document["raw"]),
                {"markets": 0, "samples": [],
                 "differs_from_approved": templates.differences(
                     templates.template_document(document["raw"]), approved)},
            )
            group["markets"] += 1
            if len(group["samples"]) < 3:
                group["samples"].append(ticker)
        return {
            "series": series,
            "evidence_source": source,
            "traded_markets": len(tickers),
            "reviewed": covered,
            "reviewed_by_basis": by_basis,
            "listing_missing": len(missing),
            "listing_missing_samples": missing[:3],
            "parent_rules_changed": len(parent_changed),
            "parent_rules_changed_samples": parent_changed[:3],
            "unreviewed_templates": [
                {"template_hash": h, **g}
                for h, g in sorted(uncovered.items(), key=lambda item: -item[1]["markets"])
            ],
            "template_method": templates.METHOD,
            "review_is_not_qualification": True,
        }

    def evidence_page(self, source, records, cursor, coverage=None):
        from . import selection

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
                db.execute(
                    "INSERT OR REPLACE INTO evidence_markets VALUES (?,?,?)",
                    (source, record["id"], record["market_ticker"]),
                )
                # Membership is published in the same transaction as the evidence, so no
                # upsert can see the evidence without also seeing the selection (DEC-034).
                selection.select_in(
                    db, "market", record["market_ticker"], source + "_evidence", "catalog",
                    source_ref=f"{source}:{record['id']}",
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

    def evidence_by_series(self):
        """Yield (series, records) one series at a time; peak memory is the largest series,
        not the whole evidence table. Each read is its own short connection."""
        with self.connect() as db:
            names = [row[0] for row in db.execute("SELECT DISTINCT series FROM evidence")]
        # Every stored row has a series (its ticker prefix), so equality covers the table.
        for series in sorted(names):
            with self.connect() as db:
                rows = db.execute(
                    "SELECT source,document FROM evidence WHERE series=? ORDER BY rowid",
                    (series,),
                ).fetchall()
            yield series, [(row[0], unpack(row[1])) for row in rows]

    def iter_evidence(self, source, series, page=2000):
        """Stream one source's evidence documents for a series in short read pages."""
        after = 0
        while True:
            with self.connect() as db:
                rows = db.execute(
                    "SELECT rowid,document FROM evidence WHERE series=? AND source=? AND rowid>? "
                    "ORDER BY rowid LIMIT ?",
                    (series, source, after, page),
                ).fetchall()
            if not rows:
                return
            for row in rows:
                yield unpack(row[1])
            after = rows[-1][0]

    def migrate_review(self, document):
        from . import selection

        migration_id = digest(document)
        with self.connect() as db:
            # Registry provenance keeps its series on detailed history (DEC-034).
            selection.select_in(
                db, "series", document["series_ticker"], "legacy_registry", "catalog",
                source_ref="review_migration:" + migration_id,
            )
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
        """Keep one consistent pre-upgrade backup; publish only a complete copy. None once
        retention has retired it (DEC-038): compression is complete and it is not rebuilt."""
        destination = Path(self.path + ".before-compression-v1")
        if destination.exists():
            return destination
        if self.state("retention:backup_retired"):
            return None
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
            # Bytes on disk per table AND index, from SQLite's own page accounting. The per-table
            # document sums above cover five tables; the file is larger than they are, and this
            # is what says where the rest is. Same full pass, so only in `detail`.
            objects_bytes = (previous or {}).get("objects_bytes")
            if detail:
                try:
                    objects_bytes = {
                        row[0]: row[1]
                        for row in db.execute(
                            # aggregate=TRUE: one row per b-tree, without per-page cell
                            # parsing. The per-page form stalled for 30+ minutes on 15 GB.
                            "SELECT name,sum(pgsize) FROM dbstat WHERE aggregate=TRUE "
                            "GROUP BY name ORDER BY 2 DESC"
                        )
                    }
                except sqlite3.OperationalError:  # SQLite built without DBSTAT_VTAB
                    objects_bytes = None
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
            "objects_bytes": objects_bytes,
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
            from .selection import status_in

            history_policy = status_in(db)
        return {
            "captured_at": now(),
            "objects": counts,
            "series_reviews": series_reviews,
            "evidence": evidence,
            "assessment_contexts": assessments,
            "contract_archive": archive,
            "history_policy": history_policy,
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
