# Probe skeleton, Kalshi API cheatsheet, and the ops run/log recipe

Read for **Phase 2** (write the probe) and **Phase 4** (run it and log the verdict). Everything
here is distilled from the probes that already run in this repo (`kalshi_art_survey.py`,
`kalshi_flb.py`, `kalshi_theta_study.py`, `econ_react_study.py`, …) — match them.

---

## 1. Self-contained probe skeleton (public-Kalshi-API version)

A probe runnable through the `ops` channel is **read-only, stdlib-only, and self-contained**. For
Kalshi's public API, reuse the browser-UA `_get` / `_num` from `xvenue_leadlag` (a default
`Python-urllib` UA gets a Cloudflare 1010 — never drop the browser UA). The dispatcher calls
`mod.main(args)`, so the entry point is `main(argv)`.

```python
"""<NAME> — <one-line question the probe answers>. (idea-model <area/date>; docs/<NAME>_THESIS.md)

<2-4 sentences: the thesis in brief, what this probe measures, and — critically — the
no-lookahead construction and that it is read-only public Kalshi REST, stdlib only.>
Usage: {"type":"script","name":"<name>","args":["--flag","v"],"id":"<slug>"}
"""
from __future__ import annotations

import argparse
import math
import time
from collections import defaultdict

import xvenue_leadlag as xl  # _get (browser UA + retries -> None on failure), _num (float or 0.0)

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"


def fee_cents(price_dollars: float, qty: int = 1) -> int:
    """Kalshi per-leg fee, cents: ceil(0.07 * qty * P * (1-P) * 100), P in dollars (0..1)."""
    p = min(max(price_dollars, 0.0), 1.0)
    return math.ceil(0.07 * qty * p * (1.0 - p) * 100)


def events(status: str, max_pages: int) -> list[dict]:
    """All events of a status (open|settled|closed), nested markets included, cursor-paged."""
    out, cursor = [], ""
    for _ in range(max_pages):
        page = xl._get(f"{KALSHI}/events?status={status}&with_nested_markets=true"
                       f"&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        out.extend(evs)
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break
        time.sleep(0.05)  # be polite to the public API
    return out


def candle_path(series: str, ticker: str, start: int, end: int) -> list[tuple[float, float, float]]:
    """[(ts, yes_ask_$, volume)] over [start,end], 1-min candles, ts-sorted. Fail-soft."""
    data = xl._get(f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
                   f"?start_ts={start}&end_ts={end}&period_interval=1")
    rows = []
    for c in (data or {}).get("candlesticks") or []:
        ts = xl._num(c.get("end_period_ts"))
        ya = (c.get("yes_ask") or {}).get("close_dollars")
        px = (c.get("price") or {}).get("close_dollars")
        ask = xl._num(ya) if ya is not None else xl._num(px)  # ask is the taker-buy price
        if ts and ask > 0:
            rows.append((ts, ask, xl._num(c.get("volume"))))
    rows.sort(key=lambda r: r[0])
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="<NAME> probe")
    ap.add_argument("--max-event-pages", type=int, default=60)
    ap.add_argument("--candle-samples", type=int, default=40, help="bounds API cost")
    args = ap.parse_args(argv)

    print("<NAME> — <question> (read-only Kalshi public REST)\n")
    settled = events("settled", args.max_event_pages)
    # ... filter to your series (precision over recall; print a matched (series [category])
    #     diagnostic), classify structure, census capacity (settled + candle coverage + volume),
    #     then MEASURE the pre-registered quantity with NO lookahead ...

    print("\n== verdict ==")
    # print PROMOTE / HOLD / KILL-leaning against the thesis' pre-registered bar, with the numbers.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**DB-reading variant.** If the probe reads the bot's own tables instead of (or with) the public
API, connect exactly like `scripts/db_query.py`: `psycopg.connect(os.environ["DATABASE_URL_RO"])`
in a read-only transaction. Keep it to a single logical read path; never write. Most idea-model
promotions are *new* categories the bot has never collected, so they are public-API probes — the
DB variant is for edges on data the worker already stores (`weather_*`, `crypto_*`, snapshots).

---

## 2. Kalshi public API + candlestick field cheatsheet

Base: `https://api.elections.kalshi.com/trade-api/v2` — market-data endpoints need **no auth**.

- **`/events?status=<open|settled|closed>&with_nested_markets=true&limit=200&cursor=<c>`** —
  paginated; each event has `category`, `series_ticker`, `title`, `sub_title`, `markets[]`.
- **Market fields** (on nested markets): `ticker`, `series_ticker`, `title`, `subtitle`,
  `result` (`yes`/`no` on settled), `close_time` (ISO), `volume` / `volume_fp`,
  `yes_bid_dollars`, `yes_ask_dollars` (0..1). Prefer `_fp` volume when present.
- **`/series/{series}/markets/{ticker}/candlesticks?start_ts=<unix>&end_ts=<unix>&period_interval=1`**
  — 1-min candles. Each candle: `end_period_ts` (unix), `volume` (contracts that minute),
  and nested `yes_bid`/`yes_ask`/`price` objects whose useful key is **`close_dollars`** (dollars,
  0..1). i.e. `(c.get("yes_ask") or {}).get("close_dollars")`. `period_interval` also supports
  `60` (hourly) / `1440` (daily); chunk long spans to bound response size.
