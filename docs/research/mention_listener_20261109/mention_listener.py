"""Mention-listener prototype — the core loop of a live mention-market bot, minus the trading.

Research attachment for docs/MENTION_LIVE_LISTENER_FEASIBILITY_20261109.md. NOT a trading
module: nothing here places, sizes or routes an order, reads a key, or touches the worker. It
exists to make the feasibility analysis concrete and testable.

Four parts, matching the operator's sketch (rules -> live stream -> LLM -> bot):

  parse_rules()    Kalshi market text -> MentionSpec (what counts, who, which source, count k)
  Matcher          token-boundary matching under Kalshi's payout criteria
  StreamDetector   revisable streaming-ASR hypotheses -> PROVISIONAL / CONFIRMED mention events
  HazardModel      in-play P(word still said | not yet said, elapsed) — the "LLM predicts" box,
                   with the LLM reduced to a pluggable context multiplier

Stdlib only. Kalshi rule semantics encoded here (from the market text fetched 2026-11-09 via
the ops channel, and cross-checked against the MIT `mention-rules` package):
  * the exact word/phrase, or its plural or possessive, counts;
  * other grammatical/tense inflections do not ("deport" != "deported");
  * a term inside a longer word does not count ("fire" not in "wildfire");
  * hyphen-joined occurrences are observed to count ("non-fake news" paid "Fake News") — the
    rules are silent, so this is flagged as an inference;
  * "A / B" means either alternative satisfies the market;
  * "(5+ times)" / "5+ times" sets a count threshold.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------------------------
# 1. Rules -> spec
# ---------------------------------------------------------------------------------------------

_THRESH_RES = (
    re.compile(r"^(?P<base>.*?)\s*\(\s*(?P<k>\d+)\s*\+\s*times?\s*\)\s*$", re.I),  # Iran (3+ times)
    re.compile(r"^(?P<base>.*?)\s+(?P<k>\d+)\s*\+\s*times?\s*$", re.I),            # Tax 5+ times
    re.compile(r"^(?P<base>.*?\D)\s+(?P<k>\d+)\s*\+\s*$", re.I),                   # Border 5+
)


@dataclass(frozen=True)
class MentionSpec:
    term: str                                   # the market's strike text, verbatim
    alternatives: tuple[tuple[str, ...], ...]   # each alternative as lowercase tokens
    threshold: int = 1                          # how many occurrences pay YES
    plural_possessive: bool = True
    compounds_count: bool = False               # Kalshi: "wildfire" does NOT pay "fire"
    hyphen_parts_count: bool = True             # observed behaviour, rules silent
    speaker: str | None = None
    source: str = "unknown"                     # live_video | transcript | unknown
    excludes_archival: bool = False
    excludes_hot_mic: bool = False
    warnings: tuple[str, ...] = ()


_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
         "fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def spoken_number(n: int) -> list[str] | None:
    """0-99 as words ("21" -> twenty one). Larger numbers return None (left as digits)."""
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        t, o = divmod(n, 10)
        return [_TENS[t]] + ([_ONES[o]] if o else [])
    return None


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens. Curly apostrophes are unified; a possessive stays attached
    ("russia's"); "&" reads as "and"; dotted acronyms collapse ("A.I." -> "ai"); hyphenated
    words are kept whole here and expanded by Matcher."""
    text = text.replace("’", "'").replace("‘", "'").replace("&", " and ")
    text = re.sub(r"\b((?:[A-Za-z]\.){2,})", lambda m: m.group(1).replace(".", ""), text)
    return re.findall(r"[a-z0-9]+(?:[-'][a-z0-9]+)*'?", text.lower())


def parse_term(term: str) -> tuple[tuple[tuple[str, ...], ...], int]:
    raw = term.strip()
    k = 1
    for rx in _THRESH_RES:
        m = rx.match(raw)
        if m:
            raw, k = m["base"].strip(), int(m["k"])
            break
    alts = []
    for part in raw.split("/"):
        toks = tuple(tokenize(part))
        if toks:
            alts.append(toks)
    return tuple(alts), k


