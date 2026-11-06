---
name: review-page
description: |
  The default way to hand the operator a larger round of decisions for this repo: a published HTML review page (a claude.ai artifact with a database) where the operator answers the open questions (recommended option first), marks items Go further / Maybe / Drop, writes notes, and the next session reads the saved marks back. Diagrams and measured numbers sit beside each decision. Use whenever a round has several decisions or many items: open questions from a 72h review, a set of candidate markets or books, a study's results to act on, a plan to confirm before a build. Also use when the operator asks for "the HTML page", "the review page", "the decision page" or to "update the page". Skip it for small asks (one or two questions, a single bug): ask those in chat.
---

# Review pages

Operator's rule (2026-11-06), adopted from the td_cargo_ship repo's review
pages: use the HTML format, with diagrams, for larger rounds of questions and
decisions in this repo. Small asks stay in chat.

The page is a **decision surface, not durable state**. An answer on the page is
the operator's go for the step it names, exactly as if typed in chat (and
`docs/STANDING_AUTHORIZATIONS.md` still applies: hard stops still need explicit
confirmation). Experiment OS stays canonical; numbers on the page come from
ops reads or results files, never retyped from memory.

## The loop

1. **Data first.** Put the round's questions, items and evidence in a data
   module in the repo: `tools/review/<topic>/round<N>.mjs`. The page is
   generated from it.
2. **Generate the page** with `tools/review/<topic>/page.mjs` (copy
   `tools/review/limm/page.mjs`: a Decide tab, evidence tabs, SVG diagrams of
   the mechanism, item cards, a sticky tally).
3. **Publish it as an Artifact** with `capabilities: {db: {}}`. Load the
   `artifact-capabilities` skill first (and the Artifact quickstart for a new
   page). Check it once at 390 px wide in dark mode (no page errors, no
   sideways scroll), then publish.
4. **Verify the store:** one `ArtifactData` `list` of each collection the page
   writes. Tell the operator in one line what was checked.
5. **Link it** in the thesis/plan doc and the PR, with the collection layout.
6. **Read the marks back** when the operator says they've answered:
   `ArtifactData` `list` on every collection; copy them to a dated JSON beside
   the data (`tools/review/<topic>/answers-YYYY-MM-DD.json`). Count from that
   file, not by eye.
7. **Act and update the same page.** Do what the answers authorize, then add
   the next round's questions (new ids) and republish the same file path so
   the URL stays and earlier answers stay readable.

## Page conventions

- **Questions:** radios, recommended option first and labelled
  "(recommended)", one line of why under each, a notes box on each, and one
  "Anything else?" at the end. Plain words; each question stands on its own.
- **Items:** a card with name, one-line pitch, the measured numbers, and
  Go further / Maybe / Drop (or Approve / Change / Cut). Tapping again clears.
- **Diagrams:** draw the mechanism (price paths, order flow) as inline SVG
  coloured from theme tokens; scale charts to the values that matter.
- **Evidence beside the decision:** study verdicts, P&L, rewards, each with
  its source.
- **Times in US Central (CT).** Phone first: 390 px, light and dark.

## Store layout (ids stable across rounds)

| Collection | Fields | Holds |
|---|---|---|
| `reviews/{itemId}` | `verdict`, `note`, `at` | A mark on an item. |
| `answers/{questionId}` | `choice` (option index), `note`, `at` | An answer. |

Question ids carry the round (`r1-windows`); never reuse one. Before the store
connects, marks are kept in `localStorage` with `pending: true` and pushed once
it does.

## Pages

- Alimm1 review desk (round 1, 2026-11-06): see `tools/review/limm/README.md`.
