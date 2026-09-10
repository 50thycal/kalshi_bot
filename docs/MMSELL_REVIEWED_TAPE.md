# The reviewed-universe paper tapes (`Rmmsell1`, `Rmmsell2`)

**Status:** designed, **not registered, not armed, not trading.** Nothing in this document
authorizes anything. Registration is an Experiment OS action and needs an explicit operator go —
see *Registration* below.

## The question

Every mmsell book to date trades a universe defined by *structure* — a band, a price ceiling, a
market type, a substring family. None of them trade a universe defined by **understanding**.

Twenty-four series have now been through the four-check review (category, resolution rules,
contracts-per-outcome, historical P&L) and been signed by the operator in
`kalshi_bot/registry/series_manifest.json`. That set makes two questions askable at once:

1. Does restricting the maker book to series a human has read beat the same book run over
   everything?
2. Inside a universe we understand, does the **contest cap** still help?

So there are two tapes, and the pair is the point. They share a universe and differ only in the
cap, so the difference between them is the cap's effect *within* the reviewed set — a different
question from `Gmmsell1`-vs-`Gmmsell0`, which asks whether the cap helps over everything.

## The books

```
Rmmsell1:lo=5,hi=10,maxyes=7,onlyx=KXARGPREMDIVTOTAL+KXATPEXACTMATCH+KXFEDMENTION+KXINXU+KXITFMATCH+KXLEAGUESCUPTOTAL+KXLIGAMXTOTAL+KXMLBGAME+KXMLBSPREAD+KXMLSGAME+KXMLSTOTAL+KXNASDAQ100U+KXNATGASD+KXNFLTOTAL+KXNPBGAME+KXRAIN+KXTRUMPSAY+KXUCLGAME+KXUCLTOTAL+KXUFCMOV+KXWNBAGAME+KXWNBASPREAD+KXWTAMATCH+KXWTASETWINNER
```

```
Rmmsell2:lo=5,hi=10,maxyes=7,contestcap=1,contestkey=split,onlyx=KXARGPREMDIVTOTAL+KXATPEXACTMATCH+KXFEDMENTION+KXINXU+KXITFMATCH+KXLEAGUESCUPTOTAL+KXLIGAMXTOTAL+KXMLBGAME+KXMLBSPREAD+KXMLSGAME+KXMLSTOTAL+KXNASDAQ100U+KXNATGASD+KXNFLTOTAL+KXNPBGAME+KXRAIN+KXTRUMPSAY+KXUCLGAME+KXUCLTOTAL+KXUFCMOV+KXWNBAGAME+KXWNBASPREAD+KXWTAMATCH+KXWTASETWINNER
```

| | `Rmmsell1` | `Rmmsell2` |
|---|---|---|
| universe | the 24 reviewed series | the same 24 |
| band / ceiling | `lo=5,hi=10,maxyes=7` | the same |
| contest cap | **none** | **1 per contest**, corrected (`split`) key |

`lo=5,hi=10,maxyes=7` is **`mmsell10`'s** band and ceiling, chosen so `mmsell10` is a ready-made
comparator running the same rule over the unreviewed universe.

Regenerate both lines from the manifest with `python scripts/reviewed_tape_spec.py`; check them
for drift with `--check`. A test (`tests/test_mmsell.py`) asserts each line above parses into
exactly the book this document describes — a spec that fails to parse is dropped **silently** by
`mmsell_variant_list`, and a tape running as zero books looks like an inactive experiment rather
than a typo.

### Why `contestcap=1,contestkey=split` and not the shipped key

The shipped contest key groups by event token, which collapses `KXTRUMPSAY`'s 37 distinct words
into 10 weekly buckets and `KXRAIN`'s 221 markets into 27 days — so a cap of 1 on that key
refuses entries that share no outcome at all. Three of the 24 reviewed series
(`KXFEDMENTION`, `KXRAIN`, `KXTRUMPSAY`) are subject-split, so the corrected key is not a
refinement here, it is the difference between a cap that measures correlation and one that
measures the calendar. `docs/MMSELL_CONTEST_KEY_SUBJECT_SPLIT.md` has the measurement.

### What the pair deliberately does NOT carry

- **No review tier** (`universe=graduated`). The tier is a coarse proxy for the very thing the
  explicit list states exactly; setting both would narrow the books twice.
- **No market-type filter.** Nine of the twenty-four are totals/spreads and the type books
  already answer that question.
