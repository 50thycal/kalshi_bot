"""Read-only access to the market-catalog service for the ops runner and its scripts.

    {"type": "catalog", "path": "/v1/status", "id": "cat-status-1"}
    {"type": "catalog", "path": "/v1/markets", "params": {"series": "KXRAIN", "limit": 50},
     "id": "cat-rain-1"}

The catalog lives on its own Railway volume (SQLite), not in Postgres, so the ops channel's
read-only database URL cannot reach it. This client calls its public HTTPS API with
`CATALOG_READ_TOKEN`, a GET-only credential: the catalog refuses it on every POST (403), so the
channel gains no writable path. The full `CATALOG_API_TOKEN` never reaches GitHub Actions.

Results are published on a public branch, so nothing secret is ever printed: not the token,
not response headers, not exception text. Catalog data itself is public market structure plus
the bot's own descriptive evidence, the same class of data `db` reads already publish.

Scripts reuse `get()` directly (for example the same-game correlation study).

Stdlib only: the ops runner's default path installs nothing but psycopg.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "https://market-catalog-production-6beb.up.railway.app"
URL_ENV = "CATALOG_URL"
TOKEN_ENV = "CATALOG_READ_TOKEN"
TIMEOUT_SECONDS = 60
MAX_RESPONSE_BYTES = 8_000_000
MAX_PRINT_CHARS = 400_000
PATH_PATTERN = re.compile(r"^/v1/[A-Za-z0-9/_.-]{1,200}$")
PARAM_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,40}$")
ALLOWED_KEYS = {"type", "id", "path", "params", "actor", "purpose", "workstream", "issue"}


class CatalogReadError(RuntimeError):
    """A failed read, carrying only a stable code (never response text or the token)."""

    def __init__(self, code: str, status: int | None = None):
        super().__init__(code if status is None else f"{code} (HTTP {status})")
        self.code = code
        self.status = status


def configured() -> bool:
    return bool((os.environ.get(TOKEN_ENV) or "").strip())


def base_url() -> str:
    url = (os.environ.get(URL_ENV) or "").strip() or DEFAULT_URL
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.netloc or parts.query or parts.fragment:
        raise CatalogReadError("invalid_configuration")
    return url.rstrip("/")


def build_url(path: str, params: dict | None = None) -> str:
    """The full GET URL; refuses anything outside /v1/ or with odd parameter names."""
    if not isinstance(path, str) or not PATH_PATTERN.match(path) or ".." in path:
        raise CatalogReadError("invalid_path")
    query = {}
    for key, value in (params or {}).items():
        if not PARAM_PATTERN.match(str(key)) or isinstance(value, (dict, list)):
            raise CatalogReadError("invalid_params")
        query[str(key)] = str(value)
    url = base_url() + path
    return url + ("?" + urllib.parse.urlencode(query) if query else "")


def get(path: str, params: dict | None = None) -> dict:
    """One authenticated GET; returns the decoded JSON body."""
    token = (os.environ.get(TOKEN_ENV) or "").strip()
    if not token:
        raise CatalogReadError("not_configured")
    req = urllib.request.Request(build_url(path, params), method="GET", headers={
        "Authorization": "Bearer " + token, "Accept": "application/json",
        "User-Agent": "kalshi-bot-ops-catalog-read/1"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310 (fixed https host)
            body = resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        code = {401: "authentication_refused", 403: "authentication_refused",
                404: "not_found", 400: "request_rejected"}.get(exc.code, "service_error")
        raise CatalogReadError(code, exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise CatalogReadError("unreachable") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise CatalogReadError("response_too_large")
    try:
        return json.loads(body)
    except ValueError:
        raise CatalogReadError("invalid_response") from None


def run(req: dict) -> int:
    """Serve one `{"type":"catalog"}` ops request. Exit 0 on a completed read, 1 otherwise."""
    unknown = set(req) - ALLOWED_KEYS
    if unknown:
        print(f"catalog: refused, unknown request keys {sorted(unknown)}")
        return 1
    path = req.get("path") or "/v1/status"
    params = req.get("params") or {}
    if not isinstance(params, dict):
        print("catalog: refused, params must be an object")
        return 1
    try:
        data = get(path, params)
    except CatalogReadError as exc:
        hint = {"not_configured": f"the {TOKEN_ENV} secret is not available to the ops runner "
                                  "(docs/OPS_RUNBOOK.md, catalog access)",
                "authentication_refused": f"the catalog refused {TOKEN_ENV}; it must match the "
                                          "market-catalog service variable of the same name"}
        print(f"catalog: FAILED {exc}" + (f" — {hint[exc.code]}" if exc.code in hint else ""))
        return 1
    text = json.dumps(data, indent=1, sort_keys=True, default=str)
    if len(text) > MAX_PRINT_CHARS:
        print(text[:MAX_PRINT_CHARS])
        print(f"\n# truncated: {len(text)} chars, printed {MAX_PRINT_CHARS}; narrow with params")
    else:
        print(text)
    return 0
