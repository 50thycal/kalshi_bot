# The reviewed-universe paper tape (`Rmmsell1`)

**Status:** designed, **not registered, not armed, not trading.** Nothing in this document
authorizes anything. Registration is an Experiment OS action and needs an explicit operator go —
see *Registration* below.

## The question

Every mmsell book to date trades a universe defined by *structure* — a band, a price ceiling, a
market type, a substring family. None of them trade a universe defined by **understanding**.

Twenty-four series have now been through the four-check review (category, resolution rules,
contracts-per-outcome, historical P&L) and been signed by the operator in
`kalshi_bot/registry/series_manifest.json`. `Rmmsell1` asks the one question that set makes
askable:

> Does restricting the maker book to series a human has actually read and signed off on beat
> the same book run over everything?

That is a **universe** question, so `Rmmsell1` differs from its comparator in universe and in
nothing else.

## The book

```
Rmmsell1:lo=5,hi=10,maxyes=7,onlyx=KXARGPREMDIVTOTAL+KXATPEXACTMATCH+KXFEDMENTION+KXINXU+KXITFMATCH+KXLEAGUESCUPTOTAL+KXLIGAMXTOTAL+KXMLBGAME+KXMLBSPREAD+KXMLSGAME+KXMLSTOTAL+KXNASDAQ100U+KXNATGASD+KXNFLTOTAL+KXNPBGAME+KXRAIN+KXTRUMPSAY+KXUCLGAME+KXUCLTOTAL+KXUFCMOV+KXWNBAGAME+KXWNBASPREAD+KXWTAMATCH+KXWTASETWINNER
```

`lo=5,hi=10,maxyes=7` is **`mmsell10`'s** band and ceiling, chosen so `mmsell10` is a
ready-made comparator running the same rule over the unreviewed universe. Read the two against
each other and the difference is the whitelist.

Regenerate the line from the manifest with `python scripts/reviewed_tape_spec.py`; check it for
drift with `--check`. A test (`tests/test_mmsell.py`) asserts the line above parses into exactly
the book this document describes — a spec that fails to parse is dropped **silently** by
`mmsell_variant_list`, and a tape running as zero books looks like an inactive experiment rather
than a typo.

### What it deliberately does NOT carry

- **No contest cap.** The correlation cap (`contestcap`, XOS-000020) is a separate live question
  with its own arm (`Gmmsell2`). Carrying it here would make a negative result unattributable:
  universe or cap? The operator's framing was *"Main thing is getting a list of markets we
  understand and have positive metrics on. We can add caps afterwards if needed."*
- **No review tier** (`universe=graduated`). The tier is a coarse proxy for the very thing the
  explicit list states exactly; setting both would narrow the book twice.
- **No market-type filter.** Nine of the twenty-four are totals/spreads and the type books
  already answer that question.

### Why `onlyx=` and not `only=`

`only=` matches **substrings** (`tracker._book_admits_series`). Against these twenty-four that is
correct for twenty-three of them and **wrong for one**: `only=KXTRUMPSAY` also admits
`KXTRUMPSAYCOMPANY` and `KXTRUMPSAYMONTH`, neither of which anyone has reviewed. The tape's whole
claim is that every series in it was read, so a substring allowlist would falsify the experiment
on day one. Same class as the earlier `KXUE` prefix collision.

`onlyx=` is exact. A malformed entry rejects the whole spec rather than quietly shrinking the
universe, because an exact allowlist cannot fail loudly the way a substring one does.

## Isolation

The operator's requirement was that this be readable **independently of everything already
running**. It is, structurally:

| | |
|---|---|
| Paper only | `Rmmsell1` is a variant book; the live mirror fires only for tags in the live arm set, and this tag is in none. |
| Fresh tape | A new tag inherits no paper state. Its epoch starts at its arming instant. |
| No shared knob touched | Every parameter is per-book. No global setting changes, so `mmsell`, `mmsell5`–`mmsell11`, the type books and both live arms see exactly the candidate stream they saw yesterday. |
| No taxonomy change | `SERIES_TYPES` is untouched. |
| Additive `onlyx` | The key defaults to `[]` for every existing book, which admits everything — inert for the whole running cohort. |

The one thing it shares with every other book is the **scan**: books are evaluated against the
same volume-ranked event list. That is intended (it is what makes a comparison against `mmsell10`
meaningful) and it is not contamination — a book's decision does not alter another book's
candidate set.

## Reading it

Compare `Rmmsell1` against `mmsell10` over `Rmmsell1`'s own window, per-trade and on total
dollars, the same way the scan-depth experiment was read. **Do not** compare it against
`mmsell10`'s lifetime numbers: they cover a different market regime.

Expect this to take a while to say anything. The twenty-four series are a small slice of a
universe of 367, so the tape's trade rate will be well below `mmsell10`'s, and per-trade
differences need roughly the same sample any mmsell comparison needs before they are readable.
A thin sample is a `HOLD`, not a verdict.

Note the selection honestly: these series were signed **partly on their own historical P&L**
(check 4 of the review). Some of the edge the tape shows is therefore in-sample by construction.
The out-of-sample claim is only about what the tape does from its arming instant forward, which
is exactly why the fresh tag matters.

## Widening it

Batches 6, 7, 8 will sign more series. The tape does **not** pick them up automatically, and that
is deliberate: a book whose universe grows underneath it produces evidence that cannot be pooled
across the change. Widening is an operator act:

1. `python scripts/reviewed_tape_spec.py` — regenerate; read the diff.
2. Decide, deliberately, that the running evidence is worth resetting.
3. New env value **and** a recorded Experiment OS epoch (changed world) on the arm.

`scripts/reviewed_tape_spec.py --check` reports the divergence between the manifest and this
document. A non-zero exit is a **prompt**, not a bug: it means series have been signed since the
tape was last widened.

## Registration — the two independent requirements

Same shape as `Gmmsell2` (`docs/handoffs/HANDOFF-gmmsell2-registration.md`). Both are required;
neither implies the other, and **doing only one produces a book that looks armed and trades
nothing**:

1. **An Experiment OS deployment arm** registered to the tag `Rmmsell1`. Enforcement is
   `NEW_ONLY`: `tracker._book_is_admissible` refuses any tag without an active arm at the write
   path, and the book is reported in `summ.blocked_books`.
2. **`MMSELL_VARIANTS`** extended with the spec line above, via the ops `env` transport. Setting
   it redeploys the worker.

Because this is a new question ("does a reviewed universe beat an unreviewed one?") rather than a
new world or a new answer to an old question, the sanctioned shape is a **new experiment**, not an
epoch cut or a version bump on `mmsell-correlation-cap`. Its comparator (`mmsell10`) is an
existing paper book in the same snapshot, so it is a legitimate internal control rather than the
cross-snapshot delta that v1's own reasoning refused.

**Neither step has been taken.** Registering an arm is durable experiment state and changing
`MMSELL_VARIANTS` redeploys production; both need the operator's explicit go.