- **No live mirror.** Both tags are paper. Neither is in any live arm set.

### Why `onlyx=` and not `only=`

`only=` matches **substrings** (`tracker._book_admits_series`). Against these twenty-four that is
correct for twenty-three and **wrong for one**: `only=KXTRUMPSAY` also admits
`KXTRUMPSAYCOMPANY` and `KXTRUMPSAYMONTH`. Same class as the earlier `KXUE` prefix collision, and
in this case not hypothetical — see below.

`onlyx=` is exact. A malformed entry rejects the whole spec rather than quietly shrinking the
universe, because an exact allowlist cannot fail loudly the way a substring one does.

## The `KXTRUMPSAY` family — why the exact key was load-bearing

Reviewed 2026-09-10. The three series carry **verbatim identical** settlement rules — same payout
criteria, same sources (public statements, Source Agency quotes, Truth Social/Twitter), same
exclusions — and differ in one clause, the resolution window:

| series | window | trades | markets | P&L | ¢/contract | loss rate | avg entry | edge | decision |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| `KXTRUMPSAY` | one week | 955 | 119 | **+$61.85** | +6.5¢ | 4.0% | 9.99¢ | **+6.0** | signed |
| `KXTRUMPSAYCOMPANY` | to month end | 58 | 12 | +$3.85 | +6.6¢ | 3.4% | 7.90¢ | +4.5 | **held** |
| `KXTRUMPSAYMONTH` | to month end | 117 | 18 | **−$7.34** | −6.3¢ | **18.0%** | 10.44¢ | **−7.5** | **barred** |

All-time paper, mmsell family, twins excluded (ops `cc-trumpsay-1`).

**The mechanism is duration mispricing, and the book is on the wrong side of it.** mmsell sells
the cheap tail. A monthly is the same question over roughly four times the window, and Kalshi
prices it at essentially the same premium — 10.44¢ against the weekly's 9.99¢. The loss rate goes
to 18% because the condition is far easier to satisfy; the premium does not move to compensate.
That is not a thin-sample story: 21 losses across 18 markets.

**They are also not independent of each other.** Seven words have been traded in more than one of
the three (ops `cc-trumpsay-3`):

| word | weekly | monthly | company | P&L |
|---|---:|---:|---:|---:|
| `UFO` | 89 | 13 | — | +$7.43 |
| `PEAC` | 61 | 2 | — | +$5.74 |
| `NEWS` | 55 | 1 | — | +$6.06 |
| `TIKT` | 41 | — | 1 | +$3.84 |
| `MAKE` | 9 | 15 | — | +$1.71 |
| `EPST` | 4 | 11 | — | +$1.34 |
| `ANTI` | 12 | 7 | — | **−$5.56** |

One utterance resolves the weekly *and* the monthly together. `ANTI` is what that looks like when
it goes the wrong way in both at once. `contest_key_of` does not catch this: it groups within a
series prefix and never claims to group across them, so this correlation is invisible to the cap
in either key. Recorded as a limitation, not fixed here.

`KXTRUMPSAYMONTH` is therefore **barred** in the manifest (a veto that refuses every book,
including live) and `KXTRUMPSAYCOMPANY` is **`in_review`** — same monthly structure, not yet
caught by it, 24% own-weight at k=38. Reasons are recorded in the manifest's `reasons` block.

### The same collision, one layer down

`registry.entry_for` matches by **longest prefix**, which is deliberate — it is what lets a
specific series be barred underneath a graduated family. The cost is that a series with no row of
its own inherits the nearest prefix row, **including its `rules_reviewed_at`**. Before this
change, `KXTRUMPSAYMONTH` and `KXTRUMPSAYCOMPANY` were inheriting `KXTRUMPSAY`'s 2026-09-08
sign-off: the ledger claimed a human had read rules nobody read.

Giving both an explicit row fixes it for this family, and
`tests/test_series_registry.py::test_a_signed_row_never_lends_its_signature_to_a_longer_series_ticker`
pins the invariant for every **signed** row. Unsigned graduated rows leak `graduated` the same way
— 12 known cases, mostly the `KXMLBHRDERBY*` family under `KXMLBHR` — which is a wider fix tracked
separately.

## Isolation

The requirement was that these be readable **independently of everything already running**. They
are, structurally:

