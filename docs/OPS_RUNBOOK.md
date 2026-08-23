# Ops runbook — logs, read-only database access, and the standing commands

Moved out of `CLAUDE.md` by the Claude Session System so global context stays a
router rather than an operating diary. This is the full mechanism plus the
standing analysis commands. `CLAUDE.md` keeps the minimal recipe every session
needs; everything else lives here.

**Experiment OS is canonical for experiment lifecycle and evidence.** The
analysis commands below are *specialist diagnostics* — they compute evidence a
generic metric cannot. None of them is a status system, and none of them
overrides Experiment OS state.

## Operating the logs + database access system

This repo has a system that lets **you (Claude)** fetch Railway logs and run
read-only Postgres queries on your own. Read this before trying to inspect logs
or data.

### Why it's built this way

The Claude Code web sandbox can only make **HTTP requests to allow-listed hosts**
— GitHub is allowed, but **Railway and Postgres are not reachable from your
environment.** So everything is routed through **GitHub Actions runners**, which
have open internet. You drive the runners and read their job logs back through
the GitHub MCP tools.

### Preferred channel: the `ops` branch (you can do this with no human help)

You **cannot** trigger a `workflow_dispatch` via the GitHub API — it returns
`403 Resource not accessible by integration`. Instead, you trigger work by
**pushing a request file to the `ops` branch**, which fires the `Ops Runner`
workflow automatically.

To run a request:

1. Work against the `ops` branch without disturbing your current branch — a
   worktree is cleanest:
   ```bash
   git fetch origin ops
   git worktree add /tmp/ops ops
   ```
