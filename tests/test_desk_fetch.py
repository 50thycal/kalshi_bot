"""desk_fetch — the allowlist IS the security model, so it is what gets pinned.

No network: `fetch` is monkeypatched. The failure modes pinned: an off-list host (or a plain
http URL) never reaches the network; HTML is reduced to visible text with scripts dropped;
JSON is pretty-printed; --find returns bounded windows; output never exceeds the hard cap.
"""

from __future__ import annotations

import contextlib
import io
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import desk_fetch as df  # noqa: E402


def test_the_script_is_allowlisted_on_the_ops_channel():
    import ops_runner
    assert "desk_fetch" in ops_runner.ALLOWED_SCRIPTS


def test_only_https_on_allowlisted_hosts():
    assert df.host_allowed("https://gasprices.aaa.com/")
    assert df.host_allowed("https://openrouter.ai/rankings?view=week")
    assert df.host_allowed("https://www.ercot.com/content/cdr/html/loadForecastVsActualCurrentDay.html")
    assert not df.host_allowed("http://gasprices.aaa.com/")          # plain http
    assert not df.host_allowed("https://evil.example.com/gasprices.aaa.com")
    assert not df.host_allowed("https://gasprices.aaa.com.evil.example/")
    assert not df.host_allowed("https://api.elections.kalshi.com/trade-api/v2/markets")  # the board does that
    assert not df.host_allowed("not a url")


def test_off_list_host_never_touches_the_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("fetch must not be called")
    monkeypatch.setattr(df, "fetch", boom)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        assert df.main(["https://example.com/"]) == 1
    assert "refused" in err.getvalue()


def test_html_becomes_visible_text_without_scripts():
    html = "<html><head><style>x{}</style><script>var a=1;</script></head><body><h1>Diesel</h1><p>$6.45 &amp; up</p></body></html>"
    text = df.to_text(html, "text/html")
    assert "Diesel" in text and "$6.45 & up" in text
    assert "var a" not in text and "x{}" not in text


def test_json_is_pretty_printed():
    assert df.to_text('{"b":1,"a":[1,2]}', "application/json") == '{\n "a": [\n  1,\n  2\n ],\n "b": 1\n}'


def test_find_windows_are_bounded_and_case_insensitive():
    text = "aaa ANTHROPIC 2.91% bbb anthropic 2.80% ccc"
    hits = df.windows(text, "anthropic", 5, 10)
    assert hits == ["aaa ANTHROPIC 2.91", " bbb anthropic 2.80"]
    assert df.windows(text, "anthropic", 5, 1) == ["aaa ANTHROPIC 2.91"]
    assert df.windows(text, "zzz", 5, 10) == []


def test_main_prints_bounded_text_and_find_windows(monkeypatch):
    body = "<html><body>" + "<p>filler</p>" * 50 + "<p>Anthropic 2.91%</p></body></html>"
    monkeypatch.setattr(df, "fetch", lambda url, timeout=45: (200, "text/html", body))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert df.main(["https://openrouter.ai/rankings", "--max-chars", "100"]) == 0
    text = out.getvalue()
    assert "HTTP 200" in text and "more chars" in text
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert df.main(["https://openrouter.ai/rankings", "--find", "anthropic", "--window", "4"]) == 0
    assert "Anthropic 2.9" in out.getvalue()


def test_http_error_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(df, "fetch", lambda url, timeout=45: (403, "text/html", "blocked"))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert df.main(["https://www.eia.gov/petroleum/gasdiesel/"]) == 1
    assert "HTTP 403" in out.getvalue()
