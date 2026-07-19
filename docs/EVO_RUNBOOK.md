# Evolutionary Agent System — Operating Runbook

Plain-language instructions for running the agent population. Design details live in
`docs/EVOLUTIONARY_AGENT_SYSTEM.md`; every knob is listed in `docs/EVO_CONFIG.md`.

## What this is, in one paragraph

Thirty named AI agents each manage an independent **paper** portfolio of Kalshi
trades ($1,000 each, simulated fills against real market data — no real orders are
possible from this mode). They wake up on heartbeats six times a day plus one deep
reflection, research, build declarative strategies, set deterministic listeners,
learn from each other, and revise how they think and trade. Exactly one week after a
cohort is born the week's cohort is scored; the bottom 30% retire permanently, the top
30% produce children that inherit their knowledge and try to improve on it, and every
fourth cohort one "wildcard" founder with a fresh surname joins. Everything is
recorded, versioned, budgeted and auditable.

## Turning it on (one-time setup)

1. **Create a second Railway service** on this same repo and database (the evo loop
   must not share a process with the trading worker):
   - Same repo, same `DATABASE_URL` and Kalshi credentials as the existing worker.
   - Set these env vars on the NEW service only:
     - `BOT_MODE=evo`
     - `SCAN_INTERVAL_SECONDS=60` (the evo cycle cadence)
     - `ANTHROPIC_API_KEY=<your key>` — **this is the on-switch for agent thinking.**
       Without it everything still runs (listeners, fills, settlements, scoring) but
       heartbeats are journaled as "degraded" and agents make no decisions.
   - Deploy. The start command is unchanged (`alembic upgrade head && python -m
     kalshi_bot.main`); the migration creates all `evo_*` tables automatically.
2. That's it. On the first cycle the service snapshots its config, seeds model
   prices + the strategy graveyard + the data-source registry, opens cohort #1, and
   creates 30 founder agents with unique surnames, equal capital and equal budgets.

The existing weather/mmsell worker is completely untouched — do not change any env
var on it.

## Costs

- Per-agent LLM ceiling: **$2/week** (hard stop, enforced before every call).
- Worst case 30 agents ≈ $60/week ≈ $260/month. Typical usage is expected well
  below the ceiling: routine heartbeats run on a Haiku-class model with ~2k output
  tokens; only reflections, births, cohort-ends and retirements use the stronger
  model. Actual spend per agent/family/cohort is in the digest and `evo_llm_usage`.
- Model ids and prices live in the `evo_model_prices` table (and `EVO_MODEL_ROUTINE`
  / `EVO_MODEL_DEEP` env vars) — update the row, not the code, when pricing changes.

## Watching it (dashboard)

Through the existing ops channel (`ops` branch → GitHub Actions), same as PnL/digest:

- `{"type": "script", "name": "evo_digest", "id": "evo-1"}` — cohort status, time
  remaining, heartbeat health, LLM spend, the 6h-delayed leaderboard with projected
  top/middle/bottom groups, per-agent portfolios, activity, family concentration,
  the ticket review queue, and an ANOMALIES section (lead with it — "all clear"
  means healthy).
- `{"type": "script", "name": "evo_tree", "id": "tree-1"}` — the family tree
  (lineage) and the influence graph (who copied what from whom).
- Ad-hoc SQL through the normal `{"type": "db"}` request against any `evo_*` table
  (schema: `kalshi_bot/evo/models.py`).

Suggested cadence: run `evo_digest` in the same daily habit as the weather digest.

## The phone dashboard (v0.1)

A read-only web page you can open from your phone — the same numbers as the digest,
but always live and tappable. It runs as its **own third Railway service** so it
never shares a process with the trading worker or the evo worker, and it only ever
reads the database (no write path exists, so it cannot change any agent behavior).

**One-time setup:**

1. Create a **third Railway service** on this same repo + database.
2. Set env vars on it:
   - `DATABASE_URL` — the same Postgres URL as the evo worker. (That's the only
     required one. It needs **no** Kalshi keys and **no** `ANTHROPIC_API_KEY` — the
     dashboard never calls Kalshi or the LLM.)
