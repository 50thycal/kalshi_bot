"""Render the status page, and decide whether today's check-in notifies the owner.

    python -m kalshi_bot.monitor.page --snapshot ops/results/<id>.txt \\
        [--previous-page old.html | --previous snapshot.json] --out status.html

Prints one JSON verdict on stdout: `{"notify": bool, "reasons": [...], ...}`.

Pure and local: it reads files and writes one file. The snapshot comes from the
ops channel (`xos monitor-snapshot`); the previous snapshot is the one embedded in
the page published last time, so "what is NEW" needs no second store and no
commit — the page carries its own history forward.

## When the owner is told

The page is republished every day; the owner is MESSAGED only when something
needs them (DEC: WS-026). Two classes:

* **Broken, every day until fixed** — an experiment ERROR / SILENT / STALE, or an
  external system ERROR / STALE. Something that should be running is not, and
  repeating that daily is the point.
* **New since the last page** — an experiment newly BLOCKED, a new operator
  decision, a new open issue, a newly detected problem with no issue (by
  fingerprint), or a gate whose sample floor is estimated to complete within
  `VERDICT_NOTIFY_DAYS` that was not already inside that window.

Chronic blocked gates and old decisions are on the page, not in a message: a
message that repeats unchanged every morning is a message that gets muted.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[2] / "dashboards" / "status.html"
PLACEHOLDER = "/*__SNAPSHOT__*/null"
VERDICT_NOTIFY_DAYS = 2.0
BROKEN_EXPERIMENT = ("ERROR", "SILENT", "STALE")
BROKEN_EXTERNAL = ("ERROR", "STALE")

_EMBED_RE = re.compile(
    r'<script id="snapshot" type="application/json">(.*?)</script>', re.S)


def load_snapshot(text: str) -> dict:
    """A snapshot from raw JSON or from an ops result file (`# ...` header lines
    followed by the CLI's JSON)."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object found")
    snap = json.loads(text[start:end + 1])
    if snap.get("schema", "").split("/")[0] != "monitor-snapshot":
        raise ValueError(f"not a monitor snapshot (schema {snap.get('schema')!r})")
    return snap


def extract_embedded(html: str) -> dict | None:
    """The snapshot a previously rendered page carries, or None."""
    m = _EMBED_RE.search(html)
    if not m:
        return None
    body = m.group(1).replace("<\\/", "</").strip()
    try:
        snap = json.loads(body)
    except ValueError:
        return None
    return snap if isinstance(snap, dict) else None


def render(snapshot: dict, template: str | None = None) -> str:
    tpl = template if template is not None else TEMPLATE.read_text()
    if PLACEHOLDER not in tpl:
        raise ValueError("template has no snapshot placeholder")
    # `</` would close the <script> early; JSON allows the escaped form.
    payload = json.dumps(snapshot, separators=(",", ":"), default=str).replace("</", "<\\/")
    return tpl.replace(PLACEHOLDER, payload, 1)


def attention(snap: dict, previous: dict | None = None) -> dict:
    """Whether to message the owner, and why. Pure."""
    prev = previous or {}
    prev_health = {e["key"]: e["health"] for e in prev.get("experiments") or []}
    prev_ext = {x["key"]: x.get("health") for x in prev.get("external") or []}
    prev_you = set(prev.get("needs_you") or [])
    prev_issues = {i["issue_key"] for i in prev.get("issues") or []}
    prev_fp = {c["fingerprint"] for c in prev.get("untracked") or []}
    prev_soon = {e["key"] for e in prev.get("experiments") or [] if _soon(e)}
    first = previous is None

    reasons: list[str] = []
    for e in snap.get("experiments") or []:
        why = "; ".join(r for r in e.get("health_reasons") or []
                        if not r.startswith("blocked gate")) or e["health"]
        if e["health"] in BROKEN_EXPERIMENT:
            reasons.append(f"{e['key']} {e['health']}: {why}")
        elif e["health"] == "BLOCKED" and (first or prev_health.get(e["key"]) != "BLOCKED"):
            reasons.append(f"{e['key']} newly BLOCKED: " + "; ".join(e.get("health_reasons") or []))
        if _soon(e) and (first or e["key"] not in prev_soon):
            nv = e["next_verdict"]
            reasons.append(f"{e['key']} · {nv['gate_key']}: verdict floor "
                           f"{int(nv['progress'] * 100)}%, estimated {nv['eta_date']}")
    for x in snap.get("external") or []:
        if x.get("health") in BROKEN_EXTERNAL:
            reasons.append(f"{x['title']} {x['health']}: {x.get('detail')}")
        elif x.get("health") == "UNKNOWN" and prev_ext.get(x["key"]) == "OK":
            reasons.append(f"{x['title']} could not be assessed today: {x.get('detail')}")
    for line in snap.get("needs_you") or []:
        if first or line not in prev_you:
            reasons.append(f"decision: {line[:240]}")
    new_issues = [i for i in snap.get("issues") or []
                  if not first and i["issue_key"] not in prev_issues]
    for i in new_issues:
        reasons.append(f"new issue {i['issue_key']} ({i.get('owner_role')}): {i.get('title')}")
    new_fp = [c for c in snap.get("untracked") or [] if c["fingerprint"] not in prev_fp]
    if new_fp:
        reasons.append(f"{len(new_fp)} newly detected problem(s) with no issue — "
                       "the owning roles adopt them (OPEN_CANDIDATE)")
    return {
        "notify": bool(reasons),
        "reasons": reasons,
        "new_untracked": [_adopt_hint(c) for c in new_fp],
        "compared_with": prev.get("generated_at"),
    }


def _soon(e: dict) -> bool:
    nv = e.get("next_verdict")
    return bool(nv and nv.get("eta_days") is not None
                and nv["eta_days"] <= VERDICT_NOTIFY_DAYS)


def _adopt_hint(c: dict) -> dict:
    """What the owning role needs to adopt a candidate. Prepared, never sent:
    issue writes redeploy the worker and belong to that role (WS-026 decision)."""
    return {"recommended_owner": c.get("recommended_owner"), "title": c.get("title"),
            "scope": c.get("scope"), "fingerprint": c.get("fingerprint")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kalshi_bot.monitor.page")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--snapshot", help="snapshot JSON, or the ops result file that holds it")
    src.add_argument("--tower", help="FALLBACK: a `control-tower --json` result, assembled "
                     "here without the monitor's own reads (no per-arm evidence, no live "
                     "realized, no external probes) — for when monitor-snapshot is unavailable")
    ap.add_argument("--previous", help="previous snapshot JSON")
    ap.add_argument("--previous-page", help="previously published page (embedded snapshot)")
    ap.add_argument("--out", required=True, help="where to write the rendered page")
    args = ap.parse_args(argv)

    if args.tower:
        from .snapshot import assemble

        text = Path(args.tower).read_text()
        tower = json.loads(text[text.find("{"):text.rfind("}") + 1])
        snap = assemble(tower, None, None, tower["generated_at"])
        snap["partial"] = ("assembled from control-tower only: per-arm evidence, live "
                           "realized and external systems were not read")
    else:
        snap = load_snapshot(Path(args.snapshot).read_text())
    previous = None
    if args.previous:
        previous = load_snapshot(Path(args.previous).read_text())
    elif args.previous_page and Path(args.previous_page).exists():
        previous = extract_embedded(Path(args.previous_page).read_text())
    Path(args.out).write_text(render(snap))
    verdict = attention(snap, previous)
    verdict["page"] = args.out
    verdict["generated_at"] = snap.get("generated_at")
    print(json.dumps(verdict, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
