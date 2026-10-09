"""Run only the market catalog API and collectors: python -m kalshi_bot.catalog.service."""

import ctypes
import gc
import hmac
import json
import logging
import os
import signal
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from .evaluators import refresh
from .ingest import Discovery, DiscoveryDeferred, reset_source_reconciliation, seed, source_page
from .store import Store, now, unpack

LOG = logging.getLogger("market_catalog")


def run_job(store, key, action):
    try:
        count = action()
        store.set_state(
            "job:" + key,
            {
                **store.state("job:" + key, {}),
                "last_success_at": now(),
                "records": count,
                "error": None,
                "http_status": None,
                "retry_at_unix": None,
            },
        )
        LOG.info("job=%s records=%s status=ok", key, count)
        return count
    except DiscoveryDeferred as error:
        store.set_state(
            "job:" + key,
            {
                **store.state("job:" + key, {}),
                "error": "DiscoveryDeferred",
                "retry_at_unix": error.retry_at,
            },
        )
        LOG.info("job=%s status=deferred retry_at_unix=%s", key, error.retry_at)
        return None
    except Exception as error:
        # Exception messages can contain connection strings; emit the type only.
        code = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        store.set_state(
            "job:" + key,
            {
                **store.state("job:" + key, {}),
                "last_error_at": now(),
                "error": type(error).__name__,
                "http_status": code,
            },
        )
        LOG.error("job=%s status=error type=%s http_status=%s", key, type(error).__name__, code)
        return None


def release_memory():
    """Hand freed heap back to the OS after a large pass. The evaluation materialises every
    evidence row; without this the process keeps that peak resident, and billed, until restart."""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):  # not glibc: nothing to trim
        pass


def collect(store, stopped, source_url, interval):
    discovery = Discovery(store)
    last_evaluation = 0
    last_storage_check = 0
    evaluation_every = max(60, int(os.environ.get("CATALOG_EVALUATION_SECONDS", "300")))
    storage_detail_every = max(
        300, int(os.environ.get("CATALOG_STORAGE_DETAIL_SECONDS", "86400"))
    )
    last_reconciliation = store.state("last_reconciliation", 0)
    while not stopped.is_set():
        stamp = time.time()
        if stamp - last_reconciliation >= 86400:
            discovery.reset()
            reset_source_reconciliation(store)
            last_reconciliation = stamp
            store.set_state("last_reconciliation", stamp)
        for job in ("series", "events"):
            run_job(store, "discovery:" + job, lambda job=job: discovery.page(job))
        run_job(store, "discovery:updates", discovery.updates)
        changed = False
        if source_url:
            # Only rows whose content differs count as new evidence (Store.evidence_page).
            evidence_changes = store.state("evidence:changes", 0)
            for source in ("paper", "live"):
                run_job(
                    store,
                    "import:" + source,
                    lambda source=source: source_page(store, source_url, source),
                )
            changed = store.state("evidence:changes", 0) != evidence_changes
        outcomes = run_job(store, "discovery:live_outcomes", discovery.live_outcomes)
        changed = changed or bool(outcomes)
        requested = store.state("evaluation_requested")
        if changed or requested or stamp - last_evaluation >= evaluation_every:
            result = run_job(store, "evaluation", lambda: refresh(store))
            release_memory()
            if result is not None and store.state("evaluation_requested") == requested:
                store.set_state("evaluation_requested", None)
            last_evaluation = stamp
        if stamp - last_storage_check >= 300:

            def report_storage():
                previous = store.state("storage:metrics")
                taken = (previous or {}).get("tables_captured_at")
                detail = (
                    not taken
                    or (
                        datetime.now(timezone.utc) - datetime.fromisoformat(taken)
                    ).total_seconds()
                    >= storage_detail_every
                )
                storage = store.storage(detail=detail, previous=previous)
                store.set_state("storage:metrics", storage)
                LOG.info("catalog_storage=%s", json.dumps(storage))
                return 0

            run_job(store, "storage:metrics", report_storage)
            last_storage_check = stamp
        # Limit maintenance time and commit each page so interruption is safe.
        maintenance_deadline = time.monotonic() + 3
        while not stopped.is_set() and time.monotonic() < maintenance_deadline:
            if store.state("storage:compression", {}).get("complete"):
                break
            if run_job(store, "storage:compression", store.compress_page) is None:
                break
        LOG.info(
            "catalog_counts=%s",
            json.dumps(
                {
                    key: value
                    for key, value in store.status().items()
                    if key in ("objects", "evidence", "assessment_contexts")
                }
            ),
        )
        stopped.wait(interval)
    discovery.client.close()


