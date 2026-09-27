# Railway runner deployment continuation

WS-021 / DEC-018. This recipe provisions the existing runner; it changes no trading
limits, shared executor, readiness logic, or model-spend authority.

Use a separate Railway project and dedicated Postgres service. Set the desk service
start command explicitly to `python -m kalshi_bot.desks serve` and healthcheck
`/healthz`. Newly created Railway services cannot opt into the deprecated
`railway.desks.json` Config as Code route; use service settings.
Keep `DESKS_LIVE_ENABLED=false`, both verification flags false, and the monthly
paid-research budget zero during setup. Store three distinct random role tokens
as service secrets. Never copy worker/Evo variables.

## Current operator choice: app-driven pilot

On 2026-09-20 the operator chose research in the ChatGPT and Claude app sessions,
triggered by saying `continue`, before considering unattended runners. Hosted model
login and billing setup are deferred. Do not resume either login or hosted cognition
unless the operator requests that mode later.

Both runner service start commands are set to
`/usr/local/bin/desk-runner-entrypoint /bin/true` with restart policy NEVER so they
exit without research or model calls, including after source-triggered deployments.
Keep their private volumes and image recipe for a possible later activation. The
shared desk service and Postgres remain available; retained infrastructure can still
incur hosting/storage charges.

Each app session should resume its own persisted desk context, perform research while
active, and persist findings, rejections and lessons before ending. An inactive chat
does not continue researching. The existing session bridge supports authenticated
context and publication operations; this choice does not establish a working cycle.

PR #448 / DEC-019 adds explicit app-session mode with the same bounded live execution
controls. Use `DESKS_RESEARCH_MODE=session` and follow APP_SESSIONS.md after integration.
Both apps must demonstrate completed research and have private bridge access; restricted
funding/account isolation, alerts and the operator common start still guard live launch.
Do not falsely attest to unattended runners. Hosted login remains deferred.

## Runner image (deferred option)

Deploy `deploy/desks/Dockerfile.runner` to one separate service per desk.
Attach one private persistent volume per service at `/data`. There is no public
domain or HTTP healthcheck on a runner. Only provide its own
`DESK_SESSION_TOKEN` and `DESK_SERVICE_URL`. Never give a runner the operator,
other-desk, database or exchange credentials.

The image pins Codex 0.155.1 and Claude Code 2.1.278, checks the required help flags
at build time, initializes private directories and drops to UID 10001.
Set Railway's start command explicitly to
`/usr/local/bin/desk-runner-entrypoint sleep infinity` for interactive setup.
Railway's custom start command bypasses the Docker ENTRYPOINT; `sleep infinity`
alone leaves PID 1 as root and skips private directory initialization.
An online container in this mode is NOT a functioning researcher.
Keep one replica. Preserve volume contents through every redeployment.

For a staged ChatGPT-only scheduled setup, leave the desk service in app-session mode and
leave `DESKS_EXTERNAL_RUNNERS_VERIFIED=false`. The eventual desk-service configuration is:

```text
DESKS_RESEARCH_TIMEZONE=America/Chicago
DESKS_CHATGPT_RESEARCH_SCHEDULE=08:00,12:00,16:00
DESKS_CLAUDE_RESEARCH_SCHEDULE=disabled
```

Do not apply `DESKS_RESEARCH_MODE=scheduled` yet. Exact windows are DST-aware, skip missed
windows instead of catching up, and retain the active-job non-overlap guard. The disabled
Claude schedule changes no Claude runner service or credential; it only prevents the shared
desk service from creating unattended Claude jobs after a future scheduled-mode cutover.

The `chatgpt-runner` service must have its own `/data` volume and only its own
`DESK_SESSION_TOKEN` plus `DESK_SERVICE_URL`. Keep `/data/runner` and `/data/model-home`
private. Do not configure an OpenAI API key: authenticate Codex in `/data/model-home` using
the existing subscription, and independently verify in the ChatGPT billing controls that
extra paid usage is disabled. If either check is unavailable, leave the runner inactive.

Using Railway's authenticated console, run commands as the same identity:

```sh
gosu desk codex login --device-auth
gosu desk codex login status
```

For Claude's separate container, use the supported interactive client login:

```sh
gosu desk claude auth login
gosu desk claude auth status
```

If the console already runs as UID 10001, omit `gosu desk`.
Login is performed by the account owner. Do not copy credentials between hosts,
paste auth files into chat, put codes in deployment logs, or use API fallback.
Verify active authentication, subscription entitlement and extra-usage settings
before any research invocation. Login success alone does not establish billing.

Scheduled mode also requires a tested HTTPS webhook; session-only alerting is not valid.
A long, random ntfy topic subscribed in the mobile app is the smallest phone-friendly
destination compatible with the notifier's JSON `message` field. Store its URL as the
secret `DESKS_ALERT_WEBHOOK_URL`, set `DESKS_ALERT_MODE=webhook`, and run the operator-only
`scripts/desk_operator.py test-alerts`. Keep the gate blocked unless that test is recorded
successful.

Then run the relevant `once` command from RUNNERS.md with these real paths:
Python `/opt/desk-venv/bin/python`, checkout `/opt/kalshi_bot`,
state `/data/runner`, research home `/data/model-home`.
Select and record an entitled model ID explicitly before running.
Verify completed publication in authenticated service state. Only then configure
the service start command for `serve`, prefixed with
`/usr/local/bin/desk-runner-entrypoint`; a failed/uncertain model call must not be
reset or repeated by deleting state. Set restart policy NEVER for a runner until
its exit-2 handling is explicitly supported by the supervisor; otherwise Railway
could restart a needs-operator failure repeatedly.

Both sessions mark only their own readiness after setup. Account isolation,
funding, alerts, working cognition, and common-start authorization remain guards.

## Setup checkpoint, 2026-09-20

PR #445 merged at `bb7c6a1e5e0a09c6b94a8c86f0939195e9633364`.
The setup session provisioned a separate service and persistent Postgres,
plus two idle runner services with separate persistent volumes. Both images
built successfully with pinned native clients. Model login is pending.
HTTPS and authenticated context were verified; the monitor reports a fresh tick.
Both sessions remain unready and the round has not started. Exchange credentials,
restricted subaccounts, alerts, model authentication/billing, completed cycles and
runner supervision remain unverified. No model calls or live orders were made.

The diagnostic CLI disables environment proxy use. In a proxy-only setup shell it
cannot connect directly; an authenticated GET through the approved environment
proxy was assessed using the unchanged `doctor.assess` function. This verifies the
service, not a successful direct-network doctor invocation in that shell.