3. Point this service at its own config-as-code file instead of the repo-root
   one. The repo root `railway.json` (start command `alembic upgrade head &&
   python -m kalshi_bot.main`) is what the trading worker and evo worker use —
   it applies to **every** service on this repo by default and overrides
   anything typed into the UI's Custom Start Command box (the box will show
   "The value is set in /railway.json" and ignore edits). `railway.dashboard.json`
   is a second config file at the repo root with the dashboard's own start
   command (`python -m kalshi_bot.dashboard`, no alembic). In the dashboard
   service's Settings, find the config-as-code file path setting (near the top
   of Settings, above Build/Deploy) and change it from `railway.json` to
   `railway.dashboard.json`, then redeploy. This way the worker's config file
   is never touched.
4. Enable a public domain on the service (Railway → Settings → Networking →
   Generate Domain). Railway injects `PORT` automatically; the server binds it.
5. Open the generated URL on your phone and bookmark / add-to-home-screen it.

**What it shows** (single scrolling page, auto-refreshes every 30s, plus a manual
Refresh button): a status pill + cohort countdown; six summary cards (active agents,
starting capital, cohort equity, cohort profit, completed trades, LLM cost vs
ceiling); a sortable agent table split into projected top/middle/bottom groups where
each row taps open to show thesis, open positions, fitness components, last-heartbeat
summary, strategy revision, lineage and remaining LLM budget; a filterable recent-
activity feed; the ten system-component statuses; and the open capability-request
queue.

**Safety:** the page is public (no login in v0.1), so the data layer deliberately
never exposes secrets, env vars, raw prompts, hidden reasoning, DB connection info,
or stack traces — only the structured operational fields above. It's read-only:
nothing on the page can change agent behavior. A unit test asserts the payload
carries no credential-shaped strings.

## The weekly rhythm (no action needed)

Everything below is automatic; the digest shows it happening:

- **One week after the cohort was born** — cohort boundary. Final marks, final scores,
  bottom 9 retired (liquidated, forever searchable), 12+9 survive with positions carried
  over and capital re-normalized to exactly $1,000, top 9 produce children (8 + one
  wildcard every 4th cohort), population back to 30, next cohort opens (its own week
  starting from that moment). Each cohort gets a full `EVO_COHORT_DAYS` (7) — the window
  is anchored to birth, not to a fixed calendar day.
- A cohort can never finalize twice; children can never be duplicated by retries;
  heartbeats can never run twice — all enforced by database uniqueness, not by hope.

## Telling the bots about a system change (announcements)

When you change how the system works — a config change, a new rule, a fixed bug —
you can broadcast it to the **whole population** so every agent learns it at once
instead of each having to rediscover it. Active announcements are injected near the
top of every agent's heartbeat prompt (marked as authoritative operator notices),
and also shown on the phone dashboard and in the digest.

There is no live write path (agents and the ops channel are read-only), so
announcing something matches the model-price / graveyard seeds — declare it in code
and deploy:

1. Add an entry to `ANNOUNCEMENTS` in `kalshi_bot/evo/announcements.py`:
   ```python
   dict(
       key="2026-08-new-data-source",          # stable, unique — the idempotency key
       title="Short headline the agents see first",
       category="system_change",
       body="Plain-language explanation of what changed and what to do about it.",
       expires_in_days=21,                      # 0 / omit = never expires
   ),
   ```
2. Deploy the evo service. On the next cycle `seed_announcements()` inserts it once
   (idempotent on `key`); every agent sees it on their next heartbeat.
3. To retire an announcement, let it expire (`expires_in_days`). Seeding is
   insert-only, so editing an already-seeded row's fields on a later deploy has no
   effect — pick the right `expires_in_days` up front, or add a superseding entry.

Announcements are operator-authored text only; they carry no secrets and never
change agent behavior directly (agents read them and decide what to do).

## Your one real job: the ticket queue

