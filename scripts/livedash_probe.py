"""How long does the deployed live-vs-paper dashboard actually take to answer?

WHY THIS EXISTS
---------------
WS-009 reduced the dashboard's read cost and phased its load, and then could not
finish, because its closing check was "confirm on the deployed livedash that first
paint is seconds rather than half a minute" and nothing in the repository could
make that measurement. The ops channel can report the service's deployment status
and its startup lines; it cannot time a page. So the verification sat open, and
the next regression was found the same way the first one was — an operator opened
the page and it was broken.

The Claude sandbox cannot close the gap either: its egress policy refuses
`*.up.railway.app` at the proxy (403 to CONNECT), so no session can time the URL
directly. The ops runner CAN — it executes in GitHub Actions, which has ordinary
outbound internet — which makes this the one place the measurement can be taken
from.

WHAT IT MEASURES
----------------
Each dashboard route, once, in the order the page itself requests them, reporting
status, wall-clock milliseconds and response size:

    /healthz                     the service is up at all
    /                            the HTML shell
    /api/runs?view=selector      phase 1 — the run picker
    /api/runs/<tag>              phase 2 — position + comparison card
    /api/runs/<tag>/series       phase 3 — the P&L overlay
    /api/runs/<tag>/orders       phase 3
    /api/runs/<tag>/events       phase 3
    /api/runs                    phase 4 — the all-runs history table

`--tag` picks the pair. Without one the probe reads the selector's `default_run`,
which is the pair an operator's browser would open on, so the default measurement
is of the default experience.

READ THE VERDICT
----------------
The bottom line is per-route, not an average: the page renders section by section,
so one route at thirty seconds is a blank card next to seven that worked, and an
average hides exactly that. Any route at or above `SLOW_MS` is called out.

A non-JSON body on an `/api/` route is reported as `BAD-BODY`. That is the
signature of an edge timeout — the browser's own error message for it is the
literal string "bad response" — and it is worth distinguishing from a clean 5xx,
because the origin may well have succeeded and simply answered too late.

Read-only: issues GET requests to a public dashboard URL and nothing else. No API
key, no database, no orders. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request

#: A route at or above this is reported as slow. The page makes eight requests on a
#: cold load; two seconds in any one of them is somebody watching a skeleton.
SLOW_MS = 2000

#: Per-request ceiling. Comfortably past any edge timeout the page could be hitting,
#: so a route that is merely slow is measured rather than truncated by the probe.
TIMEOUT_S = 120

_UA = "kalshi-livedash-probe/1.0"


def _get(base: str, path: str, timeout: int = TIMEOUT_S) -> dict:
    """One GET, timed. Never raises: a failed route is a result, not a crash — the
    remaining routes are exactly what says whether the failure is the whole service
    or one payload."""
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    began = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            code = resp.status
    except urllib.error.HTTPError as exc:          # a real response, just not a 2xx
        body, code = exc.read(), exc.code
    except Exception as exc:                        # noqa: BLE001 — timeout, DNS, reset
        return {"path": path, "ms": (time.perf_counter() - began) * 1000,
                "status": None, "bytes": 0, "error": type(exc).__name__, "json": None}
    ms = (time.perf_counter() - began) * 1000
    parsed = None
    error = None
    if path.startswith("/api/"):
        try:
            parsed = json.loads(body.decode("utf-8"))
        except Exception:                           # noqa: BLE001
            # Exactly what the browser sees as "bad response": headers arrived,
            # the body is not the JSON the page asked for.
            error = "BAD-BODY"
    return {"path": path, "ms": ms, "status": code, "bytes": len(body),
            "error": error, "json": parsed}


def _row(result: dict) -> str:
    status = result["status"] if result["status"] is not None else "---"
    flag = ""
    if result["error"]:
        flag = f"  <-- {result['error']}"
    elif result["status"] and result["status"] >= 400:
        flag = "  <-- HTTP error"
    elif result["ms"] >= SLOW_MS:
        flag = "  <-- SLOW"
    return (f"  {str(status):>4}  {result['ms']:8.0f}ms  {result['bytes']:>9,}B  "
            f"{result['path']}{flag}")


def probe(base: str, tag: str | None) -> int:
    print(f"livedash probe — {base}")
    print(f"  slow threshold {SLOW_MS}ms, per-request timeout {TIMEOUT_S}s\n")

    results = [_get(base, "/healthz"), _get(base, "/")]
    selector = _get(base, "/api/runs?view=selector")
    results.append(selector)

    if not tag:
        payload = selector["json"] or {}
        tag = payload.get("default_run")
        if not tag:
            runs = payload.get("runs") or []
            tag = runs[0].get("twin_tag") if runs else None
    if not tag:
        print("\n".join(_row(r) for r in results))
        print("\n  no run tag: the selector returned no pair and none was given with "
              "--tag,\n  so the four per-run routes cannot be probed.")
        return 1

    print(f"  probing pair: {tag}\n")
    for path in (f"/api/runs/{tag}",
                 f"/api/runs/{tag}/series",
                 f"/api/runs/{tag}/orders?offset=0&limit=100",
                 f"/api/runs/{tag}/events?category=all&limit=50",
                 "/api/runs"):
        results.append(_get(base, path))

    print("status      elapsed        bytes  route")
    for result in results:
        print(_row(result))

    slow = [r for r in results if r["ms"] >= SLOW_MS]
    broken = [r for r in results if r["error"] or (r["status"] or 0) >= 400]
    print()
    if broken:
        print("  BROKEN — these routes did not return a usable payload, so the sections"
              "\n  they feed render an error rather than numbers:")
        for r in broken:
            print(f"    {r['path']}  ({r['error'] or 'HTTP ' + str(r['status'])})")
    if slow:
        print(f"\n  SLOW — at or above {SLOW_MS}ms, worst first:")
        for r in sorted(slow, key=lambda r: -r["ms"]):
            print(f"    {r['ms']:8.0f}ms  {r['path']}")
        print("\n  Match these against the service's own `livedash stages …` log lines"
              "\n  for the same window: the probe says which ROUTE is slow, the stage"
              "\n  lines say which part of it spent the time.")
    if not slow and not broken:
        print("  All routes returned a usable payload under the slow threshold.")
    return 1 if broken else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Time every route of the deployed livedash.")
    ap.add_argument("--base", default=os.getenv("LIVEDASH_URL"),
                    help="dashboard base URL; defaults to $LIVEDASH_URL")
    ap.add_argument("--tag", default=None,
                    help="twin tag to probe; defaults to the selector's default_run")
    args = ap.parse_args(argv)
    if not args.base:
        print("no dashboard URL: pass --base https://<host> or set LIVEDASH_URL.")
        return 1
    return probe(args.base, args.tag)


if __name__ == "__main__":
    raise SystemExit(main())
