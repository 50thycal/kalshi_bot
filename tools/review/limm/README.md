# Alimm1 review desk

Review page for the Alimm1 liquidity book, in the `review-page` format
(`.claude/skills/review-page/SKILL.md`).

- **Page:** https://claude.ai/artifact/EpuauwQqwNNoAitmxqNZNf (private to the operator)
- **Round 1 (2026-11-06):** window markets, GROC/SAFE, and the open rule
  decisions (409 bookkeeping, 24h step-aside, skip events under $5, YouTube
  leftovers, time zone). Data: `round1.mjs`.
- **Window study results:** `probe-2026-11-06.json`, parsed from
  `scripts/limm_window_probe.py` output (ops); the page shows "Study running"
  until it exists.
- **Build:** `node tools/review/limm/page.mjs <out.html>`, then publish the same
  file path so the URL stays.

## Store

| Collection | Fields | Holds |
|---|---|---|
| `answers/{questionId}` | `choice` (option index), `note`, `at` | Round 1 ids: `r1-windows`, `r1-grocsafe`, `r1-409`, `r1-stepaside`, `r1-small`, `r1-youtube`, `r1-tz`, `r1-general` |
| `reviews/{itemId}` | `verdict` (go, maybe, drop), `note`, `at` | `win-say`, `win-month`, `win-company` |

Answers read back are copied to `answers-YYYY-MM-DD.json` here. An answer is the
operator's go for the step it names; hard stops still need explicit
confirmation (`docs/STANDING_AUTHORIZATIONS.md`).
