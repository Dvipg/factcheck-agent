"""Safe secret loading.

Order of lookup for every key:
1. Streamlit secrets (set in the Streamlit Cloud "Secrets" box, never in git)
2. Environment variables / a local .env file (ignored by git)

No key is ever written in code.
"""
import os

try:
    from dotenv import load_dotenv

    load_dotenv()  # reads a local .env file if present
except Exception:  # pragma: no cover - dotenv is optional
    pass


def get_secret(name: str, default: str = "") -> str:
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        # st.secrets raises if no secrets file exists; fall back to env vars
        pass
    return os.getenv(name, default)


# Free-tier model ids change over time. Override with GEMINI_MODEL if one is retired.
DEFAULT_GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
GROQ_MODEL_DEFAULT = "llama-3.3-70b-versatile"

MAX_CLAIMS = 15          # cap to protect free quotas
MAX_TEXT_CHARS = 30000   # characters of PDF text sent to the model
MAX_RESULTS_PER_CLAIM = 4
SNIPPET_CHARS = 450
MAX_RUNS_PER_SESSION = 8  # stops one visitor from burning the shared free quota
