# Fact-Check Agent

**A verification layer for documents.** Upload a PDF; the system extracts verifiable claims, tests each one against live web evidence, and returns a verdict with the corrected fact, a source link, a confidence level and the date checked.

**Live application:** https://factcheck-agentcontrol.streamlit.app/
The application runs on shared hosting and may sleep when idle. If a wake-up prompt appears, select it and allow about 30 seconds.

---

## 1. Problem

Marketing briefs, reports and AI-generated drafts routinely contain statistics, dates and figures that are outdated, misquoted or invented. Manual verification is slow, and language models checking claims from memory repeat the same stale data. Reviewers need a fast, auditable first pass that shows *what* is wrong, *what is correct*, and *where that came from*.

## 2. Solution

| Capability | Behaviour |
|---|---|
| Claim extraction | Identifies statistics, dates, financial figures, technical specifications and concrete facts. Opinions, marketing language and predictions are excluded. |
| Live verification | Each claim is searched on the web at run time. Verdicts are grounded only in the retrieved results, not in model memory. |
| Evidence-backed output | Every verdict includes the corrected fact, one-line rationale, source URL, confidence and check date. |
| Safe degradation | If search or the model is unavailable, the claim is reported as *Could not check* rather than guessed. |
| Export | The full report downloads as CSV. |

### Verdict definitions

| Verdict | Definition |
|---|---|
| **Verified** | A reliable source states the same fact and the key value matches. |
| **Inaccurate** | The subject is correct but the value is outdated or wrong (for example, an old statistic or an incorrect year). The corrected value is shown. |
| **False** | Sources contradict the claim (wrong entity or location), or no supporting evidence exists. Unsupported claims are capped at Low confidence. |
| **Could not check** | Search or model call failed or timed out. Manual review is required. |

## 3. Architecture

```
PDF upload
  -> text extraction (pypdf)
  -> claim extraction + search-query generation       [1 model call]
  -> parallel web search per claim (Tavily, cached)
  -> batched judgement against retrieved evidence     [1 model call]
  -> validation layer -> results table + CSV
```

**Design decisions**

- **Evidence-only judging.** The judge may use only supplied search results, which prevents stale model memory from deciding the verdict.
- **Two model calls per document.** Claims are judged in one batch, so cost and latency do not scale with claim count. Search is the only per-claim call, capped at 15 claims.
- **Validation layer.** A source URL must appear in the retrieved results or it is removed and confidence is lowered. A verdict with no evidence cannot carry a fabricated correction.
- **Prompt-injection defence.** Document text is passed as delimited data, the model is instructed to ignore any directives inside it, and lines that resemble instructions to an AI are surfaced as a warning.
- **Model resilience.** An ordered list of Gemini models is attempted with one retry each; rate limits and retired model names fall through to the next model. Search uses a single provider with one retry and a cache.
- **Current-date awareness.** The run date is supplied to the model so time-sensitive claims are judged against today.

## 4. Evaluation

Two answer-keyed test documents are included in `sample/`.

| Document | Claims | Purpose |
|---|---|---|
| `trap_document.pdf` | 9 + 1 opinion + 1 injection line | Compact regression test |
| `demo_document.pdf` | 11 + 1 opinion + 1 injection line | Realistic two-page brief for demonstration |

Results on `trap_document.pdf`, live pipeline (Gemini + Tavily), run on 2026-11-17:

| Run | Errors flagged | True claims verified | Label accuracy | Change made |
|---|---|---|---|---|
| Baseline | 7 of 7 | 2 of 2 | 7 of 9 | Wrong-entity claims (Eiffel Tower in Berlin, Mumbai as capital) were labelled *Inaccurate* instead of *False*; the unsupported statistic carried High confidence. |
| After prompt revision | 7 of 7 | 2 of 2 | 9 of 9 | Added explicit *Inaccurate* vs *False* criteria; unsupported *False* verdicts capped at Low confidence. |

The opinion and the embedded instruction line were not extracted as claims, and the instruction line was reported as a warning. No false positives (true claims marked wrong) and no missed errors were observed.

**Scope of these results:** nine claims is a small sample and demonstrates behaviour, not general accuracy. Re-run `python score.py --doc demo` after any prompt or model change.

## 5. Setup

Requires Python 3.10+.

```bash
git clone https://github.com/Dvipg/factcheck-agent.git
cd factcheck-agent
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env              # Windows: copy .env.example .env
streamlit run app.py
```

### Configuration

| Variable | Required | Purpose | Source |
|---|---|---|---|
| `GEMINI_API_KEY` | Yes | Claim extraction and judgement | https://aistudio.google.com/apikey |
| `TAVILY_API_KEY` | Yes | Live web search | https://app.tavily.com |
| `GEMINI_MODEL` | No | Overrides the primary model if one is retired | Model list in Google AI Studio |

Without both keys the app starts in an offline demo mode using canned answers, intended for interface development only.

### Secrets handling

Credentials are never stored in source. Locally they are read from `.env`; in production from the host's secret store (see `.streamlit/secrets.toml.example`). `.env` and `secrets.toml` are listed in `.gitignore`. If a key is ever exposed, revoke it in the provider dashboard and issue a new one; deleting the file from the repository does not remove it from history.

### Deployment

Deployed on Streamlit Community Cloud: connect the repository, set the main file to `app.py`, select Python 3.12 or 3.13, and add the two keys under *Settings > Secrets*.

## 6. Testing

```bash
pytest                           # unit tests, no network access required
python make_trap_pdf.py          # regenerate sample documents and answer keys
python score.py                  # score the live pipeline on the trap document
python score.py --doc demo       # score the live pipeline on the demonstration document
python score.py --offline        # score the offline mode (no API calls)
```

Unit tests cover PDF validation (empty, non-PDF, scanned), injection detection, claim de-duplication and caps, source-URL validation, no-evidence handling, search-failure handling and graceful degradation when the judge is unavailable.

## 7. Limitations

- Text-based PDFs only. Scanned or image-only files are rejected with a message; OCR is not included.
- English-language documents. Only the first ~30,000 characters are analysed, and up to 15 claims per document are checked.
- Verdicts rest on search-result snippets rather than full pages, and a model can still misjudge. Low confidence indicates manual review is needed.
- Relative dates ("last year") are interpreted against the run date and may be ambiguous.
- The service operates within provider free-tier quotas. A per-session run limit protects shared capacity, and throughput is correspondingly limited.
- Search relies on a single provider. Model calls fall back across Gemini models, not across providers.
- Document text is transmitted to third-party model and search providers; do not upload confidential files.

## 8. Roadmap

| Horizon | Item |
|---|---|
| Near term | OCR for scanned PDFs; highlight claims in the source document; persistent cross-session cache |
| Mid term | Multi-source corroboration for confidence scoring; secondary search provider; labelled benchmark set tracked per release |
| Longer term | Batch queue for high-volume use; API endpoint; reviewer workflow with accept/reject and audit trail |

## 9. Project structure

```
app.py                 Streamlit interface
pipeline.py            extraction, search, judgement, validation
prompts.py             model instructions
config.py              secret loading and limits
demo.py                offline mode for development
make_trap_pdf.py       builds sample documents and answer keys
score.py               scores a run against an answer key
test/                  unit tests
sample/                test documents and answer keys
Streamlit/             secrets template
```
