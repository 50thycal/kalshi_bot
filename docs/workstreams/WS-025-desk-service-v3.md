# WS-025 — Desk service 3.0: unattended desk agents

**Phase:** BUILD_CARD
**Status:** Active
**Created:** 2026-10-11
**Updated:** 2026-10-11
**Build OS:** v0.12

## Goal

Both desks (ChatGPT and Claude) research and trade on their own, waking when the service or
the agent decides a check is worth it, with no "Continue" from the operator. Every money
limit and safeguard stays exactly as it is.

## Context

v1 (WS-021) shipped hosted runners that made one short, tool-less CLI call per job (120 s, no
web, no shell). That was too weak for the evidence-backed research DEC-024 asks for, so the
operator chose app sessions (DEC-019): strong research, but a cycle only happens when someone
says Continue. Today the Claude desk fakes automation with self-booking Claude Code routines;
the ChatGPT desk has not researched since 2026-10-07. Until 2026-10-10 every clean fill also
paused the desk (fixed in `082c4cd`, `DEC-032`), which broke any unattended chain.

Owner request (2026-10-11): "make a 3.0 of the desk service… all automated so it can self run
and make its checks whenever it wants."

## Current Mental Model

```text
TODAY (v2, session mode)
  operator -> "Continue" in ChatGPT/Claude app -> agent session -> claim -> research -> complete
  desk-service: money rules, execution, reconcile, settle (always on, no cognition)

3.0
  desk-service --(wake policy: base windows | own settlement | agent-requested check-in)-->
      queued job (desk, reason)
  agent host per desk, own desk token only
      (ChatGPT: Railway runner + Codex; Claude: Claude Code routine, or runner per D5)
      claims job -> starts a full agent session (Claude Code headless / Codex exec,
      web + desk tools, subscription login) -> same Continue workflow -> complete
      (+ optional next_check_at, bounded)
  desk-service: validates completion
      dry-run  -> picks recorded as shadow decisions, graded at settlement, no order
      live     -> existing executor, unchanged limits
  watchdog: missed wake / 2 failed cycles / expired login -> alert operator
```

## Decisions Made

- **Billing → subscriptions.** Claude Code and Codex log in on the runner hosts with the
  operator's existing plans; no paid API allowance and no API fallback. The desks earn at most
  a few dollars a month, so metered research would cost more than it could return.
- **Cadence → base windows + events.** Three fixed daily windows, plus wakes on the desk's own
  settlements and on a check-in the agent asks for, capped per desk per day.
- **Rollout → dry-run week first.** Automated cycles run for about seven days with picks
  recorded but not traded; the operator reviews and then flips to live (a hard stop).
- **Unknown-order pause → narrow self-clear** (`DEC-032`, already live): a prerequisite for
  unattended trading.

## Open Decisions

- **D1. Alert channel.** Production runs `DESKS_ALERT_MODE=session` (no push). Unattended
  agents need push alerts. Options: an HTTPS webhook to a phone push service (ntfy, Pushover)
  or Slack. Recommendation: ntfy or Pushover, using the existing webhook mode.
- **D2. Base windows.** Recommendation: 08:00, 12:00, 16:00 America/Chicago, the same for both
  desks, with a cap of 6 cycles per desk per day and at least 60 minutes between cycles.
- **D3. Models.** Which model each subscription runs (e.g. Opus vs Sonnet for Claude) against
  plan limits. Recommendation: decide after the dry-run week shows real usage.
- **D5. Where the Claude agent runs.** (a) Claude Code routines: recurring routines at the base
  windows, with event and agent-requested wakes booked as one-shot routines, on Anthropic's own
  scheduled-session feature. Trade-off: the service cannot push a wake into a routine, so a
  settlement wake becomes a check-in the agent books for the known settlement time.
  (b) A self-hosted runner on Railway with a `setup-token`
  subscription token: symmetric with ChatGPT and service-driven, but in a policy grey zone.
  Recommendation: (a). The ChatGPT desk uses the Railway runner with Codex either way, since
  OpenAI has no equivalent scheduled-session feature that can hold the desk's private token.
- **D4. Active-work limit.** The board is already over its limit of four Active rows. Adding
  WS-025 needs the owner to say which row pauses (DEC-011).

## Assumptions

