"""Scores the pipeline against an answer key.

  python score.py                      trap document, real APIs (about 3 model calls plus one search per claim)
  python score.py --doc demo           demonstration document, real APIs
  python score.py --offline            same, using the offline canned mode (no API calls; trap document only)
"""
import argparse
import json

import demo
import pipeline

DOCS = {
    "trap": ("sample/trap_document.pdf", "sample/expected.json"),
    "demo": ("sample/demo_document.pdf", "sample/expected_demo.json"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", choices=DOCS, default="trap")
    ap.add_argument("--offline", "--demo", dest="offline", action="store_true")
    args = ap.parse_args()

    pdf_path, key_path = DOCS[args.doc]
    pdf = open(pdf_path, "rb").read()
    expected = json.load(open(key_path))
    kw = {"llm": demo.demo_llm, "search_fn": demo.demo_search} if args.offline else {}
    out = pipeline.run(pdf, **kw)
    print("NOTES:", *out["notes"], sep="\n  ")
    got = {r["claim"].lower(): r for r in out["results"]}

    def find(text):
        key = text.lower()
        for k, v in got.items():
            if key[:25] in k or k[:25] in key:
                return v
        return None

    ok = total = 0
    for e in expected:
        r = find(e["claim"])
        if e["expected"] in ("SKIP", "INJECTION"):
            status = "PASS (not extracted)" if r is None else f"CHECK (extracted as {r['verdict']})"
            print(f"- [{e['expected']}] {e['claim'][:60]} -> {status}")
            continue
        total += 1
        if r is None:
            print(f"- MISS (not extracted): {e['claim'][:60]}")
            continue
        v = r["verdict"]
        if e["expected"] == "Verified":
            good = v == "Verified"
        elif e["expected"] == "NOT_VERIFIED":
            good = v in ("Inaccurate", "False")
        else:
            good = v == e["expected"]
        ok += good
        print(f"- {'PASS' if good else 'FAIL'} [{v}/{r['confidence']}] {e['claim'][:50]} | fact: {r['correct_fact'][:60]} | src: {r['source_url'][:50]}")
    print(f"\nScore: {ok}/{total}")


if __name__ == "__main__":
    main()
