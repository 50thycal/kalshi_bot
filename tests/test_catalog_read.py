"""scripts/catalog_read.py — the ops channel's GET-only market-catalog client. Pins the path
allowlist, the token handling and that nothing secret reaches the public result."""

from __future__ import annotations

import importlib.util
import pathlib
import threading

import pytest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
_SPEC = importlib.util.spec_from_file_location("catalog_read", _SCRIPTS / "catalog_read.py")
cr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cr)  # type: ignore[union-attr]


def test_build_url_allows_only_v1_reads(monkeypatch):
    monkeypatch.delenv(cr.URL_ENV, raising=False)
    assert cr.build_url("/v1/status") == cr.DEFAULT_URL + "/v1/status"
    assert cr.build_url("/v1/markets", {"series": "KXRAIN", "limit": 5}).endswith(
        "/v1/markets?series=KXRAIN&limit=5")
    for bad in ("/health", "/v1/../admin", "v1/status", "/v1/a?b=c", "/v2/x"):
        with pytest.raises(cr.CatalogReadError):
            cr.build_url(bad)
    with pytest.raises(cr.CatalogReadError):
        cr.build_url("/v1/markets", {"bad key": 1})
    with pytest.raises(cr.CatalogReadError):
        cr.build_url("/v1/markets", {"series": ["a"]})


def test_base_url_must_be_plain_https(monkeypatch):
    monkeypatch.setenv(cr.URL_ENV, "http://example.com")
    with pytest.raises(cr.CatalogReadError):
        cr.base_url()


def test_unconfigured_token_fails_without_a_network_call(monkeypatch, capsys):
    monkeypatch.delenv(cr.TOKEN_ENV, raising=False)
    assert cr.run({"type": "catalog", "path": "/v1/status", "id": "c-1"}) == 1
    assert "not_configured" in capsys.readouterr().out


def test_unknown_keys_are_refused(capsys):
    assert cr.run({"type": "catalog", "path": "/v1/status", "method": "POST"}) == 1
    assert "unknown request keys" in capsys.readouterr().out


def test_end_to_end_against_a_read_token_catalog(monkeypatch, capsys, tmp_path):
    from kalshi_bot.catalog import service
    from kalshi_bot.catalog.store import Store

    store = Store(tmp_path / "c.sqlite3")
    server = service.make_server(store, "a" * 32, ("127.0.0.1", 0), read_token="r" * 32)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(cr, "base_url", lambda: f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setenv(cr.TOKEN_ENV, "r" * 32)
        assert cr.run({"type": "catalog", "path": "/v1/status", "id": "c-2"}) == 0
        out = capsys.readouterr().out
        assert "r" * 32 not in out and "evidence" in out
        monkeypatch.setenv(cr.TOKEN_ENV, "x" * 32)
        assert cr.run({"type": "catalog", "path": "/v1/status", "id": "c-3"}) == 1
        out = capsys.readouterr().out
        assert "authentication_refused" in out and "x" * 32 not in out
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