- **Claude on a self-hosted runner is a policy question, not just a technical one.** Headless
  `claude -p` works with a subscription via `claude setup-token` / `CLAUDE_CODE_OAUTH_TOKEN`
  (a one-year token, renewed only by generating a new one; it is ignored if `ANTHROPIC_API_KEY`
  is set or under `--bare`). It supports `--allowedTools` (WebSearch, WebFetch, scoped Bash),
  `--max-turns`, `--permission-mode dontAsk`, and `--output-format json --json-schema`; there is
  no whole-run timeout flag, so the runner must wrap it with an OS timeout. But Anthropic's
  terms say advertised Pro/Max limits assume "ordinary, individual usage", and products should
  use API keys; whether one operator's own scheduled bot qualifies is not documented, and
  enforcement can be without notice. Sources: code.claude.com/docs/en/authentication,
  /headless, /legal-and-compliance. The Agent SDK documents API-key auth only.
- **Claude Code routines are the sanctioned subscription route for Claude.** They are
  Anthropic's own scheduled cloud sessions, already running the Claude desk today.
- Codex can run headless (`codex exec`) on a Railway container with a ChatGPT login, web search
  and a restricted shell, within plan limits. **OpenAI's position on automated personal use is
  to be verified in the build's first step.**
- A 45-minute agent session fits inside the existing 60-minute research lease.
- Subscription limits are shared with the operator's interactive use; six cycles per desk per
  day is affordable. The dry-run week measures this.

## Non-Goals

- No change to any money limit, the IOC-only rule, reservation accounting, pause semantics or
  the unknown-order rules.
- No paid model API, no new budget.
- No change to Experiment OS, the main or evo workers, or the legacy discretionary desk.
- No promotion, sizing increase or replenishment; the 30-day comparison rules stand.

## Build Card

**Status:** Draft — awaiting owner approval · **Build Spec:** to follow once approved
### Goal

The ChatGPT and Claude desks run themselves: they wake up, research, pick and learn without
anyone saying "Continue", inside exactly today's money limits.

### Current behavior

A desk researches only when someone says Continue in its app. Claude is kept going by
self-booking reminders; ChatGPT sits idle until the operator opens the app. The service
handles money, orders and settlement on its own but cannot think or wake an agent.

### New behavior

- Each desk gets an unattended agent: ChatGPT on the rebuilt Railway runner (Codex, ChatGPT
  login); Claude on Claude Code routines (recommended, D5) or the same kind of runner. When a
  check is due, a full agent session starts with web search and the desk's own tools, on the
  operator's existing subscription.
- The service decides when a check is due: three daily base windows, the desk's own position
  settling, or a check-in the agent asked for at the end of its last cycle. There is a daily cap.
- First week is a **dry run**: picks are recorded and graded at settlement but not traded.
  After the operator reviews it, the operator flips the desk to live.
- A watchdog alerts the operator's phone when a wake is missed, two cycles in a row fail, or a
  login expires.

### Mental model / flow

```text
service clock / settlement / agent "check me at X"
        -> due? (cap: 6/day/desk, >=60 min apart)
        -> job queued for that desk
runner (own desk token only) -> agent session (<=45 min):
        status gate -> claim -> research + evidence capture -> 0-3 picks -> complete
service -> dry run: record + grade picks      live: same executor, same limits
        -> next check booked (bounded)        watchdog -> phone alert on trouble
```

### Important rules

- Money rules live in the service code and do not change: $1 per pick, $10 at risk, 3 fills
  a day, 10 attempts a day, IOC only, hold to settlement, one fill per event.
- An agent only ever holds its own desk token. Never the operator token, exchange keys or
  the database.
- A failed or timed-out cycle never turns into a retry that places an order.
- Paused means no trading. Research may continue; only the narrow `DEC-032` rule self-clears.
- Going live is an operator hard stop, per desk, after the dry run.
- When automation goes live, the existing self-booking Claude routines are retired so a desk
  never runs two cycles at once (the service's one-active-job rule also prevents it).

### Decisions made

- **How are agents paid for?** → Existing subscriptions, no API. The desks cannot earn enough
  to cover metered research.
- **How often do they wake?** → Base windows plus events, capped. Lets the agent come back when
  evidence is due, without runaway usage.
- **How does it roll out?** → One dry-run week, then an operator-approved live flip. Proves
  unattended health before money moves.

### Non-goals

- No change to limits, sizing, budgets, pauses or unknown-order rules.
- No paid model API.
- No change to Experiment OS, the main or evo workers, or the legacy manual desk.

### Definition of done

- [ ] Each desk completes three consecutive scheduled cycles with no operator action.
- [ ] Dry-run week: both desks cycle daily within the cap; shadow picks are recorded and graded.
- [ ] A forced runner failure and an expired login each produce a phone alert.
- [ ] The operator flips both desks to live; the first automated live fill ends filled and the
      desk stays running.
- [ ] No operator "Continue" is needed for 7 consecutive live days.

---

**After this change, the system should** research and trade both desks on its own schedule,
inside today's limits, and only call the operator when something is actually wrong.
