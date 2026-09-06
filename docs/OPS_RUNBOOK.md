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
   {"type": "capabilities", "id": "cap-1"}                  # what this channel can do, generated
   {"type": "doctor", "id": "doc-1"}                        # one-request operating snapshot
   {"type": "incident", "service": "main", "window_minutes": 30, "id": "inc-1"}
   {"type": "env"}                                          # read allowlisted Railway vars
   {"type": "env", "action": "set", "values": {"KILL_SWITCH": "false"}}  # MUTATING + redeploy
   {"type": "noop"}
   ```

   Start with `capabilities` and `doctor` on a new session: the first says what
   the channel can currently do (generated from the allowlists, so it cannot go
   stale the way this document can), the second says what production is currently
   doing. Everything below is detail.

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

   An **experiment LIFECYCLE write** — registering a successor contract, arming a
   live canary — reaches production the same way, through a THIRD transport with
   its own vocabulary and its own receipt ledger:
   `EXPERIMENT_OS_EXPERIMENT_COMMAND` via `env`, executed once by boot hook 2b-v.
   `experiment-command-show`/`experiment-command-list` read the receipts, with the
   same prohibition: metadata only, and they can neither execute nor retry
   anything.

   ```jsonc
   {"type":"xos","command":"experiment-command-list","id":"xc-1"}
   {"type":"xos","command":"experiment-command-show","args":["mm10-register-1"],"id":"xc-2"}
   ```

   An envelope **names a reviewed package; it cannot author one.** Arms, gate
   specs, thresholds and tags are literals in the repository that someone read in
   a pull request — otherwise a scientific contract could be written in an
   environment variable the afternoon the results arrived, and pre-registration
   would mean nothing. Four actions:

   ```jsonc
   // register the contract — arms nothing, places nothing
   {"type":"env","set":{"EXPERIMENT_OS_EXPERIMENT_COMMAND":"{\"command_id\":\"mm10-register-1\",\"action\":\"REGISTER_PACKAGE\",\"actor\":\"claude-code\",\"actor_role\":\"TASK_SPECIFIC\",\"payload\":{\"package\":\"mmsell10-canary\"},\"schema_version\":1}"}}
   // repair deployment rows an engine defect left inconsistent — no contract,
   // no lifecycle state, no gate, no live lineage
   {"type":"env","set":{"EXPERIMENT_OS_EXPERIMENT_COMMAND":"{\"command_id\":\"tmmsell-repair-1\",\"action\":\"REPAIR_LINEAGE\",\"actor\":\"claude-code\",\"actor_role\":\"TASK_SPECIFIC\",\"payload\":{\"package\":\"tmmsell-epoch-repair\",\"reason\":\"XOS-000011\"},\"schema_version\":1}"}}
   // arm the canary — EXPANDS REAL-MONEY CAPABILITY; Live Ops only
   {"type":"env","set":{"EXPERIMENT_OS_EXPERIMENT_COMMAND":"{\"command_id\":\"mm10-arm-1\",\"action\":\"ARM_CANARY\",\"actor\":\"claude-code\",\"actor_role\":\"LIVE_OPS\",\"payload\":{\"package\":\"mmsell10-canary\",\"approved_by\":\"<operator>\"},\"schema_version\":1}"}}
   // record an experiment that ran and finished OUTSIDE the system, and retire it
   {"type":"env","set":{"EXPERIMENT_OS_EXPERIMENT_COMMAND":"{\"command_id\":\"perpv1-closeout-1\",\"action\":\"CLOSE_OUT_RETROSPECTIVE\",\"actor\":\"claude-code\",\"actor_role\":\"LIVE_OPS\",\"payload\":{\"package\":\"perp-v1\",\"approved_by\":\"<operator>\",\"reason\":\"<why it is over>\"},\"schema_version\":1}"}}
   ```

   The MARKTANGLE line closed the same way on 2026-09-03, in this order — the
   predecessor first, so the lineage is never a retired successor sitting above an
   experiment that formally does not exist:

   ```jsonc
   // MARKTANGLE-1: registers the contract and closes it in one act (never registered while it ran)
   {"type":"env","set":{"EXPERIMENT_OS_EXPERIMENT_COMMAND":"{\"command_id\":\"mkt1-closeout-1\",\"action\":\"CLOSE_OUT_RETROSPECTIVE\",\"actor\":\"claude-code\",\"actor_role\":\"LIVE_OPS\",\"payload\":{\"package\":\"marktangle-reversion\",\"approved_by\":\"<operator>\",\"reason\":\"<why it is over>\"},\"schema_version\":1}"}}
   // MARKTANGLE-2: ADOPTS the contract already in production and closes both tracks
   {"type":"env","set":{"EXPERIMENT_OS_EXPERIMENT_COMMAND":"{\"command_id\":\"mkt2-closeout-1\",\"action\":\"CLOSE_OUT_RETROSPECTIVE\",\"actor\":\"claude-code\",\"actor_role\":\"LIVE_OPS\",\"payload\":{\"package\":\"marktangle-2\",\"approved_by\":\"<operator>\",\"reason\":\"<why it is over>\"},\"schema_version\":1}"}}
   ```

   `actor_role` on those two reads `LIVE_OPS` above because that is the role the verb
   was built for. `TASK_SPECIFIC` is equally admissible and is what the MARKTANGLE
   close-outs actually used, on an explicit operator direction recorded in WS-013:
   `RESEARCH_LAB` is refused here by design — the session that ran an experiment should
   not also write down its own verdict — so a Research Lab session that has been told to
   close its own work submits under `TASK_SPECIFIC` and says so in the workstream. Do
   not put `RESEARCH_LAB` in the envelope to make it fit; the transport refuses it, and
   a receipt naming the wrong role is worse than a second session.

   Those two are **not** the same shape, and the difference matters if either is ever
   re-run: `marktangle-reversion`'s close-out registers its contract first, like
   PERP-V1's; `marktangle-2`'s refuses outright unless the experiment is already
   registered, because its contract is in production and authoring a second one
   beside it would carry the verdicts away from the objects they belong to.

   `CLOSE_OUT_RETROSPECTIVE` exists because PERP-V1 ran a full probe lifecycle
   **unregistered** — correct at the time, since registering redeploys the worker
   and a probe that cannot trade had no reason to force that — and the system was
   then unable to record the one true thing: it happened, and it is over. Its
   documents were the only durable record, which is the fragmentation Experiment OS
   exists to prevent. The gap is general: any experiment that runs outside and
   finishes hits it.

   It is **atomic on purpose.** `REGISTER_PACKAGE` alone would leave a closed,
   failed experiment sitting in production as an ACTIVE PROBE with open,
   never-evaluated gates — the Control Tower would show a dead experiment as live
   research, which is worse than the documents-only state. Either the whole retired
   record exists or nothing does.

   It **authorizes nothing, structurally**: `service.close_out_retrospective`
   refuses a PASS verdict outright (a verdict computed by hand, outside the system,
   after the fact, may never be what permits a promotion), refuses any target but
   RETIRED, and refuses an experiment holding a **tagged** deployment — that is a
   MIGRATION with evidence to reconstruct, not an outside-the-system record. It asks
   about `strategy_tag`, the join key into `paper_trades.strategy` and
   `live_orders.strategy`, rather than about deployment rows: a deployment whose arms
   are all untagged has no key anything could have traded under, so it is ended as
   part of the retirement instead of refused. Both MARKTANGLE experiments register a
   deliberately tagless probe deployment, and refusing on the row alone made the verb
   unreachable for exactly the case it exists for. The transport re-checks
   both properties after the package returns rather than trusting it. Results are
   stamped `computed_by=retrospective:<actor>` so they never read as the evaluator's.

   It is **not** `import_legacy_experiment`: that is for PRE-cutover history and
   would mark post-cutover work grandfathered, which the Legacy Migration role is
   told never to do. And Research Lab may not author it — the session that RAN an
   experiment should not be the one that writes down its own verdict.

   `ARM_CANARY` still **places no order**: `LIVE_STRATEGIES` is a separate switch
   this transport cannot reach, and every structural refusal in `arm_live_canary`
   (fresh tags, a twin at the same instant, a pre-registered risk envelope, a
   fresh synchronous re-evaluation of the promotion gate) fires unchanged. Clear
   the variable once the receipt is terminal. Same publicity warning as above.

   The **runtime allowlist** step that follows arming is not this transport's and
   never will be. It is an `env` call, and for an mmsell book it must first CREATE
   the book: a live mmsell book is an ordinary `MMSELL_VARIANTS` entry
   (`Lmmsell8` and `Lmmsell10` both are), so `LIVE_STRATEGIES=<tag>` on its own
   names a book that does not exist — no orders, and `book_params` absent against
   the deployment's declared value, which is recorded as
   `EXPERIMENT_CONFIG_DRIFT`. **Never hand-compose `MMSELL_VARIANTS`:** it is one
   ~800-character string holding every book, and retyping it to add one entry is
   how a running book gets dropped. Read the current value, then compose:

   ```bash
   {"type":"env","id":"env-1"}                        # what is the service running?
   python scripts/mmsell10_canary.py activate         # prints the exact env request
   ```

   That command applies nothing — no database connection, no Railway credentials,
   and it ignores `--execute`.

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

   **Three Railway services, one channel.** `env` and `logs` requests accept a
   `"service"` field selecting which service to act on — `"main"`/`"live"` (default,
   the `BOT_MODE=live` trading worker), `"evo"` (the `BOT_MODE=evo` evolutionary-agent
   worker), or `"livedash"` (the read-only live-vs-paper dashboard). So the evo bot's
   logs/config are reachable the same way as the main bot's:
   ```jsonc
   {"type": "logs", "service": "evo", "limit": 120, "id": "evo-logs-1"}
   {"type": "env",  "service": "evo", "id": "evo-env-1"}                    // read evo vars
   {"type": "env",  "service": "evo", "set": {"EVO_MAX_ACTIVE_AGENTS": "5"}, "id": "evo-cad"}
   {"type": "logs", "service": "livedash", "limit": 80, "id": "dash-logs-1"}
   ```

   `livedash` exists because the dashboard was the one deployed service no session
   could see: a failed deploy, a crash loop or a startup error produced no signal
   anywhere, so the only way anyone learned it was broken was by opening it and
   finding it broken (WS-009 D3).

   Each service's ID lives in a secret (`RAILWAY_SERVICE_ID` for main,
   `RAILWAY_EVO_SERVICE_ID` for evo, `RAILWAY_LIVEDASH_SERVICE_ID` for the dashboard
   — never in this public repo). A service whose secret is unset answers with a
   message naming the secret to add, not an obscure lookup failure. `db` requests are
   **service-agnostic** (all services share one Postgres via `DATABASE_URL_RO`).

   Note that main's secret, `RAILWAY_SERVICE_ID`, is *also* the variable the runner
   writes to aim the Railway helpers at a service. A request that visits several
   services in one process (`doctor`) therefore cannot read main's ID back out of it
   after selecting something else; `ops_runner._main_service_id` remembers the
   pristine value instead. Anything that adds a multi-service reader must go through
   `_select_service` rather than setting `RAILWAY_SERVICE_ID` itself.

   **The workflow file is loaded from the `ops` branch.** Adding a service means
   adding its `env:` passthrough to `.github/workflows/ops-runner.yml` on `ops` as
   well as on the default branch — a plain fast-forward commit is enough for an
   additive change like this; the force-push procedure below is only for a rewrite.

   **Ask the channel what it can do, rather than trusting this document.** Prose
   drifts from allowlists — that is XOS-000005, where two commands the runbook
   advertised were refused in production for weeks. `capabilities` is generated
   from the live allowlists themselves, and the docs/runner parity tests read the
   same generator:

   ```jsonc
   {"type":"capabilities","id":"cap-1"}                 // human-readable
   {"type":"capabilities","format":"json","id":"cap-2"} // machine-readable
   ```

   It reports the SHA of the code serving the request, every request type with
   its READ/MUTATING classification, which services are configured (never their
   IDs — those are secrets), the `xos` and script allowlists, every settable
   variable with the redacted and durably-audited ones marked, and the limits.

   **`doctor` — one request instead of six.** Establishing operating context used
   to take a handful of round trips at ~60s each, re-derived from prose every
   session, which is how two sessions ended up with different pictures of the same
   production system:

   ```jsonc
   {"type":"doctor","id":"doc-1"}
   ```

   Runner freshness and code SHA · read-only DB connectivity, the last `bot_runs`
   cycles and the recent ERROR count · Railway reachability and latest deployment
   per configured service · the non-secret critical runtime config (`KILL_SWITCH`,
   `LIVE_ENABLED`, `LIVE_STRATEGIES`, exposure caps, enforcement mode) · and
   Experiment OS `enforcement`, `readiness` and open issues read through the
   **canonical CLI**, not a re-implementation. A subsystem that cannot answer is a
   WARNING in the report; the request still succeeds, because a snapshot with one
   dead subsystem is exactly the snapshot you need.

   **`incident` — the bundle you would have assembled by hand.**

   ```jsonc
   {"type":"incident","service":"main","window_minutes":30,"id":"inc-1"}
   ```

   Runner and deployment identity · service reachability · bounded recent logs ·
   `system_events` at WARNING+ over the window · live orders, paper trades and
   risk refusals over the window · `control-tower` and `issue-candidates` from the
   canonical CLI. Every section is capped: summaries and identifiers, never a raw
   dump. A finding here is **not durable state** — anything real belongs in an
   Experiment OS issue.

   **Provenance and receipts.** Any request may carry `actor`, `purpose`,
   `workstream` and `issue`. They are echoed in the result header and recorded in
   `ops/results/<id>.receipt.json` beside the output: request type and
   classification, provenance, start/end, the serving code SHA, target service,
   the command's exit status and where the result landed. They are **labels, not
   authority** — the allowlists decide what is permitted, never who claims to be
   asking. Receipts are pruned with the results they belong to; the ones that
   matter are archived (below).

   **A mutation must be unmistakable.** `{"type":"env"}` and
   `{"type":"env","set":{…}}` used to differ by one key despite differing entirely
   in authority. Say which one you mean:

   ```jsonc
   {"type":"env","action":"get","id":"env-1"}
   {"type":"env","action":"set","values":{"KILL_SWITCH":"true"},"id":"kill-1"}
   ```

   The legacy `"set"` spelling still works and means the same thing. An ambiguous
   request — `action:"get"` carrying values, `action:"set"` carrying none — is
   **refused**, because the only safe reading of "I cannot tell whether you meant
   to change production" is to stop.

   **Change, then verify.** A mutation shouts in the first line of the result,
   records the BEFORE state, applies, reports the redeploy outcome, reads the
   state BACK, and ends in one verdict:

   | verdict | means |
   |---|---|
   | `VERIFIED` | every target reads back as requested |
   | `APPLIED_BUT_UNVERIFIED` | the writes were accepted but the readback could not confirm them — **do not act on it as done** |
   | `FAILED` | at least one write was refused |

   When the mutation touched Experiment OS or live-strategy state, the canonical
   `enforcement` and `readiness` reads run afterwards and print with it. The
   runner holds no opinion of its own about health: it asks the canonical readers
   and shows what they said. `"verify": false` skips the readback; `"redeploy":
   false` applies without restarting the service.

   **Durable audit of production changes.** `ops/results` is bounded scratch — 80
   files deep, and a busy afternoon can push a live arm off the end of it within
   hours. Receipts for changes to real-money capability, the risk envelope around
   it, or the Experiment OS write transports (`ops_meta.AUDIT_WORTHY_VARS`, decided
   in code and asserted by tests) are therefore appended to the long-lived
   **`ops-audit`** branch under `receipts/<timestamp>-<id>.json`, in the same
   spirit as `digest-archive`: never merged, never deployed, only ever grows.
   Receipts only — request payloads are not copied there. An ATTEMPTED change that
   failed is archived too; "someone tried to arm this and it was refused" is
   history.

   **A failed request turns the run red.** The runner's exit status is captured,
   the result is published either way, and the workflow then re-raises the
   failure. Green means the request succeeded; a publication failure fails
   separately and loudly. (Before this, a failed request published its error and
   left the run green — indistinguishable from success to anything reading run
   status.)

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

   - **"series pnl"** -> `{"type":"script","name":"mmsell_series_pnl"}` — the standing
     per-series LOSS DETECTOR (`docs/MMSELL_NFLSPREAD_LOSS_CELL.md` §8). Every traded series
     ranked by realized P&L. It exists because nothing read per-series P&L and `KXNFLSPREAD`
     therefore gave back a third of the family's 30-day paper P&L, in one graduated series, and
     was found by accident: `mmsell_market_types` aggregates 400 series into 15 contract types,
     `mmsell_universe_review` ranks by coverage, and no gate scores a series at all. **Read
     `edge` (`be% − loss%`), never the raw loss rate** — each series is entered at a different
     premium, so only `edge` is comparable across them, and `edge <= 0` means the cell is not
     paying for its tail. **Read `contests`, not `mkts`** — one game carries a nested ladder that
     a blowout settles against a seller at one instant, so markets are not independent bets.
     `worst3%` — of everything the cell lost at the contest level, the share its three worst
     contests carried — separates "two catastrophic afternoons" (a concentration problem, which
     the contest cap addresses) from a broad negative drift (a selection problem, which it does
     not), and the
     `live` column names the real-money books that touched the cell. Run it weekly with
     `--days 7` and again with `--all-time --min-n 50`: the two disagree exactly when a cell is
     NEW, which is the case this exists to catch. **It is a report, not a gate** — at this book's
     variance a per-series P&L gate would fire constantly on noise (`docs/MMSELL_ROADMAP.md` §1),
     and acting on a cell is a universe change to a running book, i.e. a new epoch or Version
     under `NEW_ONLY`.
     `{"type":"script","name":"mmsell_series_pnl","args":["--series","KXNFLSPREAD","--maxyes","7"]}`

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

   - **"forward path"** / **"toxic flow"** -> `{"type":"script","name":"theta_forward_path"}`
     — what the underlying did AFTER each theta decision, joined from retained 1-minute closes:
     forward return at +5m/+15m/+30m/close, plus maximum favourable and adverse excursion
     oriented by the side the book SOLD. **Read section 1 (COVERAGE) first and treat anything
     below ~95% as unusable**: missing rows cluster around feed outages, so dropping them
     silently selects on the regime being studied. Retention makes this join possible; this
     script is what proves it complete.

   - **"candle depth"** -> `{"type":"script","name":"theta_candle_backfill_probe"}` — how far
     back Coinbase serves 1-minute candles, i.e. whether a fit window can be backfilled from
     history or has to accumulate forward. Touches no database. Measured 2026-08-21: at least
     365 days for BTC-USD and ETH-USD.

   - **"settlement labels"** / **"are the outcomes trustworthy"** ->
     `{"type":"script","name":"theta_settlement_labels","args":["--spot-source","coinbase","--kalshi-results"]}`
     — audits the DERIVED outcome label every theta calibration rests on. The label is the last
     ladder snapshot's spot against the strike, which is not Kalshi's settlement print: Kalshi
     settles off its own index at the close, up to three minutes later. Measures the residual
     move scale from the spot series, applies a fixed near-strike exclusion, and reports
     agreement against recorded settlement with **event-clustered** intervals.
     `--kalshi-results` fetches Kalshi's OWN settled results per event from the public endpoint
     and reports how much of the population they cover — measured at **100%**, which is why the
     refit no longer scores against the derivation at all.
     **Read the VERDICT line.** The bar applies to the one-sided LOWER bound, not the point
     estimate: a percentile bootstrap on an all-agreeing sample returns `[1, 1]` because no
     resample of successes can contain a failure, so the interval is an exact clustered
     Clopper-Pearson one. `BLOCKED_DATA` means no calibration computed against those labels is
     validated — including one that already reports a verdict. Use `--spot-source coinbase`
     (default); the ladder reconstruction is ~5-minute sampled and cannot measure a 1-3 minute
     move scale (it reported a 4-minute RMS ETH move of $0.20).

   - **"taxonomy audit"** / **"what are the unknown series"** ->
     `{"type":"script","name":"mmsell_taxonomy_audit","args":["--top","200","--dump-text"]}`
     — the candidate census by settlement mode AND the Platform Change Review package for every
     unclassified series prefix. `markets` holds no row for these tickers, so the evidence is
     fetched from Kalshi's public market-data endpoint (no key). Proposes a mode only on a
     STRONG signal — Kalshi's settlement source or rules text — and returns
     `INSUFFICIENT_EVIDENCE` otherwise; `--dump-text` prints Kalshi's own words verbatim so a
     human can decide the rest. **It edits no `SERIES_TYPES` entry.** The census it runs is the
     same function that must be re-run after a repair, so "rerun the exact same census" is one
     command. Note `can_close_early` is reported and votes on nothing: Kalshi sets it on 100% of
     these markets, index-close ones included.

   - **shadow cost, synthetic** -> `PYTHONPATH=. python3 scripts/theta_shadow_bench.py` —
     **local, not an ops script, and it does NOT measure PostgreSQL.** Fits, cache behaviour and
     memory only; the "load" line is in-process object construction, so the total is a LOWER
     BOUND on production. Run it after any change to the model or its cadence.

   - **shadow cost, production** -> `{"type":"logs","service":"main","filter":"theta: shadow cost"}`
     — one line per cycle carrying `theta_shadow_ms` (TOTAL: load + decode + construction +
     fits), `theta_shadow_load_ms`, `theta_shadow_loads`, `theta_shadow_fits`. **This is the only
     number that may be called production-derived.** Report p50/p90/p99/max over several hourly
     reloads before treating `theta_spliced_budget_ms` as anything but a synthetic figure, and
     remember a budget below the measured maximum is not a backstop — it is an hourly gap in the
     research series.

   - **"theta refit"** / **"tail model validation"** -> `{"type":"script","name":"theta_tail_refit"}`
     — scores the replacement probability model (`kalshi_bot/theta/tailmodel.py`) against the
     incumbent, strictly out of sample. Reports degeneracy (what fraction of each model's output
     is exactly 0 or 1), calibration by probability bucket with the sub-2% region broken out,
     a `tail_q` sweep chosen on TRAIN and scored on TEST, the **paired** proper-score comparison
     between the two models, a **direct** event-clustered test of the SELECTED-vs-REJECTED
     contrast under each model's own excess, and fit health.
     **Section 0 is a gate.** `--labels kalshi` (the default) scores against Kalshi's own settled
     results, fetched per event, which cover 100% of this universe; `--labels derived` reproduces
     the old last-snapshot-spot proxy and its near-strike exclusion, kept because the record has
     to be able to reproduce what earlier runs scored. `BLOCKED_DATA` there means nothing below it
     is a validated calibration result.
     **Section 6 is the comparison.** The per-model R columns in sections 3 and 5 describe two
     different partitions and are not a test of a difference — aggregate R also rewards
     predicting exactly zero, which is the incumbent's shape. Every interval is an
     **event-clustered** bootstrap: a crypto ladder settles all its strikes against one spot
     print, so a Poisson interval over markets is ~2.3x too narrow (measured design effect ~5).
     `--seed` is fixed so a recorded run reproduces its own intervals exactly.
     **Section 7 is the selection test.** The estimand is `log(R_selected / R_rejected)`, and it
     is bootstrapped as one quantity — whole events resampled from the combined eligible
     population, both groups recomputed inside every replicate, so the covariance between them is
     retained. The two groups' separate intervals in the table above it are descriptive; they are
     NOT the test, and non-overlap of them proves nothing. A Haldane–Anscombe `c = 0.5` is added
     to both observed counts uniformly (the uncorrected point estimate is printed beside the
     corrected one); a replicate drawing zero *expected* events in either group is invalid and
     dropped, and the run declines to report an interval if too few survive. Read
     `valid_replicates` and the per-group event coverage before the verdict.
     **Read the `powered` counts before anything else.** A fit below
     `MIN_TAIL_EXCEEDANCES_FOR_POWER` declustered exceedances ON THE TAIL THE STRIKE USES is a
     resolution floor, not an estimate, and the calibration/selection sections exclude those
     rows. Configuration (`--fit-days` x `--tail-qs`) is chosen on TRAIN and scored **once** on
     TEST; the split is enforced by control flow, so a TEST number cannot be reported for a
     configuration that was not frozen first. `--spot-source coinbase` (default) fetches true
     1-minute closes from the public endpoint — deep enough for a 90-day fit window and writing
     nothing; `candles` uses only the retained window; `ladder` reaches further back at
     ~5-minute sampling, which is too sparse to build blocks from.
     Findings: `docs/RESEARCH_THETA_REMEDIATION.md`.

   - **"theta A/B replay"** / **"Stage-4 floors"** -> `{"type":"script","name":"theta_ab_replay"}`
     — replays the PROPOSED control and treatment selection rules over ONE common eligible
     candidate stream, on Kalshi's settled results, and derives the A/B's evidence floors from
     what it measures. Exists because floors sized from a rule's *historical* selected set cannot
     size an experiment that runs a different rule.
     Reports per arm: eligible candidates and events, overlap between arms, markets/event,
     expected and observed losses, expected-loss rate per market and per event, candidate cadence
     per day, event-clustered design effect, sample requirement, calendar time and horizon.
     **The floor is conditional on the control's R** and the run prints a sensitivity across
     R_C ∈ {replayed, 2.0, 1.0} — register the CONSERVATIVE row, not the flattering one.
     **Section 4 is descriptive, not a result.** The rules were chosen after seeing this window,
     so the replay's own `log(R_T/R_C)` cannot promote or reject anything; it is printed so the
     effect is not rediscovered later and mistaken for news. The arms OVERLAP, so it uses
     `cluster_stats.arm_contrast_ci` (resamples the union of events, a market can be in both) —
     `ratio_contrast_ci` would be wrong here because it partitions.
     It runs the frozen spliced configuration only; it does not sweep or choose a model.
     Findings: `docs/RESEARCH_THETA_REMEDIATION.md` §4.2.3.

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
3. Commit and push — **with a retry loop, always**. `ops` is a shared transport and
   several sessions push to it concurrently, so a bare `git push` loses the race
   often enough to matter. The runner's own publish step retries six times with
   backoff; the requester side needs the same courtesy, and a rebase is always safe
   because your request commit touches one file nobody else is editing:
   ```bash
   cd /tmp/ops && git add ops/request.json && git commit -q -m "ops: <what>"
   for i in 1 2 3 4 5 6; do
     git fetch origin ops -q
     git rebase origin/ops -q 2>/dev/null || git rebase --abort 2>/dev/null
     git push -q origin ops 2>/dev/null && { echo "submitted"; break; }
     sleep $((i * 3))
   done
   ```
4. Read the result. The runner commits your output back to the `ops` branch as the
   durable per-run file `ops/results/<id>.txt` (and updates the shared `ops/result.txt`
   pointer). Plain git is the simplest path (works even when the GitHub MCP tools are
   down) — read **your own `id`** so a concurrent producer's run can't shadow yours:
   ```bash
   # poll until YOUR result lands, then:
   for i in $(seq 1 20); do
     sleep 15; git fetch origin ops -q
     git show FETCH_HEAD:ops/results/<id>.txt 2>/dev/null && break
   done
   # (git show FETCH_HEAD:ops/result.txt is the latest-run pointer — fine when you're
   #  the only producer, but it can be overwritten by a concurrent /loop run.)
   ```
   **Budget several minutes, not seconds.** A single run is ~30–90s, but GitHub
   queues Actions runs: with several sessions active your request waits behind
   theirs, and a poll window sized for the idle case reports a false "not ready"
   for a request that is merely in line. Two things follow from that, and both
   have bitten:
   - a missing `ops/results/<id>.txt` does **not** mean your request was lost.
     Check `git log origin/ops` for a `ops: result <id>` commit before resubmitting;
   - `ops/results/` is **bounded scratch — the newest 80 files, pruned on every
     run**. A busy afternoon can age your result out from under a slow poller. If
     you need output to survive, read it promptly and copy what matters into the
     durable place it belongs (a ticket, a doc, a PR body), not into a link to a
     scratch file.
   Alternatively via the GitHub MCP tools:
   - `actions_list` `method=list_workflow_runs`, `resource_id=ops-runner.yml` → newest run id
   - `actions_list` `method=list_workflow_jobs`, `resource_id=<run id>` → job id
   - `get_job_logs` `job_id=<job id>` `return_content=true` → the output is the query
     table or the log lines
5. Reset `ops/request.json` to `{"type": "noop"}` and push (leave the channel idle).

Notes:
- **Never open a PR merging `ops` into the default branch.** GitHub auto-deletes
  the branch on merge, which removes the trigger.
- **Never force-refresh `ops`, and never delete it.** Changes to `scripts/` —
  `db_query.py`, `railway_logs.py`, `ops_runner.py`, any allowlisted analysis
  script — are picked up automatically: the runner checks the **default branch**
  out into `.ops-runner-code/` and executes only that copy (XOS-000005), so a
  merge to the default branch is live on the next request. `ops` carries the
  request/result history and the workflow *file*, nothing else. See
  **Protecting the `ops` branch** below for the protection now enforcing this and
  for the rare, deliberate procedure that changes the workflow file.
- Latency is ~30–60s per run when the channel is idle, and considerably more when
  it is not — see step 4. Request commits live only on `ops`; they never touch the
  default branch and never redeploy the Railway worker. (An `env` **mutation** does
  redeploy it — that is the mutation, not the transport.)

### Sharing the channel with other sessions

Everything below is a consequence of one fact: `ops` is a shared transport and you
are usually not its only producer. What is already handled for you, and what is not:

| surface | concurrent-safe? | what you do |
|---|---|---|
| `ops/results/<id>.txt` | yes — per-run files, publish retries 6× | always set a unique `id`; read your own file |
| `ops/result.txt` | **no** — latest-run pointer, freely overwritten | do not rely on it |
| pushing `ops/request.json` | **no** — one ref, one file | use the retry loop in step 3 |
| result retention | bounded — newest 80, pruned every run | read promptly; persist what matters elsewhere |
| `EXPERIMENT_OS_*_COMMAND` | guarded — see below | batch your commands; heed a `REFUSED` verdict |

**The command transports are single-slot.** `EXPERIMENT_OS_ISSUE_COMMAND`,
`EXPERIMENT_OS_PLATFORM_COMMAND` and `EXPERIMENT_OS_EXPERIMENT_COMMAND` are each
one variable, consumed at the worker's next boot. If you set one while another
session's envelope is still waiting for its boot, that envelope is discarded —
never claimed, never executed, no receipt. The ledger cannot catch this: it
answers "did this `command_id` run", and it is only ever asked afterwards.

So `apply_set` now checks before it writes (`scripts/ops_command_guard.py`). It
reads the slot's current value, takes every `command_id` in it, and asks that
transport's receipt ledger whether each has reached a terminal state
(`SUCCEEDED`, `REJECTED`, `FAILED`). Anything with no row, or still `RUNNING`, is
unconsumed and the whole request is **REFUSED** before any write or redeploy:

```
# UNCONSUMED COMMAND: EXPERIMENT_OS_ISSUE_COMMAND still holds 1 command(s) with
#   no terminal receipt: lo-adopt-20260821. ...
# VERDICT: REFUSED
```

That is a real signal, not a glitch — wait for the other session's receipt
(`{"type":"xos","command":"issue-command-show","args":["<command_id>"]}`) and
resubmit. Override only when you know the envelope will never be consumed (an
abandoned session; nobody is going to redeploy for it) by adding
`"force_replace": true` to the request. The override is recorded in the receipt,
along with what it discarded.

The guard **fails open**: no database, an unreachable ledger, an unparseable
current value — all of these allow the write, because this sits in front of the
only authorized production write path and a closed failure would be a worse
outage than the race. It never fails open *silently*, though: an inconclusive
check prints `# guard INCONCLUSIVE:` and says so in the receipt, so "checked and
clear" can never be mistaken for "could not check".

