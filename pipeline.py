"""Fact-check pipeline: PDF -> text -> claims -> live search -> AI judge -> report.

Design goals: few LLM calls (1 to extract, 1 batched to judge), graceful
degradation (retry, fallback model, 'Could not check'), and no invented facts.
"""
from __future__ import annotations

import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Callable, Optional

import requests
from pypdf import PdfReader

import config
import prompts

TIMEOUT = 45
VALID_VERDICTS = {"Verified", "Inaccurate", "False"}


class PipelineError(Exception):
    """Friendly, user-showable error."""


class LLMError(PipelineError):
    pass


# --------------------------------------------------------------------------- PDF
def extract_pages(pdf_bytes: bytes) -> list[str]:
    """Return text per page. Raises PipelineError with a friendly message."""
    if not pdf_bytes:
        raise PipelineError("The uploaded file is empty. Please upload a PDF.")
    if not pdf_bytes.lstrip()[:5] == b"%PDF-":
        raise PipelineError("That does not look like a PDF. Please upload a .pdf file.")
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise PipelineError("This PDF is password protected. Please upload an unlocked copy.")
        pages = [(p.extract_text() or "").strip() for p in reader.pages]
    except PipelineError:
        raise
    except Exception:
        raise PipelineError("Could not read this PDF (it may be damaged). Please try another file.")
    if sum(len(p) for p in pages) < 40:
        raise PipelineError(
            "This looks like a scanned or image-only PDF, so no text could be read. "
            "Please upload a PDF with selectable text (OCR is not supported in this version)."
        )
    return pages


def build_document_text(pages: list[str], limit: int = config.MAX_TEXT_CHARS) -> tuple[str, bool]:
    """Join pages with markers. Returns (text, was_truncated)."""
    parts, total, truncated = [], 0, False
    for i, p in enumerate(pages, 1):
        chunk = f"[Page {i}]\n{p}\n"
        if total + len(chunk) > limit:
            parts.append(chunk[: max(0, limit - total)])
            truncated = True
            break
        parts.append(chunk)
        total += len(chunk)
    return "\n".join(parts), truncated


INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above|your) (instructions|rules)",
    r"disregard (all |any )?(previous|prior|above|your)",
    r"mark (every|all|each) (claim|statement)s? (as )?(verified|true|correct)",
    r"system (note|prompt|message)\b",
    r"you are now\b",
    r"(note|message|instruction)s? to (the )?(ai|assistant|model|llm|fact.?check)",
]


def detect_injection(text: str) -> list[str]:
    """Return lines that look like instructions aimed at an AI (shown as a warning)."""
    hits = []
    for line in text.splitlines():
        if any(re.search(p, line, re.I) for p in INJECTION_PATTERNS):
            hits.append(line.strip()[:200])
    return hits


# --------------------------------------------------------------------------- LLM
def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def _gemini(system: str, user: str, model: str, key: str) -> str:
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
        },
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        raise LLMError(f"gemini:{model}:{r.status_code}")
    try:
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, ValueError):
        raise LLMError(f"gemini:{model}:empty")


def _groq(system: str, user: str, model: str, key: str) -> str:
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        raise LLMError(f"groq:{model}:{r.status_code}")
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError):
        raise LLMError(f"groq:{model}:empty")


def llm_json(system: str, user: str) -> dict:
    """Try Gemini models in order, then Groq. Retry once on transient errors."""
    gkey = config.get_secret("GEMINI_API_KEY")
    qkey = config.get_secret("GROQ_API_KEY")
    custom = config.get_secret("GEMINI_MODEL")
    models = ([custom] if custom else []) + [m for m in config.DEFAULT_GEMINI_MODELS if m != custom]

    attempts: list[Callable[[], str]] = []
    if gkey:
        attempts += [lambda m=m: _gemini(system, user, m, gkey) for m in models]
    if qkey:
        gm = config.get_secret("GROQ_MODEL", config.GROQ_MODEL_DEFAULT)
        attempts.append(lambda: _groq(system, user, gm, qkey))
    if not attempts:
        raise LLMError("No LLM API key configured. Add GEMINI_API_KEY (see README).")

    last = None
    for fn in attempts:
        for retry in range(2):
            try:
                return _parse_json(fn())
            except (LLMError, requests.RequestException, json.JSONDecodeError) as e:
                last = e
                msg = str(e)
                # 404 = model retired, 400/401/403 = bad key/request: do not retry, move on
                if any(f":{c}" in msg for c in ("404", "400", "401", "403")):
                    break
                if retry == 0:
                    time.sleep(2)
    raise LLMError(f"All AI providers failed or are rate limited ({last}). Try again in a minute.")


