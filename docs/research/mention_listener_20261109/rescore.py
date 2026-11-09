"""Rescore saved ASR text under the CURRENT matcher (no re-decoding). Used to retest matcher
fixes: the recognizer output in each e2e_*.json is unchanged, only the rules side moves."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mention_listener as ml  # noqa: E402
from asr_e2e import reference_text  # noqa: E402

SEC = ("Video of the earnings call.\nThe exact phrase/word, or a plural or possessive form of "
       "the phrase/word.\nGrammatical/tense inflections.")


def rescore(result_json, targets_json, ref_nlp):
    d = json.load(open(result_json))
    terms = json.load(open(targets_json))["terms"]
    ref, asr = reference_text(ref_nlp), d["asr_text"]
    tp = fp = fn = 0
    rows = []
    for t in terms:
        m = ml.Matcher(ml.parse_rules(t, "If the company says it, then Yes.", SEC))
        r, a = m.count(ref), m.count(asr)
        tp += r > 0 and a > 0
        fp += r == 0 and a > 0
        fn += r > 0 and a == 0
        rows.append((t, r, a))
    return {"yes_found": tp, "false_yes": fp, "missed": fn, "rows": rows}


if __name__ == "__main__":
    print(json.dumps(rescore(*sys.argv[1:4])))
