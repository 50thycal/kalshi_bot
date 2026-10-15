---
name: daily-checkin
description: Run the global experiment monitor's daily check-in (WS-026) — one read-only monitor snapshot through the ops channel, republish the Experiment Monitor page to its existing URL, and message the owner only when something is broken or newly needs them. Use when a scheduled routine fires the check-in, or when the user says "run the daily check-in", "check everything is running", "status page", or "is everything running".
---

# Daily check-in

Role: **Experiment Control Tower** (read-only). This skill reads, publishes a
private page and writes a message. It never sends an env change, issue command,
lifecycle move, gate verdict or arming, and never changes real-money exposure.
Background: `docs/MONITOR.md`.

## Steps

1. **One snapshot.** Ops recipe in `CLAUDE.md`. Use exactly one request with a
   unique id, then poll for that id only. Allow several minutes.
   ```json
   {"type":"xos","command":"monitor-snapshot","id":"mon-<YYYYMMDD-HHMM>","actor":"claude","purpose":"daily check-in","workstream":"WS-026"}
   ```
   If the result says the command is not allowlisted, send
   `{"type":"xos","command":"control-tower","args":["--json"],"id":"mon-ct-<stamp>"}`
   instead and use `--tower` in step 3. The page will say PARTIAL.
   Reset `ops/request.json` to `{"type":"noop"}` afterwards. Copy the result out
   of `ops/results/` promptly, because only the newest 80 results are kept.

2. **The previous page.** The page URL is in the routine prompt. Without one, find
   "Experiment Monitor" with `Artifact` `action: "list"`. Read it with
   `Artifact` `action: "read"` and save the HTML locally. It embeds yesterday's
   snapshot.

3. **Render and decide.**
   ```bash
   python -m kalshi_bot.monitor.page --snapshot <result.txt> \
       --previous-page <previous.html> --out <scratchpad>/status.html
   ```
   It prints `{"notify": …, "reasons": […], "new_untracked": […]}`.

4. **Republish** `<scratchpad>/status.html` with `Artifact` to the **same URL**
   (`url` = the page URL). Do not create a new artifact.

5. **Message only if `notify` is true.** If a push-notification tool is
   available, send one line first. Then close with the five-line brief
   (`session-brief`):
   - WHAT HAPPENED: the check-in ran; the counts (active, blocked, not running).
   - GOAL: live realized for the last 30 days against $100.
   - BLOCKERS: the broken items from `reasons`.
   - DECISIONS: the needs-you items, in plain words.
   - NEXT STEPS: for each untracked problem in `new_untracked`, the owning role
     that should adopt it (`OPEN_CANDIDATE` + fingerprint). This is prepared,
     not sent.

   Then put the page link. If `notify` is false, end with exactly one line:
   `Check-in <date>: all expected — <page link>`.

## Never

- Never open, update or adopt an issue yourself. The issue transport redeploys
  the live worker, and that write belongs to the owning role (WS-026 owner
  decision).
- Never "fix" a STALE or SILENT book (no env append, no restart). Report it, and
  name Live Ops as the role that checks the runtime first.
- Never copy the snapshot's numbers into repo docs. The page is a view, not a
  ledger.