### Protecting the `ops` branch

`ops` is not an ordinary branch. It is a shared transport: it carries every
session's `ops/request.json`, the durable per-run `ops/results/*.txt` files that
let concurrent producers each read their own output, and the Ops Runner
**workflow file** GitHub Actions loads for a push to this branch. A force-push
rewrites all three at once — discarding results, clobbering another session's
in-flight request, and potentially reinstating an older workflow file. That is
XOS-000007.

**`refs/heads/ops` is protected by a repository ruleset** (`ops-transport-guard`,
`.github/rulesets/ops-transport-guard.json`, applied by the
`Ops Branch Protection` workflow — which re-applies the checked-in desired state
when it merges to the default branch, and can be re-run from the Actions tab at
any time; a push on any other branch only *reports* the current protections):

| rule | effect |
|---|---|
| `deletion` | the branch cannot be deleted |
| `non_fast_forward` | force pushes (any non-fast-forward update) are rejected |

Deliberately **not** included: pull-request review, required status checks,
linear history, signed commits, or a push restriction on actors. Any of those
would break the transport — ordinary request commits and the runner's own result
commits are unauthenticated-by-review fast-forwards and must keep landing
directly. If a change to this ruleset ever blocks a normal request or a result
commit, **narrow the ruleset**; do not remove it.