2. Overwrite `/tmp/ops/ops/request.json` with **one** of:
   ```jsonc
   {"type": "db",   "sql": "select ...", "max_rows": 200, "id": "pnl-check-1"}
   {"type": "logs", "limit": 200, "filter": "", "deployment_id": "", "id": "logs-1"}
   {"type": "script", "name": "weather_model_check", "args": ["--sigma", "1.5"]}
   {"type": "xos",  "command": "control-tower", "id": "ct-1"}   # canonical Experiment OS read
   {"type": "env"}                                          # read allowlisted Railway vars
   {"type": "env", "set": {"KILL_SWITCH": "false"}}         # set allowlisted vars + redeploy
   {"type": "noop"}
   ```
   `script` runs an allowlisted self-contained read-only analysis script from
   `scripts/` (see `ALLOWED_SCRIPTS` in `scripts/ops_runner.py`).

   `xos` runs the **canonical Experiment OS CLI** — the same code the worker runs,
   so the operating layer can never drift from Experiment OS the way the retired
   status checkers drifted from each other. Allowlisted commands: `control-tower`,
   `list`, `show`, `transitions`, `platform`, `tag`, `scoreboard`, `enforcement`,
   `readiness`, `evaluate-gates`, `metric`, `issue-list`, `issue-show`,
   `issue-candidates`. Extra CLI flags go in `"args"`.

   `issue-*` reads the durable investigation queue
   (`docs/EXPERIMENT_OS_ISSUES.md`): open issues, one investigation with its full
   append-only history, and the anomalies the Control Tower detects that no open
   issue covers.

   ```jsonc
   {"type":"xos","command":"issue-list","id":"iss-1"}
   {"type":"xos","command":"issue-list","args":["--owner","LIVE_OPS"],"id":"iss-2"}
   {"type":"xos","command":"issue-show","args":["XOS-000123"],"id":"iss-3"}
   {"type":"xos","command":"issue-candidates","id":"iss-4"}
   {"type":"xos","command":"issue-findings-plan","id":"iss-5"}
   {"type":"xos","command":"issue-command-show","args":["lo-adopt-1"],"id":"iss-6"}
   {"type":"xos","command":"issue-command-list","id":"iss-7"}
   ```

   `issue-findings-plan` previews the historical contract-findings import and
   reconciliation without writing. Executing it is a WORKER action — set
   `EXPERIMENT_OS_RECONCILE_FINDINGS_ON_BOOT=true` via `env` and let one boot run
   it (idempotent; switch it back off after). See `docs/EXPERIMENT_OS_ISSUES.md`.

   Only those are allowlisted, and every one of them is a READ. Every `issue`
   subcommand that WRITES refuses to run against `DATABASE_URL_RO` — which is the
   only connection this channel ever has. **The worker remains the only writer.**
   Do not add a writable path here.

   An ordinary issue write reaches production through the worker, not through
   this channel: set **one** strictly validated envelope in
   `EXPERIMENT_OS_ISSUE_COMMAND` via `env`, and boot hook 2b-iii executes it once.
   `issue-command-show`/`issue-command-list` read the resulting receipts —
   metadata only, and they can neither execute nor retry anything. Exactly-once is
   the receipt: a committed `SUCCEEDED`/`REJECTED`/`FAILED` is terminal, so a
   restart re-reads the same variable and does nothing, and **retrying means a new
   `command_id`**.

   ⚠ **That envelope is PUBLIC.** It is committed in plaintext to
   `ops/request.json` on this public branch and stays in Git history. The
   redaction in `env` output is hygiene, not privacy: no secrets, credentials,
   personal data, private logs, account/order identifiers or sensitive raw
   evidence in a payload — bounded summaries and public references only. Full
   contract, vocabulary and worked sequence: `docs/EXPERIMENT_OS_ISSUES.md`.

   `metric` computes ONE canonical metric at an explicit scope and prints its
   value with full provenance — the only way to exercise a provider against
   production before any gate depends on it:

   ```bash
   {"type":"xos","command":"metric","args":[
      "live_cents_per_contract","--experiment","mmsell-scheduled-settle-live",
      "--arm","price_ceiling","--kind","live"],"id":"m-1"}
   ```

   `--kind` is required semantics and is never inferred. Asking for a live metric
   at `--kind paper` is a legitimate question whose real answer is MISSING with the
   addressing mismatch named — which is exactly how the imported live-canary
   contract defect shows up from the outside.

   This channel is **read-only against Postgres** (`DATABASE_URL_RO`), so
   `evaluate-gates` is dry-run only here — it prints what it *would* record and
   exits 2 if asked to persist. Actually recording results is the live worker's
   job (`EXPERIMENT_OS_EVALUATE_GATES`, settable via `env`), or an operator run on
   a writable connection: `docs/EXPERIMENT_OS_GATE_RESULTS.md`.
   ```jsonc
   {"type": "xos", "command": "evaluate-gates", "args": ["--dry-run"], "id": "ev-1"}
   ```

   **Two Railway services, one channel.** `env` and `logs` requests accept a
   `"service"` field selecting which worker to act on — `"main"`/`"live"` (default,
   the `BOT_MODE=live` trading worker) or `"evo"` (the `BOT_MODE=evo` evolutionary-agent
   worker). So the evo bot's logs/config are reachable the same way as the main bot's:
   ```jsonc
   {"type": "logs", "service": "evo", "limit": 120, "id": "evo-logs-1"}
   {"type": "env",  "service": "evo", "id": "evo-env-1"}                    // read evo vars
   {"type": "env",  "service": "evo", "set": {"EVO_MAX_ACTIVE_AGENTS": "5"}, "id": "evo-cad"}
   ```

   Each service's ID lives in a secret (`RAILWAY_SERVICE_ID` for main,
   `RAILWAY_EVO_SERVICE_ID` for evo — never in this public repo). `db` requests are
   **service-agnostic** (both workers share one Postgres via `DATABASE_URL_RO`).

   **Reading a book's evidence funnel (XOS-000004).** Every series-addressed book
   ends its cycle line with a bounded, publishable funnel summary, so the ops logs
   channel — which returns `message` text and drops structured attributes — can
   answer "where did this book's count first become zero?":

   ```jsonc
   {"type":"logs","filter":"funnel/v1","limit":100,"id":"funnel-1"}
   ```

   ```
   freeze book funnel/v1 state=NO_MARKETS first_zero=fetched fetched=0 eligible=0
     candidates=0 actions=0 empty_series=7/7 [KXCOCOA KXCOFFEE KXCORN KXCOTTON KXSOYBEAN +2]
   ```

   `state` is the diagnosis and `first_zero` is the stage to start from:

   | state | means |
   |---|---|
   | `NO_MARKETS` | every series was asked successfully and the venue returned nothing — check `empty_series` |
   | `FETCH_FAILED` | every series FAILED: the universe is **unknown**, not empty. An incident, not a venue answer |
   | `NO_MARKETS_INCOMPLETE` | nothing came back, but some series failed — the cycle saw less than the universe |
   | `NO_ELIGIBLE` | markets came back; the book's eligibility filter rejected all of them |
   | `NO_CANDIDATES` | eligible markets, none survived to become a priced candidate |
   | `NO_ACTIONS` | candidates were produced and rejected downstream (caps, bands, discount bar) |
   | `ACTIONS` | the book acted |
   | `<STAGE>_NOT_RUN` | that stage was SKIPPED this cycle — see below |

   **A stage that did not run is not a stage that found nothing.** xgame throttles
   discovery and wcprop only scans for signals while a settled-match trigger is
   open, so on most cycles those stages never execute. They report `NOT_RUN`
   rather than `0`, are listed in `not_run=`, and can never produce `NO_MARKETS` —
   a zero from a code path that was skipped is a finding nobody should try to
   explain. A skipped stage never masks a real one either: if a stage that DID
   run came back zero, that zero is still the diagnosis.

   All six series-addressed trackers publish this line: `freeze`, `pin15`,
   `theta`, `tfav`, `wcprop`, `xgame`. Each one's stage mapping is a statement
   about its own processing semantics (`FUNNEL_MAPPERS` in `kalshi_bot/main.py`),
   not a generic counter forced to fit.

   The `fetch=` field carries that second axis on its own (`OK`, `EMPTY_UNIVERSE`,
   `PARTIAL_FETCH_FAILURE`, `FETCH_FAILED`, `NO_SERIES_CONFIGURED`), and `empty=`
   and `failed=` are separate bounded lists. **`NO_MARKETS` is a claim about the
   VENUE** and is only ever made when every configured series was successfully
   asked — a zero from a fetch that never completed reads as `FETCH_FAILED` or
   `NO_MARKETS_INCOMPLETE` instead, because those have the opposite remedy.

   A series that returns HTTP 200 with an empty list also logs a WARNING naming it;
   an entirely empty configured universe logs a louder ERROR saying the book cannot
   trade (the condition that went unnoticed for nine days); and a cycle where every
   series failed logs its own ERROR saying the universe is UNKNOWN. No exception
   text appears in any of them.

   The summary is an **allowlist**, not a log dump: only the four stage counters
   and sanitized, count-capped series tickers are rendered, and the whole line is
   length-bounded. Ops results are public, and the workers emit ~260 distinct
   structured field names including raw payloads, order identifiers and account
   values — widening the log READ path to return attributes generically would
   publish all of it. See `kalshi_bot/obs/funnel.py`.

   **Always set a unique `"id"`** (any short slug — sanitized to `[A-Za-z0-9._-]`).
   The runner writes your output to a durable per-run file `ops/results/<id>.txt`
   in addition to the shared `ops/result.txt`. This is what lets you read back
   **exactly your own result** even when another producer (e.g. a parallel `/loop`
   session) drives the channel at the same time — the shared `result.txt` is only the
   latest-run pointer and can be overwritten by a concurrent run, but `ops/results/<id>.txt`
   is uniquely named, never conflicts, and is always published (the workflow retries
   against concurrent pushes). Omit `id` only for throwaway requests (it then falls back
   to a timestamp+run-number filename you'd have to hunt for).

   Two of these are the standing **commands** the user asks for by name:
   - **"PnL"** -> `{"type":"script","name":"weather_pnl"}` — the per-book rollup
     plus the per-window x strategy decision table (n, win%, total, P&L/trade).
     Present it as the tables discussed: per-trade is the deciding number.
   - **"experiment results"** -> `{"type":"script","name":"weather_experiments"}`
     — runs all three research probes in one shot (model check, exit sweep, entry
     study). Present each as a structured table.
   - **"digest"** (daily) -> `{"type":"script","name":"weather_digest"}` — the live
     operational digest: worker health, today's entries/exits + fill status, open
     positions (with best-effort current price + unrealized), realized P&L today,
     a per-book paper rollup, and an **ANOMALIES** section (bad-status orders, fills
     with no order row, untracked positions, stale worker, stuck orders). Lead with
     ANOMALIES — flag anything there; otherwise confirm "all clear". Meant to be run
     once a day on a schedule (`--hours N`, `--no-prices` to skip the Kalshi lookup).

     Every `weather_digest` run is **archived automatically**: the Ops Runner
     workflow snapshots the full result to `digests/<UTC-timestamp>.md` on the
     long-lived **`digest-archive`** branch (created on first run). That branch is
     the durable running history — append-only, browsable on GitHub, never merged
     into the default branch and separate from the disposable `ops` branch. You do
     **not** need to hand-commit digests anywhere; to review past digests, read from
     `digest-archive` (e.g. `git fetch origin digest-archive && git ls-tree -r --name-only FETCH_HEAD digests/`).
   - **"mmsell fill model"** -> `{"type":"script","name":"mmsell_fill_model"}` — the
     paper→live alignment read for the mmsell books. Paper ASSUMES a resting maker order
     always fills; live it fills ~70% and misses the winners (adverse selection). This
     calibrates the live mmsell3 (price → fill, realizable P&L) relationship and projects
     each book's price mix through it to report **realizable ¢/trade** (the number to gate
     on) vs the optimistic blended paper number, with a coverage %. **Gate variants on the
     realizable column, not blended paper** — see `docs/MMSELL_FILL_MODEL.md`. Companion
     `mmsell_live` is the live execution scorecard (fill rate, fill economics, realized vs
     paper shadow, open footprint).
   - **"mmsell exit study"** -> `{"type":"script","name":"mmsell_exit_study"}` — does a
     stop-loss or volatility exit beat hold-to-settlement, per book, and by how much. Replays
     each settled position's captured intraday path (`mmsell_position_ticks`, recorded live off
     the orderbook) through a grid of confirmed catastrophic stops (yes-mid ≥ L for K ticks) and
     volatility exits (yes-mid range over W ticks ≥ V), reporting mean, **5th-pctile tail**,
     win%, %exit and the **Δ vs hold** per rule. Gate on **Δp5 (tail) up AND Δmean ≥ −0.3¢** at
     n≥100 replayable — see `docs/MMSELL_EXIT_STUDY.md`. Coverage grows after deploy (a position
     must be born + settle inside the capture window); empty early output is a data-maturity wait.

     The **ANCHOR SET** (`mmsellA1`–`mmsellA5`, `docs/MMSELL_ANCHOR_SET.md`) forward-tests the
     three mechanics the backtests liked but couldn't power: a confirmed **yes-BID** stop at
     12/20/30¢ (A1–A3), a volatility **entry** gate (A4), and a two-sided **short strangle** (A5).
     All five sit on the mmsell10 entry, so **mmsell10 is the control** — read them against it, not
     in absolute terms. Paper-only; gates are pre-registered in the doc and `docs/BOOK_REGISTRY.md`.
     They surface in the `mm check 1` skill alongside the other books.
   - **"parity"** / **"live paper parity"** -> `{"type":"script","name":"live_paper_parity"}` — is
     our paper trading system telling the truth about a LIVE book? Every live strategy runs a fresh
     paper **twin** beside it (same start instant, same candidates, the LIVE price/size/cap knobs),
     so the only difference is the fill assumption paper cannot test. Reports decision alignment
     (and the exact gate that stopped live), fill rate + price gap, and settled twin-vs-live P&L —
     including the **matched-market** pairs, which separate a wrong simulator (ACCOUNTING GAP —
     invalidates every paper gate) from a real edge lost to execution (EXECUTION GAP). Lead with
     ANOMALIES. Mechanism + traps: `docs/LIVE_PAPER_TWIN.md`; the arm-and-audit procedure is the
     `live-paper-parallel` skill. **Standing policy: no strategy goes live without a twin.**

   - **"mmsell crypto study"** -> `{"type":"script","name":"mmsell_crypto_study"}` — backtests the
     BTC/ETH cheap-tail sell over **Kalshi's own settled history** (our paper slice is n=38, which
     can't decide anything): a stop-loss grid, volatility entry+exit gates, and the two-sided
     short strangle. See `docs/MMSELL_CRYPTO_STUDY.md`. Key results already banked: the
     bid-triggered stop improves mean AND tail on crypto (continuous-path, unlike sports jumps);
     the vol EXIT gate is dead; the strangle is intriguing but needs ~82 clean pairs.
     **Two traps this script documents — read before trusting any stop backtest:** trigger on the
     yes-**BID** (or require a ≥2-tick confirm), never the raw mid/ask, or thin-book quotes fire
     ~100% of stops as an artifact; and Kalshi only serves ~1h of candles for these series, so the
     backtest population is `htc<1h` while mmsell trades `htc>=1h` — different trades.

   - **"mmsell supply forecast"** -> `{"type":"script","name":"mmsell_supply_forecast"}` and
     **"mmsell regime backtest"** -> `{"type":"script","name":"mmsell_regime_backtest"}` — the
     seasonal forward-look (`docs/MMSELL_SEASONAL_FORECAST.md`). Our whole mmsell history is ONE
     regime (16 days of summer sports + BTC daily), so Sept–Nov (NFL, MLB playoffs, NBA/NHL, the
     Nov-3 midterms) cannot be backtested from our own books. The forecast script answers **how
     many** markets each regime will offer — live supply, the assumption-free window-entry
     calendar (`close − 14d`, since `htcmax=336h` is what holds November out), and the
     **settlement-date concentration** that decides the election risk. The backtest script answers
     **what each is worth**, replaying the mmsell10 entry over Kalshi's settled history per regime
     (coverage, band yield, edge, the A1/A2/A3 bid-stop check, and a per-settlement-date
     overdispersion measure). **The hard constraint both encode: Kalshi retains only a rolling
     ~70-day settled window** (paged to cursor exhaustion; `KXNFLGAME` returns zero; auth does not
     help), so last season is unavailable and the durable fix is to CAPTURE settled history as it
     happens — the pattern `kalshi_bot/weather/backfill.py` already implements. Gate on the YIELD
     column, not the P&L: the retained window yields n≈10 trades/regime, which decides nothing.

   - **"mmsell history status"** -> `{"type":"script","name":"mmsell_history_status"}` — is the
     settled-history CAPTURE working? `kalshi_bot/mmsell/history.py` (`RegimeHistoryCapture`) rides
     along the weather/live cycles and stores settled regime markets + their candles into
     `backfill_regime_markets` / `backfill_regime_candles` **before Kalshi's ~70-day window drops
     them** — that is the only reason Sept–Nov will be measurable in October. It is silent by
     design (it must never disturb the books), so this script is how you check it: lead with
     FRESHNESS (a stale write, or a pending queue that only grows, means it is failing quietly)
     and with **BEYOND-WALL** — markets we hold that the API no longer serves. That column is the
     point of the job and should only ever grow. Series are `MMSELL_HISTORY_SERIES`
     (env-overridable on Railway without a redeploy); find real tickers with
     `mmsell_supply_forecast --list-series <regime>` before adding one.

   - **"quote parity"** -> `{"type":"script","name":"mmsell_quote_parity"}` — may the entry scan
     PRE-FILTER on the event page's inline quote instead of fetching an orderbook per market?
     `GET /events?with_nested_markets=true` (the one call the scan already makes) returns
     top-of-book on every nested market, and the band gate needs only the midpoint + yes-ask. If
     it can be trusted, the ~650–1,600 orderbook calls/cycle drop 3–7×, which is what the
     top-150 event cap — and therefore ~1,740 unscanned eligible events/cycle — currently hangs
     on. The worker scores the inline quote against the orderbook it already fetched, free, every
     cycle. **Read the DECISION TABLE, not the agreement histograms**: `miss` is in-band markets a
     pre-filter would silently throw away, and the `tight` band (mirroring live `mmsell10`) comes
     before `wide`. Gate is pre-registered in `docs/MMSELL_QUOTE_PARITY.md`; empty output early is
     a data-maturity wait (n ≥ 50,000 over ≥ 100 cycles). The same output carries **RATE LIMITS** —
     retryable Kalshi responses split by HTTP status, so a 429 (over our API tier, actionable) is
     finally distinguishable from a 502 (noise). Non-zero 429s make the pre-filter the fix rather
     than an optimization. Also greppable now: `{"type":"logs","filter":"transient response 429"}`.

   - **"deconfound"** / **"universe study"** -> `{"type":"script","name":"mmsell_deconfound_study"}`
     — how much of an apparent mmsell book-vs-book difference is the ENTRY RULE and how much is
     the UNIVERSE it happens to trade. Reads the same `only=`/`mode=`/`maxyes` specs the books
     run, then holds one factor fixed at a time: same logic across universes (`mmsell5` vs
     `mmsell8`), same book split by asset class, same asset class across rules, and the cell
     where both books' filters admit the same market. Also prints fill rate and fill-selection
     haircut **by asset class**, hold-time and entry-price distributions, and the settled-market
     sample each detectable effect size would need. Read `13. SUCCESSOR CANDIDATES` before
     designing any mmsell A/B: it shows which comparisons are already available in paper.
     Findings: `docs/RESEARCH_MMSELL_UNIVERSE_DECONFOUNDING.md`. Never quote its "NAIVE" row as
     a treatment effect — it is labelled that way because it is the confound.

   - **"theta diagnosis"** / **"tail model"** -> `{"type":"script","name":"theta_tail_diagnosis"}`
     — WHY theta's tail model misses, as opposed to by how much. Separates six mechanisms:
     stale spot (checked against the independent candle feed at matched minutes), volatility
     LEVEL vs tail SHAPE (R by standardized strike distance — flat means a scale error, rising
     means a shape error), probability calibration, threshold-selection bias (the same quotes
     split by whether theta's entry filter would have fired), momentum, and time to expiry.
     Runs on theta4's PAPER history plus every ladder quote in the entry window, so it needs no
     live money. Findings: `docs/RESEARCH_THETA_TAIL_MODEL_DIAGNOSIS.md`. Check the
     derived-vs-recorded settlement agreement line before quoting sections B onward.

   The individual probes can still be run alone:
   `weather_model_check` grades the ensemble forecast distribution against the
   market's bucket prices on settled events (Brier/log-loss + hypothetical EV)
   and prints live model-vs-market disagreements. `weather_exit_sweep` replays
   settled weather paper trades through their recorded bucket price paths under
   a TP/SL grid to find the best exit rule vs holding to settlement.
   `weather_entry_study` runs the entry experiments (market calibration,
   price-band P&L, obs-confirmed entry, limit-entry fills).
   `weather_validation` reports over the persisted forecast→settlement dataset
   (`weather_forecast_outcomes`, materialized at settlement from the raw live tables):
   coverage/growth, forecast-vs-market skill, the ensemble-vs-market probabilistic edge
   on the winning bucket, and market-vs-forecast divergence vs who was right — the
   accumulating data that makes cal/dist/pm validatable.
3. Commit and push:
   ```bash
   cd /tmp/ops && git add ops/request.json && git commit -m "ops: <what>" && git push origin ops
   ```
4. Read the result. The runner commits your output back to the `ops` branch as the
   durable per-run file `ops/results/<id>.txt` (and updates the shared `ops/result.txt`
   pointer). Plain git is the simplest path (works even when the GitHub MCP tools are
   down) — read **your own `id`** so a concurrent producer's run can't shadow yours:
   ```bash
   # poll until YOUR result lands (~30-90s), then:
   git fetch origin ops && git show FETCH_HEAD:ops/results/<id>.txt
   # (git show FETCH_HEAD:ops/result.txt is the latest-run pointer — fine when you're
   #  the only producer, but it can be overwritten by a concurrent /loop run.)
   ```
   Alternatively via the GitHub MCP tools:
   - `actions_list` `method=list_workflow_runs`, `resource_id=ops-runner.yml` → newest run id
   - `actions_list` `method=list_workflow_jobs`, `resource_id=<run id>` → job id
   - `get_job_logs` `job_id=<job id>` `return_content=true` → the output is the query
     table or the log lines
5. Reset `ops/request.json` to `{"type": "noop"}` and push (leave the channel idle).

Notes:
- **Never open a PR merging `ops` into the default branch.** GitHub auto-deletes
  the branch on merge, which removes the trigger. If `ops` is ever missing,
  recreate it: `git checkout -B ops origin/<default-branch> && git push -u origin ops`.
- If `scripts/db_query.py`, `scripts/railway_logs.py`, `scripts/ops_runner.py`,
  `.github/workflows/ops-runner.yml`, or an allowlisted analysis script change on
  the default branch, refresh `ops` from the default branch (recreate as above) so
  it picks up the fix (e.g. the digest auto-archive step).
- Latency is ~30–60s per run. Request commits live only on `ops`; they never
  touch the default branch and never redeploy the Railway worker.

### Fallback channel: one-click workflows (need the human)

`DB Query (read-only)` (`db-query.yml`) and `Railway Logs` (`railway-logs.yml`)
are `workflow_dispatch` workflows the human runs from the Actions tab. You can
still read their results with `get_job_logs`. Use only if the `ops` channel is
unavailable.

### Database query rules (read-only — do not attempt writes)

- One **single** read-only statement per request: `SELECT` / `WITH` / `TABLE` /
  `EXPLAIN`. Writes and DDL are rejected by `scripts/db_query.py` and also blocked
  server-side (a SELECT-only role + a read-only transaction). Don't try to write.
- Schema reference: `kalshi_bot/models.py`. Key tables: `bot_runs`, `markets`,
  `market_snapshots`, `orderbook_snapshots`, `signals`, `paper_trades`,
  `paper_positions`, `account_snapshots`, `system_events`. Live/paper parallel runs:
  `live_paper_twins` (one epoch row per twin — `started_at` scopes BOTH sides of a
  paper-vs-live comparison; always filter on it) and `live_paper_parity_events` (the
  per-candidate decision tape: incumbent paper book / twin / real live outcome). Weather research:
  live-collected `weather_*` tables vs `backfill_weather_markets` /
  `backfill_weather_candles` (Kalshi REST history — separate provenance, never
  mix them silently in an analysis). `weather_forecast_outcomes` is the persisted
  forecast→settlement join (one labeled row per intraday cycle, materialized at
  settlement from the live `weather_*` tables) — the validation dataset.

### Secrets

Live in **GitHub Actions secrets**, not the repo: `DATABASE_URL_RO`,
`RAILWAY_TOKEN`, `RAILWAY_PROJECT_ID`, `RAILWAY_ENVIRONMENT_ID`,
`RAILWAY_SERVICE_ID` (main/live worker), and `RAILWAY_EVO_SERVICE_ID` (the evo
worker — enables `{"service":"evo"}` env/logs requests). Human setup instructions
are in `docs/REMOTE_ACCESS.md`.

### Gotchas

- Railway's API is behind Cloudflare, which 403s the default `Python-urllib`
  user-agent (error 1010). `scripts/railway_logs.py` sends a browser User-Agent
  to get through — **keep it.**
- `scripts/db_query.py` matches write/DDL keywords on **word boundaries**, so
  columns like `created_at` / `updated_at` are fine in a read-only query.
