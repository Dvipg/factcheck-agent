"""Streamlit front end for the Fact-Check Agent ("Truth Layer")."""
import pandas as pd
import streamlit as st

import config
import demo
import pipeline

st.set_page_config(page_title="Fact-Check Agent", page_icon="✅", layout="wide")

BADGE = {"Verified": "🟢 Verified", "Inaccurate": "🟠 Inaccurate", "False": "🔴 False", "Could not check": "⚪ Could not check"}
COLOR = {"Verified": "#d1fae5", "Inaccurate": "#fef3c7", "False": "#fee2e2", "Could not check": "#e5e7eb"}

st.title("✅ Fact-Check Agent: a Truth Layer for your documents")
st.write(
    "Upload a PDF. The app finds statistics, dates and figures, checks each one against the live web, "
    "and labels it **Verified**, **Inaccurate** (outdated or wrong value) or **False** (contradicted or no evidence), "
    "with the correct fact, a source link and a confidence level."
)

with st.sidebar:
    st.header("Settings")
    has_keys = bool(config.get_secret("GEMINI_API_KEY") or config.get_secret("GROQ_API_KEY")) and bool(config.get_secret("TAVILY_API_KEY"))
    demo_mode = st.toggle("Demo mode (no API calls)", value=not has_keys,
                          help="Uses canned answers so the interface can be tried without any API quota. Only meaningful for the sample trap PDF.")
    st.caption(f"Checks at most {config.MAX_CLAIMS} claims per document.")
    st.caption("Verdicts rely on web search snippets. Low confidence means: please check manually.")
    st.caption("Uploaded text is sent to the AI and search providers. Do not upload confidential files.")

if "runs" not in st.session_state:
    st.session_state.runs = 0

uploaded = st.file_uploader("Upload a PDF", type=["pdf"])

if uploaded is not None:
    if st.button("Run fact-check", type="primary"):
        if st.session_state.runs >= config.MAX_RUNS_PER_SESSION:
            st.warning("Session limit reached to protect the free API quota. Please reload the page.")
        else:
            st.session_state.runs += 1
            bar = st.progress(0.0, text="Starting...")
            try:
                out = pipeline.run(
                    uploaded.getvalue(),
                    llm=demo.demo_llm if demo_mode else pipeline.llm_json,
                    search_fn=demo.demo_search if demo_mode else pipeline.tavily_search,
                    progress=lambda p, m: bar.progress(min(p, 1.0), text=m),
                )
                st.session_state.out = out
            except pipeline.PipelineError as e:
                st.session_state.pop("out", None)
                st.error(str(e))
            except Exception:
                st.session_state.pop("out", None)
                st.error("Something unexpected went wrong. Please try again or use a different PDF.")
            finally:
                bar.empty()

out = st.session_state.get("out")
if out:
    for n in out["notes"]:
        st.info(n)
    res = out["results"]
    if res:
        counts = {k: sum(r["verdict"] == k for r in res) for k in BADGE}
        st.subheader(
            f"{len(res)} claims checked: {counts['Verified']} verified, {counts['Inaccurate']} inaccurate, "
            f"{counts['False']} false" + (f", {counts['Could not check']} could not be checked" if counts["Could not check"] else "")
        )
        for r in res:
            with st.container(border=True):
                c1, c2 = st.columns([1, 4])
                c1.markdown(
                    f"<div style='background:{COLOR[r['verdict']]};padding:8px;border-radius:8px;color:#111;text-align:center'>"
                    f"<b>{BADGE[r['verdict']]}</b><br><small>Confidence: {r['confidence']}</small></div>",
                    unsafe_allow_html=True,
                )
                c2.markdown(f"**Claim (p.{r['page']}):** {r['claim']}")
                if r["correct_fact"]:
                    c2.markdown(f"**Correct fact:** {r['correct_fact']}")
                if r["reason"]:
                    c2.caption(r["reason"])
                if r["source_url"]:
                    c2.markdown(f"[Source]({r['source_url']}) · checked {r['checked_on']}")
                else:
                    c2.caption(f"No source link · checked {r['checked_on']}")
        df = pd.DataFrame(res)[["id", "page", "claim", "verdict", "correct_fact", "reason", "source_url", "confidence", "checked_on"]]
        st.download_button("Download report (CSV)", df.to_csv(index=False).encode("utf-8"),
                           file_name="fact_check_report.csv", mime="text/csv")
else:
    st.caption("Tip: use sample/trap_document.pdf from the repository to try it.")