# ------------------------------------------------------------------------ Search
def tavily_search(query: str, n: int = config.MAX_RESULTS_PER_CLAIM) -> list[dict]:
    key = config.get_secret("TAVILY_API_KEY")
    if not key:
        raise PipelineError("No search API key configured. Add TAVILY_API_KEY (see README).")
    r = requests.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {key}"},
        json={"query": query, "max_results": n, "search_depth": "basic", "include_answer": False},
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        raise PipelineError(f"search:{r.status_code}")
    out = []
    for it in r.json().get("results", [])[:n]:
        out.append({
            "title": it.get("title", ""),
            "url": it.get("url", ""),
            "snippet": (it.get("content") or "")[: config.SNIPPET_CHARS],
            "date": it.get("published_date", ""),
        })
    return out


_search_cache: dict[str, list[dict]] = {}


def search_claim(query: str, search_fn: Callable[[str], list[dict]] = tavily_search) -> tuple[list[dict], Optional[str]]:
    """Returns (results, error). Cached. Retries once."""
    if query in _search_cache:
        return _search_cache[query], None
    err = None
    for attempt in range(2):
        try:
            res = search_fn(query)
            _search_cache[query] = res
            return res, None
        except (PipelineError, requests.RequestException) as e:
            err = str(e)
            if attempt == 0:
                time.sleep(1.5)
    return [], err or "search failed"


# ------------------------------------------------------------------------ Claims
@dataclass
class Result:
    id: int
    claim: str
    category: str = "fact"
    page: int = 0
    verdict: str = "Could not check"   # Verified | Inaccurate | False | Could not check
    correct_fact: str = ""
    reason: str = ""
    source_url: str = ""
    confidence: str = "Low"
    checked_on: str = field(default_factory=lambda: date.today().isoformat())