def speech_variants(alt: tuple[str, ...]) -> list[tuple[tuple[str, ...], str]]:
    """How an alternative can surface in streaming-ASR output, which has no punctuation and
    often spells numbers and acronyms out. Each variant carries a label so a hit through it
    stays visibly an inference: ("ai",) -> ("a","i") acronym-spelled; ("5g",) -> ("five","g")."""
    out = [(alt, "")]
    # hyphenated term words: ASR writes "e-commerce" as "e commerce" or "ecommerce"
    if any("-" in t for t in alt):
        split = tuple(p for t in alt for p in t.split("-") if p)
        joined = tuple(t.replace("-", "") for t in alt)
        out += [(split, "hyphen-split"), (joined, "hyphen-joined")]
    # US/UK spellings: deterministic, unlike fuzzy matching (cycle 5)
    for i, t in enumerate(alt):
        for a, b in _SPELLING:
            if len(t) >= 5 and t.endswith(a):
                out.append((alt[:i] + (t[: -len(a)] + b,) + alt[i + 1:], "spelling-variant"))
    # digits <-> words, and letter/digit runs split ("5g" -> "5","g")
    toks: list[str] = []
    changed = False
    for t in alt:
        parts = re.findall(r"\d+|[a-z]+", t) if re.search(r"\d", t) else [t]
        for p in parts:
            words = spoken_number(int(p)) if p.isdigit() else None
            if words:
                toks.extend(words)
                changed = True
            else:
                toks.append(p)
        changed = changed or len(parts) > 1
    if changed:
        out.append((tuple(toks), "spoken-number"))
    # short acronyms spelled letter by letter ("ai" -> "a i"), never single letters
    if len(alt) == 1 and 2 <= len(alt[0]) <= 4 and alt[0].isalpha() and alt[0] not in _NOT_ACRONYM:
        out.append((tuple(alt[0]), "acronym-spelled"))
    return out


# variants that are still real words (take plural/possessive); spelled letters/numbers do not
_INFLECTABLE = {"", "spelling-variant", "hyphen-split", "hyphen-joined"}

# US -> UK suffix spellings an ASR model trained on mixed text may emit ("catalogue")
_SPELLING = (("log", "logue"), ("logue", "log"), ("or", "our"), ("our", "or"), ("ter", "tre"),
             ("tre", "ter"), ("ize", "ise"), ("ise", "ize"), ("ense", "ence"), ("ence", "ense"))

# terms that collide with everyday words in a transcript ("US" the country vs "us")
_FUNCTION_WORDS = {"us", "it", "may", "will", "can", "march", "august", "well", "right",
                   "trust", "bill", "mark", "turkey", "china", "jordan", "chad", "rush"}

# common short words that are words, not acronyms — spelling them out would be noise
_NOT_ACRONYM = {"oil", "gas", "tax", "dow", "fed", "war", "job", "ice", "pope", "iran", "iowa",
                "data", "grok", "beef", "safe", "home", "free", "elon", "rate", "debt", "cut",
                "cuts", "bill", "deal", "fire", "gold", "food", "farm", "army", "navy", "golf"}