- **`/markets/{ticker}/trades?min_ts=<unix>`** — the public trade tape (for maker/tape probes; see
  `kalshi_mm.py` / `kalshi_theta_study.py`). Each trade has price, count, `taker_side`.

**Fee (both legs matter):** `ceil(0.07 · qty · P · (1−P) · 100)` cents, `P` in dollars — quadratic,
~2¢ near 50¢, ~0 at the tails. Buy-and-hold-to-settlement pays the fee once (entry); an early exit
pays it again. Settlement itself is free.

**Gotchas:** use the **real** identifier (the Kalshi ticker strike), never a title-parsed number
(false pairs fake divergence). Match cross-venue by precision-over-recall. Measure
**event-conditional**, not averages (averages hide the informative moments). Treat any large clean
+EV as guilty until proven (the "already-decided-favorite" lookahead bug recurs constantly).

---

## 3. The ops run/log recipe (Phase 4)

A new allowlisted probe is only runnable **after it is merged to the default branch** — the `ops`
runner executes the copy of the script on the default branch, not your feature branch. There is
nothing to refresh: the runner checks the default branch out on every request, so the merge alone
makes the script live. **Never force-refresh `ops`**; it is protected against force pushes and
deletion (XOS-000007, see "Protecting the `ops` branch" in `docs/OPS_RUNBOOK.md`).

```bash
# 0. (after the PR merges) nothing to do — the runner picks the new script + allowlist up itself

# 1. request a run on a clean worktree (don't disturb your branch); ALWAYS set a unique id.
#    HARD GUARD (do not omit): abort if the worktree `cd` fails. Without it, the `git reset
#    --hard origin/ops` below runs in your MAIN checkout and silently resets your working
#    branch to the ops tip — a real footgun that has bitten this workflow once.
git fetch origin ops -q
WT=/tmp/ops-run; rm -rf "$WT"; git worktree prune
git worktree add "$WT" ops || { echo "ABORT: worktree add failed"; exit 1; }
cd "$WT" || { echo "ABORT: cd failed — resetting nothing"; exit 1; }
[ "$(git rev-parse --show-toplevel)" = "$WT" ] || { echo "ABORT: not inside the worktree"; exit 1; }
git reset --hard origin/ops -q            # safe: guaranteed inside $WT by the guard above
printf '%s\n' '{"type":"script","name":"<name>","args":["--max-event-pages","60"],"id":"<slug>"}' \
  > ops/request.json
git add ops/request.json && git commit -q -m "ops: run <name>"
git push origin ops || { git fetch origin ops -q; git reset --hard origin/ops -q; echo \
  "rejected (ops moved) — reconciled INSIDE the worktree; re-apply request.json and re-push"; }

# 2. poll for YOUR result (~30-90s) — read ops/results/<slug>.txt (uniquely named; a concurrent
#    /loop run can overwrite the shared ops/result.txt pointer but never your per-id file)
git fetch origin ops -q && git show FETCH_HEAD:ops/results/<slug>.txt

# 3. leave the channel idle, then remove the worktree (cd OUT of it by ABSOLUTE path first)
printf '%s\n' '{"type": "noop"}' > ops/request.json
git add ops/request.json && git commit -q -m "ops: noop" && git push origin ops
cd /path/to/your/main/checkout && git worktree remove --force "$WT"
```

Reconcile a rejected push (a concurrent producer moved `ops`) with `git reset --hard origin/ops`
**only ever inside the ops worktree** — the abort-guard in step 1 is what makes that safe. NEVER
run `git reset --hard origin/ops` from your main checkout. Never open a PR merging `ops` into the
default branch (GitHub auto-deletes the branch on merge and kills the trigger).

**Test-before-merge variant.** To re-run a *fixed* probe without waiting for a merge (e.g. after
a first run exposed a bug), you **cannot** overlay a corrected script onto `ops`: since
XOS-000005 the runner checks the default branch out into `.ops-runner-code/` and executes only
that copy, and it fails closed if it did not. `ops` is transport, never code. Push the fix to your
feature branch, merge it, and re-run — the merge alone makes it live.

---

## 4. Log the verdict (close the loop)

After reading the probe output, update three places so the record stays honest and coherent:
- **`docs/<NAME>_THESIS.md`** — set the Status line to the verdict (PASS/FAIL per prediction), and
  add a short RESULTS block with the numbers, exactly like `THETA_THESIS.md`'s RESULTS section.
- **`docs/IDEA_MODEL_SCORECARD.md`** — set the ledger row's verdict date + verdict + outcome, and
  keep the base-rate / per-family tallies in sync.
- **`docs/RESEARCH_JOURNAL.md`** — a dated entry with the measured numbers and the decision.

Then: a **PASS** on the decision rule hands off to `kalshi-strategy` (build the paper book). A
**FAIL** of a pre-registered kill criterion is a clean ruling-out — logged as a win, family closed.
A **HOLD** (census too thin / testability not yet met) names the specific missing piece and the
re-run trigger. In all three cases the probe did its job.
