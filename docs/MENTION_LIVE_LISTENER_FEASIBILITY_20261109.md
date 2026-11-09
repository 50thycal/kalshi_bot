# Live mention listener: feasibility of the operator's sketch

SESSION: Research Lab / MODE: FEASIBILITY ANALYSIS + PROTOTYPE / ENFORCEMENT: NEW_ONLY / AS OF: 2026-11-09

**Research verdict: do not build the full live bot now. Build only the cheap first-mover probe
(§8) if the owner wants to continue.** The sketch is buildable: every box in it ran here on real
inputs. But the detection half is a speed race that someone usually wins within one or two
seconds, and the prediction half has to beat a market that already prices in-play decay better
than a naive model. Regulation is tightening around the whole category. No experiment, book,
gate, collector, env var or ops-runner allowlist changed.

## The request and how it maps to the record

The operator's sketch (photo, 2026-11-09) has four boxes:

```
Mention market ─► rules for what counts as a mention
  + Live stream sources ─► stream every source, transcribe, analyse the words
  + LLM predicts next words ─► P(word will be said) from the stream so far
  ⇒ Kalshi bot ─► manages mention positions on that data
```

The repo has screened half of this three times as **MENTIONLOCK** (buy a word once it is said,
before the quote moves): [07-12 run 2](IDEA_MODEL_20260712_run2.md) S6,
[09-12](IDEA_MODEL_20260912.md) T1 and the [09-30 mentions screen](IDEA_MODEL_MENTIONS_20260930_CHATGPT.md)
C9. All three held it for the same two reasons: no ground-truth word timestamps, and adjacency
to SPORTLOCK, which was killed because a watched lock is a latency race. The **prediction half**
(in-play probability that a word is still coming) was never screened. The pre-call corpus
idea (MENTION-corpus / C1) stays on HOLD for its own reasons ([census](MENTION_CORPUS_CENSUS_20260930.md))
and is untouched here. Not a duplicate of any open experiment; not a revival, because this note
adds the measurement the earlier holds asked for and promotes nothing.

## 1. What the market looks like today (measured)

All Kalshi data came through the ops channel (`kalshi_desk_board`, public endpoints, no key).
Request ids are listed in the [attachment README](research/mention_listener_20261109/README.md).

| Measure (2026-11-09 18:58 UTC) | Value |
|---|---|
| Open markets in category `Mentions` | 455, across ~20 events |
| Contracts traded in the last 24 h across all 455 | 32,617 |
| Busiest single market, 24 h | 3,753 contracts (Altman / DealBook "Elon / Grok") |
| Open `KXTRUMPMENTION` markets | 0 (cause not verified) |
| Mention share of the live liquidity-incentive pool | about $3/day (journal, 2026-11-08) |

The open-market snapshot understates the category, because most trading happens **during the
event**, after which the market closes. The daily ABC World News Tonight event
(`KXWORLDNEWSMENTION`, broadcast 23:30–00:00 UTC) shows this. Its markets opened about 18:45
UTC, closed at the end of the broadcast, and settled on the broadcast itself:

| Show | Markets | Contracts | Traded during the 30-min broadcast | Words said live |
|---|---:|---:|---:|---:|
| 2026-11-06 | 17 | 96,882 | 45% (Trump 5+: 91%) | 10 |
| 2026-11-05 (replication) | 17 | 134,618 | 54% | 13 |

Rules text (Kalshi, fetched today) settles these on **"video … using the live broadcast or
stream"**, with transcripts only as a fallback. Earnings-call series use the call video. That
makes the broadcast itself the source of truth, which is what a live listener needs.

## 2. The detection race (measured)

For every word said during the 11-06 show that was not already priced above 80¢, the price
went from halfway-to-95¢ to the first ≥95¢ print in **0, 0, 0, 1 and 2 seconds**. The 11-05
replication gave 0, 0, 0, 1, 2, **12 and 16** seconds. Across both shows, **10 of 12 contested
jumps completed within 2 s; 2 left a 12–16 s window** (Iowa/Kansas, Europe; whether those were
ambiguous utterances cannot be told from the tape). The clearest
case is Data Center: at 23:31:28 one second contains 26 YES-taker fills walking the book from
47¢ to 92¢; at 23:31:30 everything prints 95–99¢. That is a single aggressive sweep.

