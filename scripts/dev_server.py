"""Local dev server that mirrors Vercel's routing.

Vercel serves `public/` as static assets and routes `/api/*` to the Python
function. This reproduces that in one process so the frontend can be exercised
end-to-end locally (same-origin relative fetches, no CORS shims).

    python scripts/dev_server.py            # http://localhost:8200
    PORT=8010 python scripts/dev_server.py  # the port the older e2e scripts use

Not deployed — scripts/ is in .vercelignore.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.staticfiles import StaticFiles          # noqa: E402
from src.api_app import app                          # noqa: E402

PUBLIC = ROOT / "public"


class SPAStatic(StaticFiles):
    """Serve /foo as /foo.html the way Vercel's clean URLs do."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except Exception:
            if "." not in path:
                return await super().get_response(f"{path}.html", scope)
            raise


app.mount("/", SPAStatic(directory=str(PUBLIC), html=True), name="public")


if __name__ == "__main__":
    import os
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8200)))
