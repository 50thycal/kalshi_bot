# Desk postmortems — every losing pick, and every closed edge class

One entry per losing pick, written on settlement, tagged with the failure class. A class is
**closed** when 30 settled picks sit below break-even; the closure line goes at the top of §2
and no further pick in that class is written until a mechanically new premise reopens it.

Failure tags (extend as needed, never rename): `priced` (the market already knew), `source`
(misread the settlement rule or source), `late` (information arrived after the crowd), `size`
(fee/spread ate a real but thin edge), `variance` (thesis held, outcome did not), `process`
(lookahead, wrong unit, pooled read — the desk's own error).

## 1. Losing picks

| pick_id | settled | ticker | tag | one line on what the desk got wrong |
|---|---|---|---|---|

## 1a. Process failures found before settlement

A thesis error is knowable before the outcome is. Recording it here on discovery, rather than
waiting for the grade, is what keeps the postmortem honest: the outcome cannot then be used to
decide whether the reasoning was sound. The pick's original row in the ledger is never rewritten
(R9); the correction is a dated line here and a `corrected_*` note appended to the row.

| pick_id | found | tag | what the desk got wrong |
|---|---|---|---|
| `D-2026-09-19-001` | 2026-09-20 | `source` | **The thesis named a settlement source the contract does not.** The pick asserted `KXDIESELW-26SEP21-T6.52` settles on the EIA weekly on-highway diesel survey, and bought a +5.5–6.7¢ basis of EIA over AAA that the market was said to be ignoring. The contract's own rules text, read on 2026-09-20, says only: *"If the Diesel Price on September 21, 2026 is above $6.52, then the market resolves to Yes."* There is no secondary rules block and no named source. The twin market `KXDIESELD-26SEP21` ("Diesel prices tomorrow") carries the **identical** rule text, the same close and the same expiration, and its ladder overlays the weekly's strike for strike. The two series settle on **one price print on 2026-09-21** — the weekly is a coarse-strike ladder on the same daily number, not a weekly average. So the basis the pick was built on does not exist in the contract, and the market's price was not ignoring it. Entry at 76¢ against a market that has since held 46/53 is an overpay of roughly 26¢ per contract independent of how it settles. |

## 2. Closed edge classes

| class | closed on | picks | realized win % | break-even % | why |
|---|---|---|---|---|---|