def extract_claims(text: str, llm: Callable[[str, str], dict] = llm_json, max_claims: int = config.MAX_CLAIMS) -> list[dict]:
    data = llm(prompts.EXTRACT_SYSTEM,
               prompts.EXTRACT_USER.format(today=date.today().isoformat(), max_claims=max_claims, text=text))
    claims = data.get("claims", []) if isinstance(data, dict) else []
    clean, seen = [], set()
    for c in claims:
        if not isinstance(c, dict) or not str(c.get("claim", "")).strip():
            continue
        key = c["claim"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            page = int(c.get("page", 0) or 0)
        except (TypeError, ValueError):
            page = 0
        clean.append({
            "id": len(clean) + 1,
            "claim": c["claim"].strip(),
            "category": str(c.get("category", "fact")),
            "page": page,
            "search_query": str(c.get("search_query") or c["claim"])[:200],
        })
        if len(clean) >= max_claims:
            break
    return clean


def judge_claims(claims: list[dict], evidence: dict[int, list[dict]],
                 llm: Callable[[str, str], dict] = llm_json) -> dict[int, dict]:
    payload = []
    for c in claims:
        payload.append({
            "id": c["id"],
            "claim": c["claim"],
            "search_results": [
                {"title": r["title"], "url": r["url"], "snippet": r["snippet"], "date": r.get("date", "")}
                for r in evidence.get(c["id"], [])
            ],
        })
    data = llm(prompts.JUDGE_SYSTEM,
               prompts.JUDGE_USER.format(today=date.today().isoformat(),
                                         payload=json.dumps(payload, ensure_ascii=False, indent=1)))
    out = {}
    for v in (data.get("verdicts", []) if isinstance(data, dict) else []):
        try:
            out[int(v.get("id"))] = v
        except (TypeError, ValueError):
            continue
    return out


def finalize(claim: dict, evidence: list[dict], verdict: Optional[dict], search_error: Optional[str]) -> Result:
    """Turn raw judge output into a safe Result (guards against invented sources)."""
    r = Result(id=claim["id"], claim=claim["claim"], category=claim["category"], page=claim["page"])
    if search_error and not evidence:
        r.reason = "Search failed or was rate limited, so this claim was not checked."
        return r
    if not verdict or verdict.get("verdict") not in VALID_VERDICTS:
        r.reason = "The AI did not return a usable verdict for this claim."
        return r
    r.verdict = verdict["verdict"]
    r.correct_fact = str(verdict.get("correct_fact", "") or "")
    r.reason = str(verdict.get("reason", "") or "")
    conf = str(verdict.get("confidence", "Low")).capitalize()
    r.confidence = conf if conf in ("High", "Medium", "Low") else "Low"
    url = str(verdict.get("source_url", "") or "")
    known = {e["url"] for e in evidence}
    if url and url not in known:           # the model invented a link: drop it
        url = ""
        r.confidence = "Low"
        r.reason = (r.reason + " (source link removed: not in search results)").strip()
    r.source_url = url
    if not evidence:                        # nothing found at all
        r.verdict = "False" if r.verdict != "Verified" else "Could not check"
        r.confidence = "Low"
        r.correct_fact = ""
        r.reason = "No evidence found in web search results."
    if r.verdict in ("Inaccurate", "False") and r.correct_fact and not r.source_url:
        r.confidence = "Low"                # a correction without a source is weak
    if r.verdict == "False" and not r.source_url:
        r.confidence = "Low"                # 'no evidence found' is absence of proof: say check manually
    return r


def run(pdf_bytes: bytes, *, llm: Callable[[str, str], dict] = llm_json,
        search_fn: Callable[[str], list[dict]] = tavily_search,
        progress: Callable[[float, str], None] = lambda p, m: None) -> dict:
    """Full pipeline. Returns {'results': [...], 'notes': [...], 'claims_found': int}."""
    notes = []
    progress(0.05, "Reading PDF...")
    pages = extract_pages(pdf_bytes)
    text, truncated = build_document_text(pages)
    if truncated:
        notes.append(f"Document is long: only the first ~{config.MAX_TEXT_CHARS:,} characters were analysed.")
    inj = detect_injection(text)
    if inj:
        notes.append("Warning: the document contains text that looks like instructions to an AI "
                     "(treated as content, not obeyed): " + " | ".join(f'"{h}"' for h in inj[:3]))

    progress(0.15, "Finding checkable claims...")
    claims = extract_claims(text, llm)
    if not claims:
        return {"results": [], "notes": notes + ["No checkable claims found in this document."], "claims_found": 0}

    evidence: dict[int, list[dict]] = {}
    errors: dict[int, Optional[str]] = {}
    done = 0

    def work(c):
        return c["id"], *search_claim(c["search_query"], search_fn)

    with ThreadPoolExecutor(max_workers=4) as ex:
        for cid, res, err in ex.map(work, claims):
            evidence[cid], errors[cid] = res, err
            done += 1
            progress(0.2 + 0.5 * done / len(claims), f"Searched claim {done} of {len(claims)}...")

    progress(0.75, "Judging claims against the evidence...")
    try:
        verdicts = judge_claims(claims, evidence, llm)
    except PipelineError as e:
        notes.append(f"The AI judge was unavailable ({e}). Claims below were not judged.")
        verdicts = {}

    results = [finalize(c, evidence[c["id"]], verdicts.get(c["id"]), errors[c["id"]]) for c in claims]
    if any(r.verdict == "Could not check" for r in results):
        notes.append("Some claims could not be checked (rate limit, timeout or no usable verdict). "
                     "Try again later or check them manually.")
    progress(1.0, "Done")
    return {"results": [asdict(r) for r in results], "notes": notes, "claims_found": len(claims)}
