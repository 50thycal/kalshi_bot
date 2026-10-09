"""Bounded official PDF archive. Capture is not proof of effective historical terms."""

import hashlib
import math
import re
import shutil
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from .store import digest, now, pack, unpack

METHOD = "official-document-capture-v1"
MAX_BYTES = 2 * 1024 * 1024
MIN_FREE_BYTES = 256 * 1024 * 1024
REFRESH_SECONDS = 86400
RETRY_SECONDS = 3600
FIELDS = ("contract_url", "contract_terms_url")
URL_PATTERN = re.compile(
    r"https://assets\.kalshi\.com/(?:contract_terms|regulatory/product-certifications)/"
    r"[A-Za-z0-9_-][A-Za-z0-9_.-]*\.pdf\Z"
)


class DocumentCaptureError(Exception):
    pass


def allowed_url(url):
    return isinstance(url, str) and len(url) <= 512 and bool(URL_PATTERN.fullmatch(url))


def references(store, document):
    """Only metadata is exposed. GET never fetches, approves or returns file bytes."""
    items = []
    with store.connect() as db:
        for field in FIELDS:
            url = document.get("raw", {}).get(field)
            if not url:
                continue
            target = (
                db.execute("SELECT * FROM contract_targets WHERE url=?", (url,)).fetchone()
                if allowed_url(url)
                else None
            )
            observation = (
                db.execute(
                    "SELECT document FROM contract_observations WHERE id=?",
                    (target["capture_id"],),
                ).fetchone()
                if target and target["capture_id"]
                else None
            )
            capture = unpack(observation[0]) if observation else None
            items.append(
                {
                    "field": field,
                    "url": url,
                    "status": target["status"]
                    if target
                    else ("not_captured" if allowed_url(url) else "unsupported_url"),
                    "content_sha256": (capture or {}).get("content_sha256"),
                    "capture": capture,
                    "last_attempt_at": target["attempted_at"] if target else None,
                    "error": target["error"] if target else None,
                    "next_check_at_unix": target["next_check"] if target else None,
                    "captured_bytes_available": capture is not None,
                    "current_capture_fresh": bool(
                        target
                        and target["status"] == "captured"
                        and target["next_check"] > time.time()
                    ),
                    "listing_rules_hash": document.get("rules_hash"),
                    "version_at_execution_verified": False,
                    "contract_document_binding_verified": False,
                }
            )
    return items


def history(store, url=None, limit=100, offset=0):
    """Paginated immutable observation metadata; archived bytes stay internal."""
    where, args = ("WHERE url=?", [url]) if url else ("", [])
    with store.connect() as db:
        total = db.execute("SELECT count(*) FROM contract_observations " + where, args).fetchone()[
            0
        ]
        rows = db.execute(
            "SELECT document FROM contract_observations "
            + where
            + " ORDER BY retrieved_at DESC,id DESC LIMIT ? OFFSET ?",
            [*args, limit, offset],
        )
        items = [unpack(row[0]) for row in rows]
    return {
        "items": items,
        "total": total,
        "method_version": METHOD,
        "historical_versions_verified": False,
        "advisory_only": True,
    }