def parse_rules(term: str, rules_primary: str = "", rules_secondary: str = "") -> MentionSpec:
    """Market text -> spec. Anything we cannot read becomes a warning for human review —
    a mention bot that guesses at settlement wording is a bot that buys the wrong contract."""
    alts, k = parse_term(term)
    prim, sec = rules_primary or "", rules_secondary or ""
    text = f"{prim}\n{sec}"
    warnings: list[str] = []

    speaker = None
    m = re.search(r"If\s+(.+?)\s+says?\b", prim, re.S)
    if m:
        speaker = re.sub(r"\s+", " ", m.group(1)).strip()
    else:
        warnings.append("speaker not found in rules_primary")

    src = "unknown"
    low = text.lower()
    if re.search(r"video of .*?(live broadcast|stream|earnings call)", low):
        src = "live_video"
    elif "transcript" in low:
        src = "transcript"
    else:
        warnings.append("settlement source not recognised")

    pp = "plural or possessive" in low
    if not pp:
        warnings.append("no plural/possessive clause — matcher defaults to exact only")
    if "inflection" not in low:
        warnings.append("no inflection exclusion clause found")
    if "/" in term and "slash" not in low:
        warnings.append("slash alternatives used but rules do not define them")
    if k > 1 and not re.search(r"\d+\s*\+?\s*times|times", low):
        warnings.append("count threshold in term but not in rules text")
    for alt in alts:
        if len(alt) == 1 and alt[0] in _FUNCTION_WORDS:
            warnings.append(f"'{alt[0]}' is also a common word — speech-to-text cannot tell "
                            "the senses apart; a human must confirm each hit")

    return MentionSpec(
        term=term, alternatives=alts, threshold=k, plural_possessive=pp,
        speaker=speaker, source=src,
        excludes_archival="archival" in low or "previously aired" in low,
        excludes_hot_mic="hot mic" in low,
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------------------------
# 2. Matcher
# ---------------------------------------------------------------------------------------------

_IRREGULAR = {
    "man": "men", "woman": "women", "child": "children", "person": "people", "mouse": "mice",
    "crisis": "crises", "analysis": "analyses", "thesis": "theses", "index": "indices",
    "tooth": "teeth", "foot": "feet", "leaf": "leaves", "life": "lives", "wife": "wives",
}


def plural_forms(word: str) -> set[str]:
    w = word.lower()
    out = {w + "s"}
    if re.search(r"(s|x|z|ch|sh)$", w):
        out.add(w + "es")
    if re.search(r"[^aeiou]y$", w):
        out.add(w[:-1] + "ies")
    if w.endswith("o"):
        out.add(w + "es")
    if w in _IRREGULAR:
        out.add(_IRREGULAR[w])
    return out


def accepted_forms(word: str, plural_possessive: bool) -> set[str]:
    w = word.lower()
    if not plural_possessive:
        return {w}
    forms = {w, w + "'s", w + "'"}
    for p in plural_forms(w):
        forms |= {p, p + "'", p + "'s"}
    return forms


@dataclass(frozen=True)
class Hit:
    alternative: str
    token_index: int     # index of the FIRST token of the hit in the token stream
    surface: str
    via: str             # exact | inflected | hyphen-part (hyphen-part is an inference)


class Matcher:
    def __init__(self, spec: MentionSpec):
        self.spec = spec
        self._alts = []
        for alt in spec.alternatives:
            for form, label in speech_variants(alt):
                head = [{t} for t in form[:-1]]
                # inflections attach to the word, not to a spelled-out letter or number word
                tail = (accepted_forms(form[-1], spec.plural_possessive)
                        if label in _INFLECTABLE else {form[-1]})
                self._alts.append((alt, form, head, tail, label))

    @staticmethod
    def expand(tokens: list[str], hyphen_parts: bool) -> list[tuple[str, int, str]]:
        """(token, original index, how). A hyphenated token is offered whole, de-hyphenated
        ("e-mail" -> "email": the same word spelled two ways) and, if allowed, as its parts —
        never as a substring of an unhyphenated word."""
        out = []
        for i, t in enumerate(tokens):
            out.append((t, i, ""))
            if "-" in t:
                out.append((t.replace("-", ""), i, ""))
                if hyphen_parts:
                    out.extend((p, i, "hyphen-part") for p in t.split("-") if p)
        return out

    def find(self, tokens: list[str]) -> list[Hit]:
        """Hits in utterance order. Overlapping hits are ONE utterance: "President Trump"
        under "Trump / President Trump" counts once, the longest alternative winning — a
        double count would fire a false YES on a "5+ times" market."""
        stream = self.expand(tokens, self.spec.hyphen_parts_count)
        cands: list[tuple[int, int, Hit]] = []
        for alt, form, head, tail, label in self._alts:
            n = len(form)
            for s in range(len(stream) - n + 1):
                win = stream[s:s + n]
                if any(win[j][0] not in head[j] for j in range(n - 1)):
                    continue
                last = win[-1][0]
                if last not in tail:
                    continue
                idx = [w[1] for w in win]
                # a phrase must walk forward through the original tokens
                if any(b < a for a, b in zip(idx, idx[1:], strict=False)):
                    continue
                via = label or next((w[2] for w in win if w[2]), "") or (
                    "exact" if last == form[-1] else "inflected")
                cands.append((idx[0], idx[-1],
                              Hit(" ".join(alt), idx[0], " ".join(w[0] for w in win), via)))
        cands.sort(key=lambda c: (c[0], -(c[1] - c[0])))
        hits: list[Hit] = []
        last_end = -1
        for start, end, h in cands:
            if start <= last_end:
                continue
            hits.append(h)
            last_end = end
        return hits

    def find_fuzzy(self, tokens: list[str], min_ratio: float = 0.80,
                   min_len: int = 5) -> list[Hit]:
        """Near-miss tier for ASR misspellings ("PANDAMIC", "INVENTURIES", "POD CAST").
        Single-token terms of >= min_len letters only; a token, or two adjacent tokens merged,
        must reach `min_ratio` similarity to an accepted form. Exact hits are excluded, so this
        tier only ADDS candidates, each labelled `fuzzy`. It never settles anything: a fuzzy
        hit is a reason to look harder, priced as such (cycle 5 measures what it costs)."""
        from difflib import SequenceMatcher
        exact = {h.token_index for h in self.find(tokens)}
        out: list[Hit] = []
        for alt in self.spec.alternatives:
            if len(alt) != 1 or len(alt[0]) < min_len or not alt[0].isalpha():
                continue
            forms = accepted_forms(alt[0], self.spec.plural_possessive)
            i = 0
            while i < len(tokens):
                if i in exact:
                    i += 1
                    continue
                cands = [(tokens[i], 1)]
                if i + 1 < len(tokens) and i + 1 not in exact:
                    cands.append((tokens[i] + tokens[i + 1], 2))
                best = max(((max(SequenceMatcher(None, c, f).ratio() for f in forms), c, n)
                            for c, n in cands), key=lambda x: x[0])
                if best[0] >= min_ratio and best[1] not in forms:
                    out.append(Hit(alt[0], i, best[1], "fuzzy"))
                    i += best[2]
                    continue
                i += 1
        out.sort(key=lambda h: h.token_index)
        return out

    def count(self, text_or_tokens) -> int:
        toks = tokenize(text_or_tokens) if isinstance(text_or_tokens, str) else text_or_tokens
        return len(self.find(toks))

    def resolves_yes(self, text: str) -> bool:
        return self.count(text) >= self.spec.threshold


# ---------------------------------------------------------------------------------------------
# 3. Streaming detector
# ---------------------------------------------------------------------------------------------

@dataclass
class MentionEvent:
    term: str
    kind: str              # PROVISIONAL (partial hypothesis) | CONFIRMED (finalized segment)
    count: int             # cumulative count at the time of the event
    audio_t: float         # recognizer's estimate of when the word was spoken (s)
    detect_t: float        # audio position processed when the detector fired (s)
    surface: str

    @property
    def latency(self) -> float:
        return self.detect_t - self.audio_t


@dataclass
class StreamDetector:
    """Consumes streaming-ASR output. A partial hypothesis can be REVISED by the recognizer,
    so a word seen in a partial is PROVISIONAL; it is CONFIRMED once its segment is finalized.
    A trading layer would size differently on the two (or wait for `stable_updates` repeats)."""
    specs: list[MentionSpec]
    stable_updates: int = 2
    events: list[MentionEvent] = field(default_factory=list)
    _final_counts: dict = field(default_factory=dict)
    _partial_seen: dict = field(default_factory=dict)
    _provisional_fired: set = field(default_factory=set)

    def __post_init__(self):
        self._matchers = {s.term: Matcher(s) for s in self.specs}
        for s in self.specs:
            self._final_counts[s.term] = 0

    def _word_times(self, words: list[str], times: list[float] | None) -> list[float]:
        if times and len(times) == len(words):
            return times
        return [0.0] * len(words)

    def on_partial(self, words: list[str], times: list[float] | None, audio_pos: float):
        toks = [w.lower() for w in words]
        wt = self._word_times(words, times)
        for term, m in self._matchers.items():
            hits = m.find(toks)
            total = self._final_counts[term] + len(hits)
            key = (term, total)
            if hits and key not in self._provisional_fired:
                n = self._partial_seen.get(key, 0) + 1
                self._partial_seen[key] = n
                if n >= self.stable_updates:
                    h = hits[-1]
                    self._provisional_fired.add(key)
                    self.events.append(MentionEvent(term, "PROVISIONAL", total,
                                                    wt[h.token_index], audio_pos, h.surface))

    def on_final(self, words: list[str], times: list[float] | None, audio_pos: float):
        toks = [w.lower() for w in words]
        wt = self._word_times(words, times)
        for term, m in self._matchers.items():
            for h in m.find(toks):
                self._final_counts[term] += 1
                self.events.append(MentionEvent(term, "CONFIRMED", self._final_counts[term],
                                                wt[h.token_index], audio_pos, h.surface))
        # partial bookkeeping restarts with each new segment
        self._partial_seen.clear()

    def counts(self) -> dict[str, int]:
        return dict(self._final_counts)


# ---------------------------------------------------------------------------------------------
# 4. In-play hazard model (the "LLM predicts the next words" box)
# ---------------------------------------------------------------------------------------------

def _poisson_sf(lam: float, k: int) -> float:
    """P(N >= k) for N ~ Poisson(lam)."""
    if k <= 0:
        return 1.0
    if lam <= 0:
        return 0.0
    term, cdf = math.exp(-lam), 0.0
    for i in range(k):
        if i:
            term *= lam / i
        cdf += term
    return max(0.0, min(1.0, 1.0 - cdf))


def implied_rate(p_prior: float, k: int = 1) -> float:
    """The whole-event Poisson mean that makes P(N >= k) equal the pre-event price."""
    p = min(max(p_prior, 1e-6), 1 - 1e-6)
    if k == 1:
        return -math.log(1 - p)
    lo, hi = 0.0, 200.0
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _poisson_sf(mid, k) < p else (lo, mid)
    return (lo + hi) / 2


@dataclass
class HazardModel:
    """Constant-hazard baseline, with an optional context multiplier.

    p_remaining = P(at least k - said more occurrences in the rest of the event).
    `context` is where an LLM would plug in: given the transcript so far it returns a
    multiplier on the remaining rate (topic already being discussed -> >1; segment for that
    topic already passed -> <1). The default is 1.0 — no LLM is called in this prototype.
    """
    p_prior: float
    threshold: int = 1
    context: Callable[[str], float] | None = None

    def __post_init__(self):
        self.lam = implied_rate(self.p_prior, self.threshold)

    def p_remaining(self, elapsed_frac: float, said: int, transcript_so_far: str = "") -> float:
        if said >= self.threshold:
            return 1.0
        rem = max(0.0, 1.0 - elapsed_frac)
        mult = self.context(transcript_so_far) if self.context else 1.0
        return _poisson_sf(self.lam * rem * mult, self.threshold - said)


def keyword_context(related: Iterable[str], boost: float = 2.0) -> Callable[[str], float]:
    """A deliberately dumb stand-in for the LLM: if a related word has appeared in the last
    ~60 words, the remaining rate is multiplied by `boost`. Exists to show the interface."""
    rel = {r.lower() for r in related}

    def f(transcript: str) -> float:
        recent = tokenize(transcript)[-60:]
        return boost if rel & set(recent) else 1.0
    return f
