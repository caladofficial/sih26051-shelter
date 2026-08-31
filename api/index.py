"""Vercel Python entrypoint — thin shim (must stay minimal!).

The whole FastAPI application lives in src/api_app.py. The Vercel Python
builder statically analyses this file to decide how to route: when it can
confirm a top-level ASGI ``app`` it emits single-function ("app" build)
routing for the whole site; when it cannot, it falls back to per-file
"api-dir" routing which — for this project — results in a 404 on every
/api/* path. Keep this file a one-line re-export.

Run locally:    uvicorn api.index:app --reload --port 8000
"""
from src.api_app import app  # noqa: F401  (re-export for uvicorn/tests)