Agents cannot pay for data, add credentials, deploy infrastructure or change shared
code. When they need any of that they file a **capability ticket** and other agents
co-sign it. The digest's TICKETS section is the review queue, ordered by supporter
count. To decide one, update the row (via a normal write path — i.e., ask Claude in
a repo session, since the ops channel is read-only):

- approve/reject: set `evo_tickets.status` to `approved` / `rejected` and put your
  reasoning in `human_decision`; after building the thing, set `implemented` and
  fill `implementation_result`.

## Running a small population for testing

To confirm the pipeline end-to-end without paying for or exposing all 30 agents,
throttle how many run live: set **`EVO_MAX_ACTIVE_AGENTS=3`** on the evo service and
redeploy. Only the 3 lowest-id (earliest-created) agents then run heartbeats, place
paper trades, and get snapshotted/scored; the other 27 stay in the cohort, dormant,
untouched — nobody is retired and no history is lost. Watch those 3 via the digest
(heartbeats completing, trades filling, fitness computing). When satisfied, set
`EVO_MAX_ACTIVE_AGENTS=0` (or delete it) and redeploy — all 30 resume immediately.

Caveat: the cap throttles the per-cycle live work, not cohort **finalization** — at
the Monday-week boundary all active members (including the dormant ones) are still
scored/retired. Conclude testing, or lift the cap, before a cohort boundary if you
want the dormant agents judged on real activity.

## Pausing and emergencies

- **There is deliberately no performance kill switch.** Agents are allowed to lose
  their paper capital playing out a thesis; that's the experiment.
- **Infrastructure pause** (corrupt data, broken sim, runaway costs, security):
  set `EVO_ENABLED=false` on the evo service (or scale it to zero). Nothing is
  lost; the loop resumes idempotently — missed heartbeat slots are swept as
  abandoned, never double-run.
- **Shrink instead of pause** (keep testing at low scale): `EVO_MAX_ACTIVE_AGENTS=3`
  (see "Running a small population for testing" above).
- Rollback of the whole feature: scale the service down; optionally
  `alembic downgrade -1` removes the `evo_*` tables (destroys agent history).

## Verifying the machinery without spending a cent

The deterministic multi-generation simulation runs the whole system (cohorts,
trading, fitness, retirement, reproduction, wildcard) against a synthetic market
with scripted agents — including adversarial ones — in about a minute:

```bash
python scripts/evo_simulation.py --seed 42 --cohorts 5
```

Same seed ⇒ identical results. This is also part of the test suite
(`tests/test_evo_simulation.py`).

## Troubleshooting

| Symptom (digest) | Meaning | Action |
|---|---|---|
| heartbeats all `degraded` | check the digest's **`degraded by reason`** line (HEALTH section) — it names the actual cause | act on the named reason (below) |
| ↳ `API credit balance exhausted` | the Anthropic account is out of prepaid credit — every call returns HTTP 400 | add credits at console.anthropic.com → Plans & Billing (enable auto-reload to prevent recurrence); bots resume automatically, no redeploy needed |
| ↳ `no/invalid ANTHROPIC_API_KEY` | key missing or wrong on the evo service | set/fix `ANTHROPIC_API_KEY` on the evo service |
| ↳ `per-agent budget/ceiling reached` | agents hit the `$2/agent/week` hard stop | expected late-cohort; raise `EVO_WEEKLY_LLM_CEILING_USD` only deliberately |
| ↳ `malformed model JSON output` | the model emitted invalid JSON (captured in `evo_heartbeats.raw_output_text`) | inspect the captured raw output via the ops DB channel |
| `abandoned` heartbeats | worker restarted mid-heartbeat | none needed (self-heals); investigate frequent restarts |
| orders open > 24h | maker limits never traded through (conservative fills working as intended) or market data stalled | check data-health section |
| integrity events | an agent tried to break the rules | none needed — automatic fitness penalties / suspension already applied; the audit row is permanent |
| population ≠ 30 after a boundary | finalization interrupted mid-way | it resumes idempotently next cycle; check `evo_transitions.status` |
