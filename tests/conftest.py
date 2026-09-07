"""Test-suite safety net: never let pytest touch the production database.

`src/db/store.py` calls `load_dotenv()` at import time, so a developer with a
populated `.env` (SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY) would silently run
the whole suite against the live Supabase project. The suite creates users,
designs, simulations and CAD imports — which is how the deployed site's "live
counters" get inflated, and how stray `e2e_user_*` rows end up in production.

This fixture clears the Supabase credentials *before* anything imports the
store, so tests always fall back to a throwaway SQLite file.

Opt in deliberately when you really do want an integration run:

    SHELTER_TEST_ALLOW_SUPABASE=1 python -m pytest tests/ -q
"""
from __future__ import annotations

import os

_ALLOW = os.environ.get("SHELTER_TEST_ALLOW_SUPABASE") == "1"

if not _ALLOW:
    # unset before src.db.store (and therefore src.api_app) is first imported
    for _var in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
                 "SUPABASE_ANON_KEY", "DATABASE_URL"):
        os.environ.pop(_var, None)
    # make load_dotenv() a no-op for the same reason
    os.environ["DOTENV_DISABLED"] = "1"


def pytest_report_header(config):
    if _ALLOW:
        return ("db: SUPABASE (SHELTER_TEST_ALLOW_SUPABASE=1) — "
                "WARNING: this run writes to the live project")
    return "db: sqlite (production Supabase credentials suppressed)"
