# Claude desk — app-session access check, 2026-09-20

Access evidence, not launch authorization. Claude desk readiness was **not** set.
No round was started, no decision submitted, no trade placed, no key rotated,
no credential printed, and no ChatGPT/operator credential used.

## Environment under test

A Claude Code on the web session on the default branch at
`9c03c2313624db95010a1a943a94f9846ef2d582` (merge of PR #450). Repository access only.

## Result: the app-session bridge is not established for this environment

`docs/desks/APP_SESSIONS.md` requires each app's execution environment to reach the
service over HTTPS with **only** its own `DESK_SESSION_TOKEN` and `DESK_SERVICE_URL`
privately provisioned, and states that GitHub access alone does not provide that bridge.
Both halves are missing here:

- No desk client variables are present in the session environment, and no private
  credential file is mounted. The read-only `python -m kalshi_bot.desks.doctor` exits `1`
  with `ACTION — Set DESK_SERVICE_URL to HTTPS or provide a valid DESK_SESSION_TOKEN`.
  Exit `1` is invalid configuration, not a service fault; the service was never contacted.
- The session's egress policy refuses the service host outright. `CONNECT
  desk-service-production.up.railway.app:443` is answered `403` by the environment's
  egress proxy before TLS, so even a correctly provisioned token could not be used.
  The same policy blocks the research sources a real cycle needs
  (`api.elections.kalshi.com`, `docs.kalshi.com`, `api.weather.gov` all fail to connect;
  `api.github.com` succeeds). Both the shell client and the session's fetch tool are
  refused, so this is the environment policy, not a tool-specific failure.

Consequently this session could not GET authenticated context, read publications, claim a
research job, capture service-side source evidence, complete a cycle, or verify a durable
publication. Per the startup packet, missing access is reported rather than claiming
readiness. ChatGPT's earlier bridge, established through authenticated Railway console
access, does not transfer to this environment.

## Verified without service access

- Default branch head is the PR #450 merge; the working tree matches it. The discovery
  repair is present in deployed-branch code: `kalshi_bot/desks/research.py` sends
  `mve_filter=exclude`, records it in the snapshot `coverage`, and invalidates
  snapshots whose coverage lacks it.
- `tests/test_desks_research.py` and `tests/test_desks_service.py` — 64 tests — pass on
  this checkout. Default-branch CI for the merge commit was still running at the time of
  this check, with lint and migrations already green.
- Railway's actually-running revision cannot be confirmed from here; that check needs
  service or console access.

## Next action (Claude desk)

Provision, privately and outside the repository, the Claude desk's existing
`DESK_SESSION_TOKEN` and `DESK_SERVICE_URL` to an execution environment that is also
permitted to reach the service host and the research source domains — either by allowing
those hosts in this session's environment network policy, or by running the Claude app
session where that egress already exists. Then, in one continuation: confirm
`research_mode=session`, resume the existing round, claim as `claude-app`, complete one
source-backed cycle with `decisions=[]`, read the publication back, and only then POST
`/api/desks/claude/ready`. Common start stays with the operator after both desks are
ready and every launch gate in `docs/desks/VERIFICATION-2026-09-20.md` passes.
