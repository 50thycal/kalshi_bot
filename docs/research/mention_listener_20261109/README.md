# Mention listener prototype — 2026-11-09

Research attachment for [the feasibility note](../../MENTION_LIVE_LISTENER_FEASIBILITY_20261109.md).
Not a collector, not a trading module: nothing here places orders, reads keys or runs in the
worker.

## Files

| File | What it is |
|---|---|
| `mention_listener.py` | Core, stdlib only: `parse_rules` (market text → `MentionSpec`), `Matcher` (+ `find_fuzzy`, a labelled non-trading tier), `StreamDetector` (PROVISIONAL / CONFIRMED events from revisable ASR output), `HazardModel` (in-play Poisson probability with a context hook where an LLM would sit) |
| `asr_e2e.py` | Real audio → sherpa-onnx streaming recognizer (100 ms chunks) → detector → scored against the human reference |
| `rescore.py` | Re-scores saved ASR text under the current matcher (retests matcher fixes without re-decoding) |
| `tape_population.py` | Per-event Kalshi tape analysis: in-show volume share, stale-quote lifts, jump speed |
| `hazard_vs_tape.py` | Constant-hazard model vs the live market path, naive ±10¢ rule |
| `targets_prereg*.json` | Target slates fixed **before** each call's audio was fetched |
| `e2e_*.json` | End-to-end results (include the recognizer's full text and every detection event). The `asr_text` fields are machine transcripts of Rev's Earnings-21 audio, © Rev.com, licensed CC BY-SA 4.0; they are redistributed here under that license, as are any derivative counts |
| `tape_wn06.json`, `tape_wn05.json`, `tape_population_20261106.json` | Derived market evidence (no raw trades) |
| tests | `tests/test_mention_listener_research.py` (53) |

## Reproduce

```bash
python3 -m pytest tests/test_mention_listener_research.py -q          # 53 pass
pip install sherpa-onnx numpy sentencepiece                            # + ffmpeg on PATH
# model: github.com/k2-fsa/sherpa-onnx/releases asr-models/
#        sherpa-onnx-streaming-zipformer-en-2023-06-26.tar.bz2
# hotwords need bpe.vocab: write sp.id_to_piece(i)\tsp.get_score(i) for each piece of bpe.model
# audio/reference: github.com/revdotcom/speech-datasets earnings21 (git lfs pull --include=...)
python3 asr_e2e.py --audio 4386541.mp3 --ref 4386541.nlp --model-dir <zipformer> \
    --targets targets_prereg.json --out e2e_hotwords_s2.json --hotwords --hotword-score 2.0
```

Kalshi tapes come from the ops channel (Claude's sandbox cannot reach Kalshi):
`{"type":"script","name":"kalshi_desk_board","args":["--ticker","<T>","--trades","1000"]}`.

## Evidence receipts (ops request ids, 2026-11-09)

| id | content |
|---|---|
| `rl-mention-ct-1` | Control Tower read (role startup; no duplicate experiment) |
| `rl-mention-inv-1` | `--search MENTION --hours 2000`: 455 open markets, 32,617 contracts / 24 h |
| `rl-mention-ev-1`, `rl-mention-ev-wn06`, `rl-mention-ev-wn05` | event structures and market lists |
| `rl-mt-kxburnhammention-26nov04-budg`, `rl-mt-rningsmentionhims-26nov02-test`, `rl-mt-kxburnhammention-26nov04-ukra` | rules text + pre-event tapes |
| `rl-mt-wn06-*` (17) | every market of `KXWORLDNEWSMENTION-26NOV06` with up to 1,000 trades |
| `rl-mt-wn05-*` (17) | the same for `-26NOV05` (replication) |

`ops/results/` keeps only the newest 80 files; the derived JSON here is the durable copy.

## Cycle log

| Cycle | Focus | Findings | Fix and retest |
|---|---|---|---|
| 1 | Matcher correctness vs Kalshi's real rules text | 38/38 unit tests passed first time; adversarial probes then found 5 defects: dotted/spelled acronyms, **double count on overlapping alternatives**, "&", spoken numbers, hyphenated spellings. Two ambiguities ("a i think", "US"/"us") surfaced as labels and warnings | Fixed; 9 regression tests; 47/47 |
| 2 | Real audio end to end (Cumulus Media call) | 0 events, because sherpa 2.x prefixes words with a space; after the fix 7/11 said words found, 0 false YES. Domain words misheard (PANDANA / POD CASTING). Confirmed text lags provisional by a 3 s median (11 s max) | Tokenizer fixed; recognizer text saved in results |
| 3 | Hotword biasing toward the slate | 7→9 of 11, 0 false YES; occurrence recall 15/42 → 27/42. At score 4.0: 10/11 but a **false YES on "Guidance"** | Score frozen at 2.0 |
| 4 | Held-out call (ZAGG), frozen setting | 11→13 of 19, 0 false YES; "Inventory" 11 said / 1 caught; brand names misheard | No tuning (held out); ceiling recorded |
| 5 | Third untouched call (Lands' End); fuzzy near-miss tier, rule fixed before fetch | Exact 12/16, 0 false YES; fuzzy +1 word but 4/10 of its hits wrong or excluded inflections. Matcher missed "e commerce" (13×); "catalogue" spelling; hotword pulled "non-GAAP" to "non DEBT" | Hyphen split/join + spelling variants (≥5-letter words); fuzzy kept non-trading; a new test caught a plural bug in variants. Rescore: 12→14/16, other calls unchanged; 53/53 |
| 6 | Prediction box vs the live market (11-06 show) | Market below the constant-hazard model at 70% of checkpoints; the naive rule's YES side lost; the net came from one word | None. Finding: the LLM must forecast the rundown |
| 7 | Deliverable review: page rendered with Playwright at 1280 and 390 px, light and dark | Phone page scrolled sideways 19 px (grid track sized by content; unbreakable repo paths); chart text ~5 px on phones (800-unit drawings squeezed); end labels clipped | Grid `minmax(0,1fr)`, paths wrap, charts drawn at real pixel width with fewer ticks; re-render: scroll width = viewport on all 4, no console errors |
| 8 | Replication on the 11-05 show | 54% on air, $464 lifted, jumps 0,0,0,1,2,**12,16** s: the race is usually ≤ 2 s but not always | Note and page updated; recommendation unchanged |