**Applying it needs a temporary admin token.** Repository rulesets are
administration-scoped and Actions' built-in `GITHUB_TOKEN` cannot hold that
permission, so the workflow reads a fine-grained PAT from the `OPS_ADMIN_TOKEN`
repository secret. Scope it to this repository only, grant only
*Administration: read and write* (plus GitHub's unavoidable implicit metadata
access), and use the **shortest practical expiration**.

The token is an application credential, not standing infrastructure. After the
ruleset is applied, an ordinary request/result round trip succeeds, and a
controlled non-fast-forward update is refused, **delete the secret and revoke the
PAT**. The branch protection remains active after the token is removed. If the
checked-in desired state changes later, deliberately install a new temporary
token, apply and validate, then remove it again. Without the secret an apply run
still reports the current protections and then **fails loudly** — it never reports
success over an unprotected branch. Nothing else in the repo uses that secret.

Ordinary work is unaffected: pushing a request, pushing a result, and the
runner's `ls -1t ops/results/*.txt | tail -n +81 | xargs rm` pruning (a file
deletion inside a normal commit, not a branch deletion) all remain fast-forward
updates.

#### The rare exception: changing the workflow file

An ORDINARY change to the workflow file — adding a step, adding a secret
passthrough, fixing the failure-status handling — is a **plain fast-forward
commit on `ops`**, not a rewrite. The `ops-transport-guard` ruleset blocks
deletion and non-fast-forward updates only, so committing a new
`.github/workflows/ops-runner.yml` onto the tip of `ops` is allowed and preserves
every in-flight request and result. Do it while the channel is idle (step 1
below), then validate with a real round trip (step 4 below). The rewrite
procedure that follows is only for the case where the branch's history itself
has to be replaced.

