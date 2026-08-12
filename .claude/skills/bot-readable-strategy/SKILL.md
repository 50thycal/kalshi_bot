---
name: bot-readable-strategy
description: Make an operator strategy readable by the evolutionary agent fleet — thesis, premise, AND live performance. Use whenever a new operator strategy/book is created (or an existing one is missing from what the bots can see), when the user says "make sure the bots can read this", "publish this strategy to the bots", "can the fleet see X", or as the final step of building any new book. Verifies the three touchpoints (BOOK_REGISTRY row, thesis doc in docs/, a paper_trades tag) that the fleet's read_doc + book_performance channels pick up automatically on the next deploy.
---

# Bot-readable strategy — the tag-team contract

The evolutionary agents mirror the operator's research through exactly two channels,
both automatic once the conventions below are followed. **There is no publish step**:
`docs/` ships inside the deployed image (nixpacks copies the repo), so a merged PR is
readable by the fleet at the next deploy of the evo worker; performance is read live
from the DB every time an agent asks.

| what the bots read | channel | where it comes from |
|---|---|---|
| thesis / premise / studies | `read_doc` action | any `docs/*.md` file |
| the master index of books | `read_doc BOOK_REGISTRY` | `docs/BOOK_REGISTRY.md` |
| live performance (n, win%, total, per-trade, open) | `inspect_data {"source": "book_performance"}` | `paper_trades` grouped by `strategy` tag |

## Checklist for a new operator strategy

Run through all four; the first three are the same conventions `kalshi-strategy`
Phase 5 already requires, restated here from the bots' point of view:

1. **Tag** — the book writes `paper_trades.strategy` with a stable tag (or prefix for
   a variant family). This is the join key for everything: `book_performance` groups
   by it, and the registry row must match it exactly.
2. **Registry row** — add the book to `docs/BOOK_REGISTRY.md`: tag, status, thesis
   doc link, one-line edge, pre-registered gate. This is the FIRST doc agents are
   told to read; a book missing here is invisible to their index of what exists.
3. **Thesis doc** — the premise lives in a `docs/<NAME>_THESIS.md` (or equivalent
   study doc). Any `.md` directly in `docs/` is automatically in the fleet's
   `read_doc` library on the next deploy — subdirectories are NOT readable, so keep
   strategy docs at the top level of `docs/`.
4. **Verify the mirror** (after the deploy that includes the docs):
   - the doc appears in the library: the agents' action protocol lists it, or check
     `python -c "from kalshi_bot.evo import knowledge; print(knowledge.doc_names())"`
   - the scoreboard row exists once trades settle: ops request
     `{"type":"db","sql":"select strategy, count(*) from paper_trades where strategy='<TAG>' group by 1"}`

## What NOT to do

- Do not hand-copy research into `announcements.py` or `graveyard_seed.py` to make it
  readable — that was the pre-`read_doc` workaround. Announcements are for one-time
  broadcasts ("this changed"); the docs are the durable library. A graveyard seed is
  still right for a **dead** idea you never want re-explored, since the graveyard is
  checked at `save_strategy` time, which `read_doc` is not.
- Do not create a bots-only summary doc of a strategy. The bots read the same doc the
  humans do; a second copy will drift.
- Do not put the performance numbers in the doc. Numbers in prose go stale the day
  they are written — the bots get live numbers from `book_performance`, and the doc
  should carry the thesis, the gate, and the verdicts.

## Why both channels must exist for every book

An agent that reads a thesis without its scoreboard will cargo-cult a losing book
(several operator books are documented mirages — the registry says which). An agent
that reads a scoreboard without the thesis will copy numbers with no mechanism and
no gate. The pair is the product: premise + live outcome, same as the operator uses.
