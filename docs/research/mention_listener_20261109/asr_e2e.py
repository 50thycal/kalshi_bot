"""End-to-end replay: real call audio -> streaming ASR -> StreamDetector -> scored vs reference.

Research harness for docs/MENTION_LIVE_LISTENER_FEASIBILITY_20261109.md. NOT a collector and not
part of the bot: it runs offline on a recorded file, feeding audio to the recognizer in 100 ms
chunks exactly as a live stream would arrive, and measures what the live loop would have seen.

Inputs (not committed; fetched by the reader):
  * audio + reference: Rev "earnings21" (CC BY-SA 4.0), github.com/revdotcom/speech-datasets
  * model: sherpa-onnx streaming zipformer (Apache-2.0), GitHub release asset
    sherpa-onnx-streaming-zipformer-en-2023-06-26
Optional deps: `pip install sherpa-onnx numpy`, plus ffmpeg on PATH.

Measures, per pre-registered target word (targets_prereg.json):
  * reference count (Matcher on the human transcript) vs ASR count (Matcher on ASR finals)
  * first-detection algorithmic latency = audio processed when the detector fired minus the
    recognizer's own time estimate for the word. Excludes network and broadcast delay, which
    are the dominant terms in a live setting (see the feasibility doc), and depends on the
    recognizer's timestamps — the reference transcript for this file carries no word times.
  * real-time factor (compute seconds per audio second)

Usage:
  python asr_e2e.py --audio 4386541.mp3 --ref 4386541.nlp --model-dir <zipformer dir> \
      --targets targets_prereg.json --out result.json [--hotwords] [--hotword-score 2.0]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import numpy as np
import sherpa_onnx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mention_listener as ml  # noqa: E402

SR = 16000
CHUNK_S = 0.1


def load_audio(path: str) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-ac", "1",
                          "-ar", str(SR), "-"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def reference_text(nlp_path: str) -> str:
    with open(nlp_path, encoding="utf-8") as f:
        next(f)
        return " ".join(line.split("|", 1)[0] for line in f if line.strip())


def words_from_tokens(tokens: list[str], times: list[float], offset: float):
    """BPE pieces -> (words, word start times). A piece starting with '▁' or ' ' starts a new
    word (sherpa-onnx 2.x returns ' WE', older builds return '▁WE' — handle both)."""
    words, wt = [], []
    for tok, t in zip(tokens, times):
        if tok[:1] in ("▁", " ") or not words:
            words.append(tok.lstrip("▁ "))
            wt.append(offset + t)
        else:
            words[-1] += tok
    keep = [i for i, w in enumerate(words) if w]
    return [words[i] for i in keep], [wt[i] for i in keep]


def build_recognizer(model_dir: str, hotwords_file: str = "", score: float = 1.5):
    p = lambda n: os.path.join(model_dir, n)  # noqa: E731
    kw = dict(
        tokens=p("tokens.txt"),
        encoder=p("encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx"),
        decoder=p("decoder-epoch-99-avg-1-chunk-16-left-128.onnx"),
        joiner=p("joiner-epoch-99-avg-1-chunk-16-left-128.int8.onnx"),
        num_threads=2, sample_rate=SR, feature_dim=80,
        enable_endpoint_detection=True, rule1_min_trailing_silence=2.4,
        rule2_min_trailing_silence=0.8, rule3_min_utterance_length=20.0,
        decoding_method="greedy_search",
    )
    if hotwords_file:
        kw.update(decoding_method="modified_beam_search", max_active_paths=4,
                  hotwords_file=hotwords_file, hotwords_score=score,
                  modeling_unit="bpe", bpe_vocab=p("bpe.vocab") if os.path.exists(
                      p("bpe.vocab")) else "")
    return sherpa_onnx.OnlineRecognizer.from_transducer(**kw)


def run(args) -> dict:
    targets = json.load(open(args.targets))["terms"]
    specs = [ml.parse_rules(t, "If the company says it, then Yes.",
                            "Video of the earnings call.\nThe exact phrase/word, or a plural or "
                            "possessive form of the phrase/word.\nGrammatical/tense inflections.")
             for t in targets]
    det = ml.StreamDetector(specs, stable_updates=2)
    hot = ""
    if args.hotwords:
        hot = os.path.join(os.path.dirname(os.path.abspath(args.out)), "hotwords.txt")
        with open(hot, "w") as f:
            for t in targets:
                for alt in ml.parse_term(t)[0]:
                    f.write(" ".join(alt).upper() + "\n")
    rec = build_recognizer(args.model_dir, hot, args.hotword_score)
    audio = load_audio(args.audio)
    if args.max_seconds:
        audio = audio[: int(args.max_seconds * SR)]
    stream = rec.create_stream()
    step = int(CHUNK_S * SR)
    seg_offset = 0.0
    finals: list[str] = []
    t0 = time.perf_counter()
    last_partial = None
    for i in range(0, len(audio), step):
        stream.accept_waveform(SR, audio[i:i + step])
        while rec.is_ready(stream):
            rec.decode_stream(stream)
        pos = min(len(audio), i + step) / SR
        r = rec.get_result_all(stream)
        words, wt = words_from_tokens(list(r.tokens), list(r.timestamps), seg_offset)
        if words and words != last_partial:
            det.on_partial(words, wt, pos)
            last_partial = words
        if rec.is_endpoint(stream):
            if words:
                det.on_final(words, wt, pos)
                finals.append(" ".join(words))
            rec.reset(stream)
            seg_offset = pos
            last_partial = None
    stream.input_finished()
    while rec.is_ready(stream):
        rec.decode_stream(stream)
    r = rec.get_result_all(stream)
    words, wt = words_from_tokens(list(r.tokens), list(r.timestamps), seg_offset)
    if words:
        det.on_final(words, wt, len(audio) / SR)
        finals.append(" ".join(words))
    compute = time.perf_counter() - t0

    ref = reference_text(args.ref)
    asr_text = " ".join(finals)
    rows = []
    for s in specs:
        m = ml.Matcher(s)
        ref_n, asr_n = m.count(ref), m.count(asr_text)
        fuzzy_n = len(m.find_fuzzy(ml.tokenize(asr_text)))
        evs = [e for e in det.events if e.term == s.term]
        prov = next((e for e in evs if e.kind == "PROVISIONAL"), None)
        conf = next((e for e in evs if e.kind == "CONFIRMED"), None)
        rows.append({
            "term": s.term, "ref_count": ref_n, "asr_count": asr_n, "fuzzy_count": fuzzy_n,
            "fuzzy_surfaces": [h.surface for h in m.find_fuzzy(ml.tokenize(asr_text))][:5],
            "ref_yes": ref_n >= s.threshold, "asr_yes": asr_n >= s.threshold,
            "first_provisional_latency_s": round(prov.latency, 2) if prov else None,
            "first_confirmed_latency_s": round(conf.latency, 2) if conf else None,
            "first_word_audio_t": round((prov or conf).audio_t, 1) if (prov or conf) else None,
        })
    tp = sum(r["ref_yes"] and r["asr_yes"] for r in rows)
    fp = sum((not r["ref_yes"]) and r["asr_yes"] for r in rows)
    fn = sum(r["ref_yes"] and not r["asr_yes"] for r in rows)
    lat_p = sorted(r["first_provisional_latency_s"] for r in rows
                   if r["first_provisional_latency_s"] is not None)
    lat_c = sorted(r["first_confirmed_latency_s"] for r in rows
                   if r["first_confirmed_latency_s"] is not None)
    med = lambda xs: xs[len(xs) // 2] if xs else None  # noqa: E731
    ref_tok = ml.tokenize(ref)
    asr_tok = ml.tokenize(asr_text)
    return {
        "file": os.path.basename(args.audio), "audio_s": round(len(audio) / SR, 1),
        "compute_s": round(compute, 1), "real_time_factor": round(compute / (len(audio) / SR), 3),
        "decoding": "modified_beam_search+hotwords" if args.hotwords else "greedy_search",
        "hotword_score": args.hotword_score if args.hotwords else None,
        "ref_words": len(ref_tok), "asr_words": len(asr_tok),
        "market_level": {"yes_true_pos": tp, "false_yes": fp, "missed_yes": fn,
                         "terms": len(rows), "ref_yes_terms": tp + fn},
        "median_provisional_latency_s": med(lat_p), "median_confirmed_latency_s": med(lat_c),
        "events_total": len(det.events),
        "events": [vars(e) for e in det.events],
        "asr_text": asr_text,
        "rows": rows,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--targets", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--hotwords", action="store_true")
    ap.add_argument("--hotword-score", type=float, default=2.0)
    ap.add_argument("--max-seconds", type=float, default=0.0)
    args = ap.parse_args(argv)
    res = run(args)
    with open(args.out, "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1))
    for r in res["rows"]:
        print(r)


if __name__ == "__main__":
    main()
