"""Scores the REAL pipeline against sample/expected.json. Uses a little API quota (about 3 LLM calls).

Run:  python score.py            (keys must be in .env)
Add --demo to score the offline demo mode instead (no quota).
"""
import json
import sys

import pipeline
import demo


def main():
    pdf = open("sample/trap_document.pdf", "rb").read()
    expected = json.load(open("sample/expected.json"))
    kw = {}
    if "--demo" in sys.argv:
        kw = {"llm": demo.demo_llm, "search_fn": demo.demo_search}
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
        if e["expected"] in ("SKIP", "INJECTION"):
            r = find(e["claim"])
            status = "PASS (not extracted)" if r is None else f"CHECK (extracted as {r['verdict']})"
            print(f"- [{e['expected']}] {e['claim'][:60]} -> {status}")
            continue
        total += 1
        r = find(e["claim"])
        if r is None:
            print(f"- MISS (not extracted): {e['claim'][:60]}")
            continue
        v = r["verdict"]
        good = (v == "Verified") if e["expected"] == "Verified" else (v in ("Inaccurate", "False") if e["expected"] == "NOT_VERIFIED" else v == "False")
        ok += good
        print(f"- {'PASS' if good else 'FAIL'} [{v}/{r['confidence']}] {e['claim'][:55]} | fact: {r['correct_fact'][:60]} | src: {r['source_url'][:50]}")
    print(f"\nScore: {ok}/{total}")


if __name__ == "__main__":
    main()
