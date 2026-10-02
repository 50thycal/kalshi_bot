"""Open public-internet evidence capture for the desks (DEC-024).

Any public HTTPS host may be captured. The safety boundary protects the service, not the
desk's choice of source: every hop resolves DNS, refuses non-global addresses (private,
loopback, link-local, CGNAT, metadata, multicast, reserved and their IPv6/embedded-IPv4
forms) and connects to the exact address it checked, so a rebinding resolver cannot swap
in a private target between check and connect. No credentials, cookies, proxies or
environment configuration are ever sent. Size, time, redirect and content-type limits
apply. Retrieved content is untrusted data; provenance (URL, time, sha256 of the stored
text) is what decision verification checks.
"""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from .contracts import DeskError

MAX_SOURCE_BYTES = 3_000_000       # decoded body bytes read from the network
MAX_SOURCE_CHARS = 1_000_000       # stored text; long pages and JSON lists stay whole
MAX_REDIRECTS = 5
MAX_URL_LENGTH = 2048
FETCH_DEADLINE_SECONDS = 45
KALSHI_HOSTS = frozenset({"api.elections.kalshi.com", "external-api.kalshi.com"})
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".intranet", ".lan", ".home.arpa",
                     ".corp", ".private")
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
_HTML_TYPES = ("text/html", "application/xhtml+xml")
_TEXT_TYPES = ("text/", "application/json", "application/xml", "application/rss+xml",
               "application/atom+xml", "application/javascript", "application/csv",
               "application/x-ndjson", "application/geo+json")


def address_allowed(value) -> bool:
    """True only for a globally routable unicast address, including embedded IPv4 forms."""
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return address_allowed(ip.ipv4_mapped)
        if ip.sixtofour is not None or ip.teredo is not None or any(ip in net for net in _NAT64):
            return False
        if ip.scope_id:
            return False
    return bool(ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified
                                      or ip.is_loopback or ip.is_link_local or ip.is_private))


def system_resolver(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        raise DeskError("source_dns_failure") from None
    return list(dict.fromkeys(info[4][0] for info in infos))


def check_url(url: str) -> tuple[str, str]:
    """Return (normalized url without fragment, lowercase hostname) or refuse."""
    if not isinstance(url, str) or not 8 <= len(url) <= MAX_URL_LENGTH or any(c.isspace() for c in url):
        raise DeskError("source_url_invalid")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise DeskError("source_url_invalid") from None
    host = (parts.hostname or "").rstrip(".").lower()
    if parts.scheme != "https" or port not in (None, 443) or parts.username or parts.password:
        raise DeskError("source_url_refused")
    if not host or "%" in host:
        raise DeskError("source_url_invalid")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not address_allowed(literal):
            raise DeskError("source_address_refused")
    elif "." not in host or host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
        raise DeskError("source_address_refused")
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, "")), host


class PinnedTransport(httpx.BaseTransport):
    """Resolve, refuse any non-global answer, then connect to that exact address.

    TLS still verifies the certificate against the original hostname via SNI, and the
    Host header carries the original name, so virtual hosting works normally.
    """

    def __init__(self, *, resolver=None, inner: httpx.BaseTransport | None = None):
        self.resolver = resolver or system_resolver
        # A fresh connection pool per hop: pooled connections are keyed by address, and a
        # connection opened with one hostname's SNI must never carry another host's request.
        self.inner = inner or httpx.HTTPTransport(retries=0, trust_env=False)

    def close(self):
        self.inner.close()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        addresses = [str(literal)] if literal is not None else self.resolver(host)
        if not addresses or not all(address_allowed(a) for a in addresses):
            raise DeskError("source_address_refused")
        pinned = httpx.Request(request.method, request.url.copy_with(host=addresses[0]),
                               headers=request.headers,
                               extensions={**request.extensions, "sni_hostname": host})
        pinned.headers["Host"] = host
        return self.inner.handle_request(pinned)


def retry_delay(header, attempt):
    """Seconds to wait before retrying a 429, or None when the cooldown is too long.

    Never retry before a server-requested cooldown. Long cooldowns require a later
    operator-authorized cycle, not a sleep inside the claim route (#506).
    """
    delay = 2 ** (attempt + 1)
    if header is not None:
        try:
            delay = float(header)
        except ValueError:
            try:
                delay = (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                pass
    if not 0 <= delay <= 10:
        return None
    return max(1, delay)


def _market_read(url: str) -> bool:
    """Only safe, public Kalshi market GETs are retried on 429."""
    parts = urlsplit(url)
    return (parts.hostname == "api.elections.kalshi.com"
            and (parts.path == "/trade-api/v2/markets" or parts.path.startswith("/trade-api/v2/markets/")))


def _html_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|template|svg)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?i)<\s*(br|/p|/div|/li|/tr|/h[1-6]|/table|/section|/article)\b[^>]*>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]*>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    return re.sub(r"\s*\n\s*", "\n", raw).strip()