Note that the workflow, `ops/README.md` and the request/result files are the ONLY
things on `ops` that matter; the runner's code always comes from the default
branch, so merging a change to `scripts/ops_runner.py` needs nothing done to this
branch at all.


The only remaining reason to rewrite `ops` is a change to
`.github/workflows/ops-runner.yml` itself, which Actions loads from the
triggering branch. That is maintenance, not routine, and it follows this
procedure exactly.

1. **Idle channel.** Confirm the transport is quiet before touching it:
   ```bash
   git fetch origin ops -q
   git show origin/ops:ops/request.json     # must be exactly {"type":"noop"}
   ```
   Also confirm no run is in flight (`actions_list` on `ops-runner.yml` shows no
   queued/in-progress run). A rewrite over a live request silently drops it.
2. **Backup branch at the current SHA.** A branch ref, not a tag — tag pushes are
   rejected by the sandbox's git transport:
   ```bash
   OPS_SHA=$(git rev-parse origin/ops)
   git push origin "$OPS_SHA:refs/heads/ops-backup-$(date -u +%Y%m%dT%H%M%SZ)"
   ```
   Record `$OPS_SHA` in the change's Experiment OS issue before proceeding.
3. **Explicit expected-SHA lease.** Never plain `-f`. Lease against the exact SHA
   recorded in step 2, so a concurrent push aborts the rewrite instead of losing it:
   ```bash
   git push --force-with-lease=refs/heads/ops:"$OPS_SHA" origin <new-tip>:ops
   ```
   The `non_fast_forward` rule refuses this too, so a maintainer must lift the
   ruleset for the window (disable `ops-transport-guard`, or add themselves as a
   bypass actor) and **re-enable it in the same session**. Install a temporary
   `OPS_ADMIN_TOKEN` using the scope and expiration rules above, then re-run the
   `Ops Branch Protection` workflow to re-apply the ruleset idempotently. After
   the post-change validation succeeds, delete the secret and revoke the PAT.