| | |
|---|---|
| Paper only | Both are variant books; the live mirror fires only for tags in the live arm set, and neither tag is in it. |
| Fresh tape | A new tag inherits no paper state. Each epoch starts at its arming instant. |
| No shared knob touched | Every parameter is per-book — `contestcap` included, which is why the cap can run beside an uncapped twin at all. No global setting changes. |
| No taxonomy change | `SERIES_TYPES` is untouched. |
| Additive `onlyx` | The key defaults to `[]` for every existing book, which admits everything — inert for the whole running cohort. |

**One exception, stated plainly:** barring `KXTRUMPSAYMONTH` is *not* per-book. `registry.admits`
refuses a barred series even for a book naming no minimum tier, so the series leaves the candidate
set of **every** mmsell book, live arms included, on merge. That is exposure-*reducing* on a
series measured at −7.5 edge, which is the safe direction, but it is a change to every book's
candidate stream and is called out here rather than buried.

The one thing the tapes share with every other book is the **scan**: books are evaluated against
the same volume-ranked event list. That is intended — it is what makes the `mmsell10` comparison
meaningful — and it is not contamination, since one book's decision does not alter another's
candidate set.

## Reading them

Two comparisons, and they answer different things:

- **`Rmmsell1` vs `mmsell10`** — does the reviewed universe beat the unreviewed one? Same band,
  same ceiling, same scan.
- **`Rmmsell2` vs `Rmmsell1`** — does the cap help inside a universe we understand? Same
  universe, same everything, cap only.

Compare over each tape's **own window**, per-trade and on total dollars, the way the scan-depth
experiment was read. **Do not** compare either against `mmsell10`'s lifetime numbers: they cover
a different market regime.

Expect this to be slow. Twenty-four series out of 364 known means a trade rate well below
`mmsell10`'s, and `Rmmsell2` will take fewer trades again — that is the cap working, not a fault,
but it means the capped-vs-uncapped comparison is the later of the two to become readable. A thin
sample is a `HOLD`, not a verdict.

Note the selection honestly: these series were signed **partly on their own historical P&L**
(check 4 of the review). Some of the edge `Rmmsell1` shows is therefore in-sample by construction.
The out-of-sample claim is only about what the tapes do from their arming instant forward, which
is exactly why the fresh tags matter. `Rmmsell2` vs `Rmmsell1` is unaffected by this — both sides
share the selection, so the cap's effect is clean even where the universe's is not.

## Widening the universe

Batches 6, 7, 8 will sign more series. The tapes do **not** pick them up automatically, and that
is deliberate: a book whose universe grows underneath it produces evidence that cannot be pooled
across the change. Widening is an operator act, and it widens **both** tapes together or the pair
stops being a controlled comparison:

1. `python scripts/reviewed_tape_spec.py` — regenerate; read the diff.
2. Decide, deliberately, that the running evidence is worth resetting.
3. New env value **and** a recorded Experiment OS epoch (changed world) on both arms.

`scripts/reviewed_tape_spec.py --check` reports the divergence between the manifest and this
document. A non-zero exit is a **prompt**, not a bug: it means series have been signed since the
tapes were last widened.

## Registration — the two independent requirements

Same shape as `Gmmsell2` (`docs/handoffs/HANDOFF-gmmsell2-registration.md`). Both are required;
neither implies the other, and **doing only one produces a book that looks armed and trades
nothing**:

1. **Experiment OS deployment arms** registered to the tags `Rmmsell1` and `Rmmsell2`.
   Enforcement is `NEW_ONLY`: `tracker._book_is_admissible` refuses any tag without an active arm
   at the write path, and the book is reported in `summ.blocked_books`.
2. **`MMSELL_VARIANTS`** extended with both spec lines above, via the ops `env` transport.
   Setting it redeploys the worker.

Because this is a new question ("does a reviewed universe beat an unreviewed one, and does the cap
help inside it?") rather than a new world or a new answer to an old question, the sanctioned shape
is a **new experiment** with these two arms, not an epoch cut or a version bump on
`mmsell-correlation-cap`. Its external comparator (`mmsell10`) is an existing paper book in the
same snapshot, so it is a legitimate internal control rather than the cross-snapshot delta that
v1's own reasoning refused — and the cap question is answered **within** the experiment by its own
two arms, which is what keeps it off `mmsell-correlation-cap` entirely.

**Neither step has been taken.** Registering arms is durable experiment state and changing
`MMSELL_VARIANTS` redeploys production; both need the operator's explicit go.