def make_server(store, token, address=("::", 8080)):
    if len(token) < 24:
        raise ValueError("CATALOG_API_TOKEN must have at least 24 characters")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # URLs can contain query data; structured job logs are sufficient.

        def respond(self, code, value):
            body = json.dumps(value, default=str, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            if hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                return True
            self.respond(401, {"error": "Bearer token required"})
            return False

        def do_GET(self):
            parsed = urlsplit(self.path)
            path = parsed.path.rstrip("/")
            assets = {
                "": ("index.html", "text/html; charset=utf-8"),
                "/dashboard": ("index.html", "text/html; charset=utf-8"),
                "/dashboard/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/dashboard/style.css": ("style.css", "text/css; charset=utf-8"),
            }
            if path in assets:
                name, content_type = assets[path]
                body = (Path(__file__).parent / "dashboard" / name).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/health":
                self.respond(
                    200, {"service": "market-catalog", "status": "ok", "schema_version": 1}
                )
                return
            if not self.authorized():
                return
            query = parse_qs(parsed.query)
            try:
                limit = min(500, max(1, int(query.get("limit", ["100"])[0])))
                offset = max(0, int(query.get("offset", ["0"])[0]))
                series = query.get("series", [None])[0]
                if path == "/v1/status":
                    result = store.status()
                elif path in ("/v1/review-packets", "/v1/calibration-inputs"):
                    from .readiness import calibration_inputs, review_packets

                    action = review_packets if path == "/v1/review-packets" else calibration_inputs
                    result = action(
                        store,
                        strategy=query.get("strategy", ["mmsell"])[0],
                        series=series,
                        limit=min(50, limit),
                        offset=offset,
                    )
                elif path in ("/v1/review-migrations", "/v1/live-economics"):
                    result = store.pipeline_items(path.split("/")[-1], limit, offset, series)
                elif path == "/v1/scoring-requirements":
                    from .economics import SCORING_REQUIREMENTS

                    result = {"strategies": SCORING_REQUIREMENTS, "advisory_only": True}
                elif path in ("/v1/assessments", "/v1/select"):
                    settlement_type = query.get("settlement_type", [None])[0]
                    source = query.get("evidence_source", [None])[0]
                    minimum = (
                        float(query["min_confidence"][0]) if "min_confidence" in query else None
                    )
                    if source and source not in ("paper", "live"):
                        raise ValueError("Invalid evidence source")
                    if minimum is not None and not 0 <= minimum <= 100:
                        raise ValueError("Confidence must be in 0–100")
                    result = store.assessment_page(
                        strategy=query.get("strategy", [None])[0],
                        series=series,
                        qualified=query.get(
                            "qualified", ["true" if path == "/v1/select" else "false"]
                        )[0]
                        == "true",
                        min_edge=float(query["min_edge"][0]) if "min_edge" in query else None,
                        source=source,
                        minimum=minimum,
                        settlement_type=settlement_type,
                        limit=limit,
                        offset=offset,
                    )
                elif path.startswith("/v1/assessments/"):
                    with store.connect() as db:
                        row = db.execute(
                            "SELECT document FROM assessments WHERE id=?", (path.split("/")[-1],)
                        ).fetchone()
                    result = unpack(row[0]) if row else None
                elif path in ("/v1/series", "/v1/events", "/v1/markets", "/v1/review-queue"):
                    kind = {
                        "/v1/series": "series",
                        "/v1/events": "event",
                        "/v1/markets": "market",
                    }.get(path, query.get("kind", ["market"])[0])
                    if kind not in ("series", "event", "market"):
                        raise ValueError("Invalid object kind")
                    result = {
                        "items": store.list_objects(
                            kind, limit, offset, series, path == "/v1/review-queue"
                        )
                    }
                elif path.startswith("/v1/objects/"):
                    parts = path.split("/")
                    if len(parts) == 5:
                        result = store.get(parts[3], parts[4])
                    elif len(parts) == 6 and parts[5] in ("revisions", "reviews"):
                        result = {"items": store.history(parts[5], parts[3], parts[4], limit)}
                    else:
                        result = None
                else:
                    result = None
                self.respond(200 if result is not None else 404, result or {"error": "Not found"})
            except (ValueError, TypeError):
                self.respond(400, {"error": "Invalid query parameters"})

        def do_POST(self):
            if not self.authorized():
                return
            if self.path not in ("/v1/reviews", "/v1/import"):
                self.respond(404, {"error": "Not found"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= (1048576 if self.path == "/v1/import" else 65536):
                    raise ValueError("Review body must be 1–65536 bytes")
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError("Review must be an object")
                if self.path == "/v1/import":
                    source = payload.get("source")
                    records = payload.get("records")
                    provenance = payload.get("provenance")
                    if (
                        source not in ("paper", "live")
                        or not isinstance(records, list)
                        or not provenance
                    ):
                        raise ValueError(
                            "Import requires paper/live source, records and provenance"
                        )
                    if len(records) > 1000:
                        raise ValueError("Import page limit is 1000 records")
                    for row in records:
                        if (
                            not isinstance(row, dict)
                            or not isinstance(row.get("id"), int)
                            or row["id"] <= 0
                            or not row.get("market_ticker")
                        ):
                            raise ValueError(
                                "Every record requires a positive source ID and market ticker"
                            )
                    store.evidence_page(
                        source,
                        [{**r, "import_provenance": provenance} for r in records],
                        store.state("cursor:" + source, {"after": 0, "initial_complete": False}),
                    )
                    store.set_state(
                        "manual_import:" + source,
                        {
                            "last_success_at": now(),
                            "records": len(records),
                            "provenance": provenance,
                        },
                    )
                    store.set_state("evaluation_requested", now())
                    self.respond(201, {"imported": len(records), "source": source})
                else:
                    self.respond(201, store.review(payload))
            except LookupError as error:
                self.respond(409, {"error": str(error)})
            except (ValueError, TypeError) as error:
                self.respond(400, {"error": str(error)})

    if address[0] == "::":
        import socket

        class Server(ThreadingHTTPServer):
            address_family = socket.AF_INET6

        return Server(address, Handler)
    return ThreadingHTTPServer(address, Handler)


def main():
    import sys

    logging.basicConfig(
        stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(name)s %(message)s"
    )
    store = Store(os.environ.get("CATALOG_DB_PATH", "/data/catalog.sqlite3"))
    token = os.environ.get("CATALOG_API_TOKEN", "")
    interval = max(10, int(os.environ.get("CATALOG_INTERVAL_SECONDS", "30")))
    server = make_server(store, token, ("::", int(os.environ.get("PORT", "8080"))))
    LOG.info("catalog_backup status=starting")
    backup = store.backup_before_compression()
    LOG.info("catalog_backup status=ready bytes=%s", backup.stat().st_size)
    seed(store)
    stopped = threading.Event()
    thread = threading.Thread(
        target=collect,
        args=(store, stopped, os.environ.get("CATALOG_SOURCE_DATABASE_URL"), interval),
        daemon=True,
    )
    thread.start()

    def stop(_signum, _frame):
        stopped.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    LOG.info(
        "catalog_api_ready source_import=%s", bool(os.environ.get("CATALOG_SOURCE_DATABASE_URL"))
    )
    try:
        server.serve_forever()
    finally:
        stopped.set()
        thread.join(timeout=45)
        server.server_close()


if __name__ == "__main__":
    main()