class ContractCapture:
    def __init__(self, store, client=None):
        self.store = store
        self.client = client or httpx.Client(timeout=20, follow_redirects=False)

    def enqueue_page(self):
        """Prioritize imported live series; also resume a keyset scan of all series."""
        state = self.store.state("documents:scan", {"after": "", "restart_at": 0})
        stamp = time.time()
        with self.store.connect() as db:
            priority = db.execute(
                "SELECT o.document FROM objects o WHERE o.kind='series' AND o.ticker IN "
                "(SELECT DISTINCT series FROM evidence WHERE source='live') AND o.ticker>? "
                "ORDER BY o.ticker LIMIT 50",
                (self.store._state_in(db, "documents:priority_after") or "",),
            )
            documents = [unpack(row[0]) for row in priority]
            self.store.put_state(
                db, "documents:priority_after", documents[-1]["ticker"] if documents else ""
            )
            rows = []
            if state.get("restart_at", 0) <= stamp:
                rows = db.execute(
                    "SELECT ticker,document FROM objects WHERE kind='series' AND ticker>? "
                    "ORDER BY ticker LIMIT 100",
                    (state.get("after", ""),),
                ).fetchall()
                documents.extend(unpack(row[1]) for row in rows)
                self.store.put_state(
                    db,
                    "documents:scan",
                    {
                        "after": rows[-1][0] if rows else "",
                        "restart_at": 0 if rows else stamp + REFRESH_SECONDS,
                    },
                )
            for document in documents:
                for field in FIELDS:
                    url = document.get("raw", {}).get(field)
                    if allowed_url(url):
                        db.execute(
                            "INSERT OR IGNORE INTO contract_targets "
                            "(url,next_check,status) VALUES (?,0,'not_captured')",
                            (url,),
                        )

    def check_space(self):
        if shutil.disk_usage(Path(self.store.path).parent).free < MIN_FREE_BYTES + MAX_BYTES:
            raise DocumentCaptureError("storage_reserve")

    def fetch(self, url):
        if not allowed_url(url):
            raise DocumentCaptureError("unsupported_url")
        self.check_space()
        started = time.monotonic()
        with self.client.stream(
            "GET",
            url,
            follow_redirects=False,
            timeout=20,
            headers={"Accept-Encoding": "identity"},
        ) as response:
            if response.status_code == 429 or response.status_code >= 500:
                # Shared provider cooldown survives restart. Metadata is never effective-date proof.
                try:
                    delay = float(response.headers.get("Retry-After", RETRY_SECONDS))
                except ValueError:
                    try:
                        date = parsedate_to_datetime(response.headers.get("Retry-After", ""))
                        if date.tzinfo is None:
                            date = date.replace(tzinfo=timezone.utc)
                        delay = date.timestamp() - time.time()
                    except (TypeError, ValueError, OverflowError):
                        delay = RETRY_SECONDS
                delay = max(RETRY_SECONDS, delay) if math.isfinite(delay) else RETRY_SECONDS
                self.store.set_state("documents:backoff", time.time() + delay)
            if response.status_code != 200:
                raise DocumentCaptureError("http_" + str(response.status_code))
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise DocumentCaptureError("encoded_body")
            length = response.headers.get("Content-Length")
            if length and (not length.isdigit() or int(length) > MAX_BYTES):
                raise DocumentCaptureError("body_size")
            body = bytearray()
            for chunk in response.iter_bytes(chunk_size=4096):
                if len(body) + len(chunk) > MAX_BYTES:
                    raise DocumentCaptureError("body_size")
                if time.monotonic() - started > 20:
                    raise DocumentCaptureError("fetch_deadline")
                body.extend(chunk)
            if not body.startswith(b"%PDF-"):
                raise DocumentCaptureError("not_pdf")
            metadata = {
                key: response.headers.get(key, "")[:512]
                for key in ("content-type", "etag", "last-modified")
            }
        self.check_space()
        return bytes(body), metadata

    def save(self, url, body, metadata):
        stamp = now()
        content_hash = hashlib.sha256(body).hexdigest()
        observation = {
            "method_version": METHOD,
            "url": url,
            "content_sha256": content_hash,
            "byte_count": len(body),
            "retrieved_at": stamp,
            "response_metadata": metadata,
            "historical_effective_at": None,
            "version_at_execution_verified": False,
            "metadata_is_effective_date_proof": False,
        }
        capture_id = digest(observation)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT content FROM contract_blobs WHERE sha256=?", (content_hash,)
            ).fetchone()
            if existing and bytes(existing[0]) != body:
                raise DocumentCaptureError("archive_integrity")
            db.execute("INSERT OR IGNORE INTO contract_blobs VALUES (?,?)", (content_hash, body))
            db.execute(
                "INSERT OR IGNORE INTO contract_observations VALUES (?,?,?,?,?)",
                (capture_id, url, content_hash, stamp, pack({"id": capture_id, **observation})),
            )
            db.execute(
                "UPDATE contract_targets SET next_check=?,status='captured',capture_id=?,"
                "attempted_at=?,error=NULL WHERE url=?",
                (time.time() + REFRESH_SECONDS, capture_id, stamp, url),
            )
        return capture_id

    def step(self):
        self.enqueue_page()
        if self.store.state("documents:backoff", 0) > time.time():
            return 0
        with self.store.connect() as db:
            target = db.execute(
                "SELECT url FROM contract_targets WHERE next_check<=? "
                "ORDER BY next_check,url LIMIT 1",
                (time.time(),),
            ).fetchone()
        if not target:
            return 0
        url = target[0]
        try:
            body, metadata = self.fetch(url)
            self.save(url, body, metadata)
        except Exception as error:
            reason = str(error) if isinstance(error, DocumentCaptureError) else type(error).__name__
            with self.store.connect() as db:
                db.execute(
                    "UPDATE contract_targets SET next_check=?,status='refresh_failed',"
                    "attempted_at=?,error=? WHERE url=?",
                    (time.time() + RETRY_SECONDS, now(), reason, url),
                )
            raise DocumentCaptureError(reason) from None
        return 1