def _kind(content_type: str) -> str:
    media = content_type.split(";", 1)[0].strip().lower()
    if media in _HTML_TYPES:
        return "html"
    if media == "application/pdf":
        return "pdf"
    if not media or media.startswith(_TEXT_TYPES) or media.endswith(("+json", "+xml")):
        return "text"
    return "binary"


class PublicFetcher:
    """GET one public HTTPS URL as captured, provenance-stamped evidence."""

    def __init__(self, *, transport=None, resolver=None, max_bytes=MAX_SOURCE_BYTES,
                 max_chars=MAX_SOURCE_CHARS, deadline_seconds=FETCH_DEADLINE_SECONDS):
        # An injected transport (tests) still passes through the same address checks.
        self.inner, self.resolver = transport, resolver
        self.max_bytes, self.max_chars, self.deadline = max_bytes, max_chars, deadline_seconds

    def close(self):
        if self.inner is not None:
            self.inner.close()

    def _get(self, url: str, started: float) -> tuple[httpx.Response, bytes]:
        if time.monotonic() - started > self.deadline:
            raise DeskError("source_timeout")
        request = httpx.Request("GET", url, headers={
            "User-Agent": "KalshiDeskResearch/2.0 (+evidence capture)",
            "Accept": "text/html,application/json,text/plain,application/xml;q=0.9,*/*;q=0.5",
            "Accept-Encoding": "gzip, deflate"},
            extensions={"timeout": httpx.Timeout(20, connect=5).as_dict()})
        transport = PinnedTransport(resolver=self.resolver, inner=self.inner)
        try:
            response = transport.handle_request(request)
            try:
                body = bytearray()
                if response.status_code == 200:
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self.max_bytes:
                            raise DeskError("source_too_large")
                        if time.monotonic() - started > self.deadline:
                            raise DeskError("source_timeout")
                return response, bytes(body)
            finally:
                response.close()
        except httpx.TimeoutException:
            raise DeskError("source_timeout") from None
        except httpx.HTTPError:
            raise DeskError("source_connection_failure") from None
        finally:
            if self.inner is None:
                transport.close()  # per-hop pool; see PinnedTransport

    def __call__(self, url: str, now: datetime | None = None) -> dict:
        requested, _host = check_url(url)
        current, redirects, started, limited = requested, [], time.monotonic(), 0
        while True:
            response, body = self._get(current, started)
            if response.status_code == 429:
                # The response is already closed; wait, then repeat the same safe GET.
                delay = retry_delay(response.headers.get("retry-after"), limited)
                if not _market_read(current) or limited == 2 or delay is None:
                    raise DeskError("source_rate_limited")
                limited += 1
                time.sleep(delay)
                continue
            location = response.headers.get("location")
            if response.status_code in (301, 302, 303, 307, 308) and location:
                if len(redirects) >= MAX_REDIRECTS:
                    raise DeskError("source_too_many_redirects")
                current, _host = check_url(urljoin(current, location))
                redirects.append(current)
                continue
            if response.status_code != 200:
                raise DeskError("source_http_failure")
            break
        content_type = response.headers.get("content-type", "")
        kind = _kind(content_type)
        if kind == "pdf":
            # In-process PDF parsing of untrusted input is not run on the real-money
            # service; capture the HTML/text/data rendition of the same document instead.
            raise DeskError("source_pdf_unsupported")
        if kind == "binary":
            raise DeskError("source_content_type_unsupported")
        charset = response.charset_encoding or "utf-8"
        try:
            text = body.decode(charset, errors="replace")
        except LookupError:
            text = body.decode("utf-8", errors="replace")
        if kind == "html":
            text = _html_text(text)
        truncated = len(text) > self.max_chars
        stored = text[:self.max_chars]
        final_host = urlsplit(current).hostname or ""
        metadata = {}
        if final_host in KALSHI_HOSTS and not truncated:
            try:
                parsed = json.loads(stored)
                if isinstance(parsed.get("markets"), list):
                    metadata["_market_data"] = parsed["markets"]
                    metadata["_cursor"] = parsed.get("cursor", "")
                market = parsed.get("market")
                if market:
                    from .exchange import rules_hash
                    metadata["rules_sha256"] = rules_hash(market)
            except (ValueError, TypeError, AttributeError):
                pass
        return {**metadata, "source_id": uuid.uuid4().hex, "url": url,
                "final_url": current, "redirects": redirects,
                # Capture time includes any rate-limit wait inside this request.
                "retrieved_at": (now + timedelta(seconds=max(0, time.monotonic() - started))
                                 if now is not None else datetime.now(timezone.utc)).isoformat(),
                "content_type": content_type[:200], "kind": kind,
                "bytes": len(body), "chars": len(stored), "truncated": truncated,
                "excerpt": stored, "sha256": hashlib.sha256(stored.encode()).hexdigest()}