4. **Post-change validation.** A rewrite is not finished until a real request has
   round-tripped. Note that a force-push onto a commit that already exists in the
   repository does **not** trigger the workflow — the push event carries no
   changed files for the `ops/request.json` path filter — so the validation must
   be a genuine new commit:
   ```bash
   # a harmless request that exercises the full path
   echo '{"type":"noop","id":"ops-maint-<date>"}' > ops/request.json
   git add -A && git commit -q -m "ops: post-maintenance validation" && git push origin ops
   # ~30-90s later
   git fetch origin ops -q && git show FETCH_HEAD:ops/results/ops-maint-<date>.txt
   ```
   Then confirm the runner still sourced its code from the default branch: the
   job log must show the `.ops-runner-code` checkout and the
   `OPS_RUNNER_CODE_SOURCE=default-branch` attestation the runner fails closed
   without. Finally reset `ops/request.json` to `{"type":"noop"}`.
5. **Recovery.** If validation fails, or the rewrite landed the wrong tree:
   ```bash
   # restore the exact pre-change tip from the backup branch
   git push --force-with-lease origin "ops-backup-<stamp>:ops"
   ```
   with the ruleset still lifted, then re-run the `Ops Branch Protection`
   workflow, then repeat step 4. If `ops` is missing entirely, recreate it from
   the backup branch — **not** from a feature branch, which is how a stale
   workflow file gets reinstalled:
   ```bash
   git push origin "ops-backup-<stamp>:refs/heads/ops"
   ```
   Keep the backup branch until the next successful maintenance; delete stale
   ones only after a validated round trip.

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
`RAILWAY_SERVICE_ID` (main/live worker), `RAILWAY_EVO_SERVICE_ID` (the evo
worker — enables `{"service":"evo"}` env/logs requests), and
`RAILWAY_LIVEDASH_SERVICE_ID` (the live-vs-paper dashboard — enables
`{"service":"livedash"}`). Human setup instructions are in `docs/REMOTE_ACCESS.md`.

To add a service ID: Railway → the service → Settings, copy its service ID; then
GitHub → the repo → Settings → Secrets and variables → Actions → New repository
secret. It goes in **GitHub**, not Railway: the ops runner is a GitHub Actions
workflow, and the ID names which Railway service that workflow should query.

### Gotchas

- Railway's API is behind Cloudflare, which 403s the default `Python-urllib`
  user-agent (error 1010). `scripts/railway_logs.py` sends a browser User-Agent
  to get through — **keep it.**
- `scripts/db_query.py` matches write/DDL keywords on **word boundaries**, so
  columns like `created_at` / `updated_at` are fine in a read-only query.