What the fastest detector collected (YES-taker lifts below 95¢ in the minute before the
first ≥95¢ print, priced at least 5¢ above the pre-window level):

| Word (11-06) | Pre-jump | Contracts lifted | Gross edge |
|---|---:|---:|---:|
| Measles | 7¢ (decayed during the show) | 759 | $382 |
| Dow | 32¢ | 308 | $89 |
| Hegseth | 61¢ | 526 | $75 |
| Data Center | 45¢ | 212 | $56 |
| AI / Artificial Intelligence | 62¢ | 201 | $34 |
| all 10 said words | | | **$641** |

The 11-05 show lifted **$464** (Oil/Gas $230, Poll $85, Europe $51). So there is a real prize,
of the order of **$450–650 per show**, and it is taken within one to
two seconds by whoever hears the word first. Measles also shows liquidity left 14 s after the
first lift, so the race is not always perfectly tight. Without utterance timestamps we cannot
say how far behind the broadcast the winner is. Measuring that is the probe in §8.

**Where the seconds go.** Speech-to-text is not the bottleneck: the local streaming recognizer
here ran at 0.07× real time on 2 CPU threads, with a median 0.84 s from word to provisional
detection. The delay that decides the race is the **feed**. Published figures put YouTube Live
at roughly 5–60 s behind real time depending on latency mode, and broadcast and cable delivery
at several seconds more than over-the-air TV ([web research](#sources)). A bot on a public
internet stream would hear the word several seconds after an over-the-air or venue-feed
competitor. That is exactly the SPORTLOCK failure mode.

## 3. Rules: the box that has to be exactly right

Kalshi's payout criteria (verbatim, fetched today): the exact word or phrase, or its plural or
possessive; other inflections do not count; "A / B" means either; content must be in the
original broadcast; hot-mic and archival content excluded. Observed practice also counts
hyphen-joined forms ("non-fake news" paid "Fake News") while terms inside longer words do not
("wildfire" does not pay "fire"). Count markets ("Trump (5+ times)") need a correct count.

The prototype's `parse_rules` turns the market text into a spec and **warns instead of
guessing** when a clause is missing. It also warns on terms that collide with everyday words
("US" vs "us"). A bot that misreads settlement wording buys the wrong contract; that is the
costliest error class here.

## 4. The prototype and what five test cycles found

[`research/mention_listener_20261109/`](research/mention_listener_20261109/README.md):
`mention_listener.py` (rules → spec, matcher, streaming detector, hazard model; stdlib),
`asr_e2e.py` (real audio → streaming ASR → detector → scored against a human transcript),
`tape_population.py`, `hazard_vs_tape.py`, and 53 tests in
`tests/test_mention_listener_research.py`.

End-to-end inputs: three real earnings calls from Rev's earnings21 corpus (CC BY-SA 4.0) and the
sherpa-onnx streaming zipformer (Apache-2.0), audio fed in 100 ms chunks like a live stream.
Each call's target words were **fixed in a committed file before its audio was fetched**,
like a real Kalshi slate (a mix of likely and unlikely words).

| Cycle | Focus | What broke or what it showed | Change, retested |
|---|---|---|---|
| 1 | Matcher correctness | 38 unit tests passed first time, then adversarial probes found 5 defects. "A.I."/"A I" never matched. **Overlapping alternatives double-counted** ("Trump / President Trump" counted 2 for one utterance, a false YES on 5+ markets). "&" was dropped. "5G" didn't match "five g". "e-mail" didn't match. | All fixed; 9 regression tests |
| 2 | Real audio baseline | Integration bug: the recognizer marks word starts with a space, so no word ever matched (0 events). After the fix: 7/11 said words found, 0 false YES. "Pandemic" said 8× was heard as PANDANA/PANAMIC/PANDAMIC. Confirmed text lags provisional by a median of 3 s (max 11 s). | Fixed; documented that a bot must act on provisional hits |
| 3 | Bias recognition toward the slate (hotwords) | 7→9 of 11, still 0 false YES; word recall 36%→64%. At a stronger bias (4.0) recall reached 10/11, but "Guidance" (never said) became a **false YES**. | Fixed setting at 2.0 |
| 4 | Held-out call, frozen setting | Gain held (11→13 of 19, 0 false YES), but recall is capped near 2/3. "Inventory" was said 11× and caught once ("INVENTURIES"). Brand names: "Mophie" became MORPHY, "Samsung" became SAMSON. | Diagnosis: small CPU model's ceiling |
| 5 | Third untouched call; fuzzy near-miss tier | Exact tier 12/16, 0 false YES. Fuzzy added 1 word but **4 of its 10 hits were wrong or excluded inflections** ("promotional", "digitally", "investor" for Inventory), so it is unsafe as a trigger. Also: "E-commerce" (said 13×) never matched spoken "e commerce" (matcher bug); "catalogue" spelling; "non-GAAP" heard as **"non DEBT"** (hotword-induced false hit). | Hyphen split/join + spelling variants; fuzzy kept as a labelled non-trading tier. Third call 12→14 of 16; others unchanged |
| 6 | Prediction box vs the market | On the 11-06 show the market sat **below** a constant-hazard model at 70% of checkpoints. The crowd already knows a news story either leads or isn't coming. A naive "model ≥10¢ off the market" rule lost on its YES side; its +94¢ net came from one word. | No change: evidence the LLM must forecast the rundown, not a hazard rate |

**Final end-to-end tally (frozen setting, three real calls):** 36 of 46 said words detected
(78%), **0 false YES on 14 unsaid words**. Fourteen negatives is too few to claim a low
false-positive rate, and cycle 3 showed one appearing under stronger bias.

## 5. Who else is doing this

Others are building exactly this, in the open. Sources are listed below. The Sonist and
CFTC items were re-checked independently; the rest come from one web-research pass.

- **Sonist** sells "live mention trading": live audio or captions → word-by-word transcript →
  IOC orders on Kalshi and Polymarket through the user's account. Its claims are unverified,
  it shows no public track record, and auto-trading is waitlist-only.
- **Open-source pipelines** (0–9 GitHub stars each). `maloyan/polymarket-speech` runs
  yt-dlp/ffmpeg → Deepgram Nova-3 → Aho-Corasick → Polymarket orders, with a kill switch.
  `Aferrara3/kalshi-mentions` runs YouTube Live → faster-whisper → Kalshi paper logging; its own
  notes say platform delay dominates. Also `sha-sta/speech-signal-pipeline`, which strips
  strategy code from a production system, and `jsteng19/kalshi-research` (word-rate models).
- **`signxx/mention-rules`** (MIT): Kalshi and Polymarket rule matchers that reproduce 2,306
  settled outcomes at 98.3%, from a project that tested 5 mention edges and falsified 4.
- **Research:** arXiv 2602.21229 forecasts Kalshi earnings-call mentions with LLMs; using the
  market price as a prior beats the market baseline.
- **Commercial low-latency transcripts:** Aiera and Benzinga sell live earnings-call transcripts.

Plain word detection is therefore a commodity. Any edge must come from a faster or more
authoritative feed, flawless rule reading, or forecasting the words still to come.

## 6. Regulatory and venue risk

- **CFTC staff advisory, 2026-09-22 (Letter 26-27):** mention contracts are *presumptively
  readily susceptible to manipulation*. Exchanges listing them should expect a heightened
  showing, position limits, restricted-participant lists and event-window surveillance. The
  advisory is non-binding.
- Kalshi **paused sports mention markets** around 2026-08-01 during a CFTC review (reported).
- Enforcement: a former White House teleprompter operator paid about $172.5k for trading
  presidential mention markets on advance copies of remarks.
- Position limits sized against manipulation would also cap a legitimate fast bot's size.

## 7. What it would take to build (estimates, not measurements)

| Box | Build | Hard part | Ops cost |
|---|---|---|---|
| Rules | Prototype exists; harden plus a human-review queue for warnings | Ambiguous words, count markets, unusual templates | Low |
| Sources | One adapter per source type: YouTube/HLS, C-SPAN, earnings webcasts or dial-in, network TV, X Spaces | **The hardest part, as the operator said.** Winning needs the earliest feed: an over-the-air tuner and antenna or a phone dial-in, which Railway cannot host. Each event must be mapped to a feed by hand. | Hardware and a location for over-the-air; per-minute fees for dial-in or commercial ASR |
| Speech-to-text | Local model works (0.07× real time) but caught 78% of said words; a commercial streaming model with keyword prompting should do better (untested here, needs a key) | Domain words and brand names are the worst case; bias raises false hits | Commercial ASR is billed per audio minute (check current pricing) |
| Prediction | Hazard model with an LLM context hook exists; the real job is forecasting the rundown | Must beat a market that already decays faster than naive models | Small |
| Execution | Kalshi IOC orders on provisional hits, with per-event caps | False triggers (revised partials, homophones, hot mic, replayed clips) | Under NEW_ONLY a book needs Experiment OS registration; live money is a hard stop |

## 8. Recommendation and the probe that decides it

Against the $100/month north star the prize is not the issue: one show held about $600 of
lifted stale quotes, so winning even a small share of one daily show would clear the bar.
**The only question that matters for the detection half is "are we ever first?"** No amount
of offline work answers it.

**Cheapest falsifying probe (for owner decision, not started):** for 10 broadcasts of one
daily show, run the listener on the best feed we can get, with no orders. Log our
provisional-detection wall-clock time for each said word, and pull the market tape afterwards.
- **PASS:** our median detection precedes the market's mid-cross by ≥ 1 s on ≥ 6 of 10 said-word jumps.
- **KILL:** we are later than the mid-cross on most jumps. MENTIONLOCK then dies on measured
  evidence rather than adjacency.

This needs a collector running during broadcasts, which is a new service, so it is an
**OWNER DECISION**. The prediction half should not be built before an LLM rundown forecaster
beats the market's own in-play decay on a pre-registered multi-show sample.

## 9. Limits of this note

- Market evidence is two consecutive shows of one series. Jump speeds come from trade
  timestamps at 1 s resolution, not book updates. Last-trade prices are not executable quotes.
- Detection latency is algorithmic (audio processed minus the recognizer's own word time). The
  reference transcripts carry no word times. Network and feed delay are excluded.
- Earnings21 calls are from 2020 and phone-quality. TV audio and today's slates may behave differently.
- Testing against the `mention-rules` package as a code oracle was **not run**: installing or
  reading that external package was refused by this session's permission policy. Its published
  rule table was used as test data instead.
- Landscape facts come from web search. Sonist and the CFTC advisory were re-checked; the rest
  were not independently verified.

## Sources

- Kalshi market rules, tapes and inventory: ops results listed in the [attachment README](research/mention_listener_20261109/README.md)
- CFTC Letter 26-27: https://www.cftc.gov/csl/26-27/download ; coverage: https://decrypt.co/379030/cftc-staff-advisory-says-prediction-market-mention-contracts-invite-manipulation
- Sports mention pause: https://www.legalsportsreport.com/272940/report-kalshi-paused-sports-mention-markets-amid-federal-review/
- Teleprompter case: https://www.npr.org/2026/08/13/nx-s1-5930689/cftc-probe-mention-markets-prediction-markets-kalshi
- Sonist: https://polymart.app/sonist , https://hunted.space/product/sonist
- Repos: https://github.com/maloyan/polymarket-speech , https://github.com/Aferrara3/kalshi-mentions , https://github.com/sha-sta/speech-signal-pipeline , https://github.com/signxx/mention-rules , https://github.com/jsteng19/kalshi-research
- arXiv 2602.21229: https://arxiv.org/html/2602.21229v1
- YouTube live latency modes: https://support.google.com/youtube/answer/7444635
- Earnings21: https://github.com/revdotcom/speech-datasets ; sherpa-onnx models: https://github.com/k2-fsa/sherpa-onnx/releases
