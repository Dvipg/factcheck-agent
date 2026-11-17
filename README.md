# Fact-Check Agent: a "Truth Layer" for PDFs

**Live app:** https://factcheck-agentcontrol.streamlit.app/ (free hosting: if it is asleep, click the wake-up button and wait about 30 seconds)

Upload a PDF. The app extracts checkable claims (statistics, dates, financial and technical figures, concrete facts), searches the **live web** for each one, and labels it:

| Verdict | Meaning |
|---|---|
| 🟢 **Verified** | A reliable source states the same fact and the key value matches |
| 🟠 **Inaccurate** | The claim is outdated or off; the app shows the correct value from the sources |
| 🔴 **False** | Sources contradict the claim, or no evidence was found (the reason says which) |
| ⚪ **Could not check** | Search or AI was rate limited or timed out; shown instead of guessing |

Every row shows the correct fact, a one-line reason, a clickable source, a confidence level (High/Medium/Low) and the date it was checked. A CSV report can be downloaded.

## How it works

```
PDF -> text (pypdf) -> AI extracts claims + search queries (1 call)
    -> live web search per claim (Tavily, parallel, cached)
    -> AI judge reads ONLY the search results, all claims in one call (1 call)
    -> safety checks -> report table
```

Design choices:
- **Live search, not AI memory.** Outdated statistics come from model memory, so the judge is only allowed to use the fresh search results.
- **Few AI calls.** About 2 LLM calls per document regardless of claim count, to stay inside free tiers. Claims are capped at 15.
- **No invented facts or links.** A source URL must appear in the search results or it is dropped and confidence is lowered. With no search results the claim is "False: no evidence found" at Low confidence; with a search *failure* it is "Could not check", never a false "False".
- **Prompt-injection defence.** Text inside the PDF is passed as data inside delimiters, the model is told to ignore instructions in it, and lines that look like instructions to an AI are shown as a warning.
- **Fallbacks.** Gemini models are tried in order, then Groq if a key is set. Each call retries once. Failures produce a friendly message, never a crash.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows. On Mac/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env            # Mac/Linux: cp .env.example .env
# open .env and paste your keys (this file is git-ignored)
streamlit run app.py
```

No keys yet? Leave the sidebar **Demo mode** on to try the interface with canned answers (only meaningful for `sample/trap_document.pdf`).

## API keys (free tiers, no billing needed)

| Key | Get it | Used for |
|---|---|---|
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey | extract claims and judge |
| `TAVILY_API_KEY` | https://app.tavily.com | live web search |
| `GROQ_API_KEY` (optional) | https://console.groq.com/keys | backup LLM |

Keys are **never in the code**. Locally they live in `.env`; on Streamlit Cloud they go in *App > Settings > Secrets* (see `.streamlit/secrets.toml.example`). Both locations are in `.gitignore`.

## Deploy (Streamlit Community Cloud)

1. Push this repo to GitHub (public).
2. https://share.streamlit.io > *Create app* > pick the repo, branch `main`, main file `app.py`.
3. *Advanced settings > Secrets*: paste the contents of `.streamlit/secrets.toml.example` with real values.
4. Open the live URL in a private window and upload `sample/trap_document.pdf`.

## Testing

```bash
pytest                    # unit tests, no API calls
python make_trap_pdf.py   # regenerates sample/trap_document.pdf + expected.json
python score.py           # scores the REAL pipeline on the trap PDF (uses ~3 LLM calls)
python score.py --demo    # scores offline demo mode (no quota)
```

`sample/trap_document.pdf` is our own trap document: wrong dates, an outdated population figure, a wrong location, an invented statistic, two true facts, an opinion that must be skipped, and a hidden "ignore your rules" line.

**Results log** (update after each real run):

| Date | Mode | Score | Notes |
|---|---|---|---|
| 2026-11-17 | Live app, real keys (Gemini + Tavily) | 9/9 flagged correctly | All 6 planted errors flagged with the correct fact and a source; both true claims Verified; invented statistic marked False; the opinion and the hidden "ignore your rules" line were not extracted. The injection line was shown as a warning. |

**Observations from that run, and what was changed:**
- Two contradicted claims (Eiffel Tower in Berlin, Mumbai as capital) were labelled *Inaccurate* instead of *False*. The judge prompt now separates "right subject, wrong or outdated value" (Inaccurate) from "wrong entity or unsupported" (False).
- The invented statistic was correctly *False* but with High confidence. "No evidence found" is absence of proof, so a False with no source is now always capped at Low confidence ("check manually").
- No false positives (no true claim was marked wrong) and no false negatives (no planted error was missed) were observed in this single 9-claim run. This is a small test, not a measure of general accuracy.

## Assumptions and limitations

- Text PDFs only. Scanned/image PDFs get a friendly warning (no OCR).
- English documents. Long documents: only the first ~30,000 characters are analysed, and at most 15 claims are checked, most specific first.
- Verdicts rest on web search snippets, not full pages, and the AI can still misjudge. Low confidence means: check manually.
- Relative dates ("last year") are judged against today's date and the document's own context, and may be ambiguous.
- Free-tier AI providers may use submitted text to improve their products: do not upload confidential documents.
- Free quotas are small; a per-session run limit protects them. If they run out, see the demo video.

## What I would do with more time

Caching across sessions, a confidence score from multiple sources, highlighting claims in the PDF, OCR for scanned files, a background job queue for 1,000 documents a day, and a larger labelled test set to measure accuracy after every prompt change.

## Project layout

```
app.py            Streamlit screen
pipeline.py       extract -> search -> judge, fallbacks, safety
prompts.py        all AI instructions
config.py         safe secret loading and limits
demo.py           offline demo mode
make_trap_pdf.py  builds the test document
score.py          scores a run against expected answers
tests/            unit tests
sample/           trap_document.pdf and expected.json
```
