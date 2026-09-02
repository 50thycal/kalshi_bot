# `ops` — the machine-operable control plane

This branch is how a Claude/ChatGPT session, or anyone without a Railway login,
**operates the running Kalshi system**: read production logs and the database,
run the canonical Experiment OS reads, run allowlisted research probes, take an
operating snapshot, open an incident bundle — and, within tight bounds, **change
the running configuration**.

It is a **transport**, not code. Nothing here is ever executed.

## How a request runs

1. A producer overwrites `ops/request.json` on this branch and pushes.
2. That push fires the `Ops Runner` workflow
   (`.github/workflows/ops-runner.yml`, which lives on **this** branch because
   Actions loads a workflow from the branch that triggered it).
3. The runner checks out **the repository's default branch** into
   `.ops-runner-code` and executes `scripts/ops_runner.py` **from there**. The
   runner refuses to serve unless it can attest that (`OPS_RUNNER_CODE_SOURCE`),
   so a merge to the default branch is live on the next request and this branch
   can never serve stale code. That failure mode is XOS-000005.
4. Results are committed back here:
   - `ops/result.txt` — the latest run (last writer wins);
   - `ops/results/<id>.txt` — **your** run, durably, keyed by the request's `id`;
   - `ops/results/<id>.receipt.json` — the machine-readable receipt.

   The newest 80 of each are kept. **Always set a unique `id`** and read your own
   file: several producers use this channel at once.
5. A failed request is published like any other **and turns the workflow run
   red**. Green means the request succeeded.

Latency is ~30–90s. Request commits never touch the default branch and never
redeploy the worker — only an `env` mutation does that, deliberately.

## Discovering what the channel can do

Do not trust this file for the current allowlists — ask the runner:

```jsonc
{"type": "capabilities", "id": "cap-1"}            // generated from the live allowlists
{"type": "capabilities", "format": "json", "id": "cap-2"}
```

It reports the executing code's SHA, every request type with its READ/MUTATING
classification, which Railway services are configured, the Experiment OS command
allowlist, the analysis-script allowlist, every readable/settable variable (with
the redacted and durably-audited ones marked), and the hard limits.

And to establish operating context in one request:

```jsonc
{"type": "doctor", "id": "doc-1"}
```

— runner freshness, database connectivity, Railway reachability per service, the
non-secret critical runtime configuration (kill switch, live enabled, live
strategies, exposure caps), and Experiment OS enforcement/readiness/issues read
through the **canonical CLI**. A subsystem that cannot answer is a WARNING in
the report, not a failed request.

## Request families

| type | class | what it does |
|---|---|---|
| `noop` | read | nothing — the resting state |
| `logs` | read | bounded Railway logs for one service |
| `db` | read | one read-only SQL statement (`DATABASE_URL_RO`) |
| `script` | read | one allowlisted self-contained analysis script |
| `xos` | read | the canonical Experiment OS read CLI |
| `capabilities` | read | this channel's generated capability surface |
| `doctor` | read | one-request operating snapshot |
| `incident` | read | bounded investigation bundle for one service |
| `env` | read **or MUTATING** | read allowlisted Railway variables — or change them |

```jsonc
{"type": "logs",   "service": "main", "limit": 200, "filter": "", "id": "logs-1"}
{"type": "db",     "sql": "select count(*) from paper_trades", "max_rows": 200, "id": "q-1"}
{"type": "script", "name": "live_paper_parity", "args": [], "id": "s-1"}
{"type": "xos",    "command": "control-tower", "id": "ct-1"}
{"type": "incident", "service": "main", "window_minutes": 30, "id": "inc-1"}
{"type": "env",    "service": "evo", "id": "env-1"}
{"type": "noop",   "id": "idle"}
```

`service` selects the Railway target on `env`, `logs` and `incident`:
`"main"`/`"live"` (the trading worker, default), `"evo"` (the evolutionary-agent
worker), `"livedash"` (the live-vs-paper dashboard). `db` is service-agnostic —
one Postgres behind all of them.

**Experiment OS is canonical.** `xos` runs the same read CLI the worker runs, so
the operating layer cannot drift from Experiment OS. This channel is
**read-only against Postgres**: every writing subcommand refuses the read-only
URL, which is the only URL it has. The worker is the only writer.

## The one mutating family

`env` changes the running configuration of a Railway service and, by default,
redeploys it. Say so explicitly:

```jsonc
{"type": "env", "action": "get", "id": "env-1"}
{"type": "env", "action": "set", "values": {"KILL_SWITCH": "true"}, "id": "kill-1"}
```

The legacy spelling `{"type":"env","set":{…}}` still works and means the same
thing. A request that is ambiguous about which it is — `action:"get"` carrying
values, `action:"set"` carrying none — is **refused**, not guessed at.

A mutation announces itself in the first line of the result, records the
**before** state, applies, reports the redeploy outcome, reads the state **back**,
and ends in one verdict: `VERIFIED`, `APPLIED_BUT_UNVERIFIED` or `FAILED`. When
it touched Experiment OS or live-strategy state, the canonical `enforcement` and
`readiness` reads run afterwards and are printed with it. Only allowlisted
variables can be set (see `capabilities`); everything else is refused before any
network call. Changes to real-money capability, the risk envelope, or the
Experiment OS write transports are additionally archived to the long-lived
`ops-audit` branch.

Add `"verify": false` to skip the readback, `"redeploy": false` to apply without
restarting the service.

## Provenance

Any request may carry `actor`, `purpose`, `workstream` and `issue`. They are
echoed into the header and the receipt so a change can be traced back to why it
was made. **They are labels, never authority** — the allowlists decide what is
permitted, not who claims to be asking.

## ⚠ This branch is PUBLIC

Every request payload committed here is public disclosure, permanently, in Git
history. Redaction in the runner's OUTPUT is hygiene, not privacy. Never put a
credential, personal data, private log or sensitive raw evidence in a request.

## What this channel deliberately cannot do

No arbitrary shell. No arbitrary module execution. No writable database
credential. No secret can be read, printed or set — `KALSHI_*`, `DATABASE_URL`,
`RAILWAY_*` are not on any allowlist. No arbitrary Railway API access. No
Experiment OS write path against Postgres. None of that expands because a script
or a variable happens to exist in the code.

Full mechanism, standing commands, branch-protection rules and the deliberate
procedure for changing the workflow file: **`docs/OPS_RUNBOOK.md`**.
