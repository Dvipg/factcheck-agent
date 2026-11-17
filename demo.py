"""Offline demo mode: canned AI + search so the UI can be tested with ZERO API quota.

Only knows the sample trap document. Real runs use pipeline.llm_json / tavily_search.
"""
import json
import re

import prompts

KNOWN = [
    # keyword, verdict, correct fact, source
    ("chatgpt", "Inaccurate", "ChatGPT was released publicly in November 2022.", "https://openai.com/index/chatgpt/"),
    ("python", "Inaccurate", "Python was first released in 1991.", "https://www.python.org/doc/essays/blurb/"),
    ("population", "Inaccurate", "India's population is over 1.4 billion.", "https://www.un.org/development/desa/pd/"),
    ("eiffel", "False", "The Eiffel Tower is in Paris, France.", "https://www.toureiffel.paris/en"),
    ("boils", "Verified", "Water boils at 100 C at sea level (standard pressure).", "https://www.britannica.com/science/boiling-point"),
    ("google", "Verified", "Google was founded in 1998.", "https://about.google/our-story/"),
    ("iphone", "Inaccurate", "The first iPhone launched in 2007.", "https://www.apple.com/newsroom/"),
    ("capital", "False", "India's capital is New Delhi.", "https://knowindia.india.gov.in/"),
    ("xyz-lens", "False", "", ""),
]


def demo_llm(system: str, user: str) -> dict:
    if system == prompts.EXTRACT_SYSTEM:
        body = user.split("<document>")[-1].split("</document>")[0]
        sents = re.split(r"(?<=[.!?])\s+|\n+", body)
        claims = []
        for s in sents:
            s = s.strip()
            if re.search(r"\d|located|capital", s) and not s.startswith("[Page") and len(s) > 20:
                claims.append({"claim": s, "category": "fact", "page": 1, "search_query": s})
        return {"claims": claims[:15]}
    payload = json.loads(user.split("<claims_and_evidence>")[-1].split("</claims_and_evidence>")[0])
    verdicts = []
    for c in payload:
        low = c["claim"].lower()
        hit = next((k for k in KNOWN if k[0] in low), None)
        url = c["search_results"][0]["url"] if c["search_results"] else ""
        if hit:
            verdicts.append({"id": c["id"], "verdict": hit[1], "correct_fact": hit[2],
                             "reason": "Demo mode: canned verdict.", "source_url": url,
                             "confidence": "High" if hit[3] else "Low"})
        else:
            verdicts.append({"id": c["id"], "verdict": "False", "correct_fact": "",
                             "reason": "Demo mode: no evidence found.", "source_url": "", "confidence": "Low"})
    return {"verdicts": verdicts}


def demo_search(query: str) -> list[dict]:
    low = query.lower()
    hit = next((k for k in KNOWN if k[0] in low), None)
    if not hit or not hit[3]:
        return []
    return [{"title": "Demo source", "url": hit[3], "snippet": hit[2], "date": ""}]
