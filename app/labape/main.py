"""The FastAPI application: API routers plus the built web UI (single-page app)."""
from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from .api import admin, auth, environments, hosts, jobs, networks
from .config import get_settings

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def create_app() -> FastAPI:
    s = get_settings()
    if not s.secret_key:
        raise RuntimeError("LABAPE_SECRET_KEY (or LABAPE_SECRET_KEY_FILE) must be set")
    public = urlsplit(s.public_url)
    origin = f"{public.scheme}://{public.netloc}"

    app = FastAPI(title="LABaPe", docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        # SameSite=Lax already keeps the session cookie off cross-site
        # POSTs; this refuses any cross-origin write outright as well.
        # The loopback break-glass listener has its own origin, so it's allowed.
        if request.method not in SAFE_METHODS:
            req_origin = request.headers.get("origin")
            local = request.headers.get("x-labape-local") == "1"
            if req_origin and req_origin != origin and not local:
                return JSONResponse({"detail": "Cross-origin request refused"}, status_code=403)
        return await call_next(request)

    app.add_middleware(SessionMiddleware, secret_key=s.secret_key, session_cookie="labape_session",
                       max_age=s.session_hours * 3600, same_site="lax", https_only=public.scheme == "https")

    for r in (auth.router, hosts.router, networks.router, environments.router, jobs.router, admin.router):
        app.include_router(r)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    web = s.web_dir
    index = web / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        candidate = (web / path).resolve()
        if path and candidate.is_file() and web.resolve() in candidate.parents:
            return FileResponse(candidate)
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse({"detail": "Web UI not built (LABAPE_WEB_DIR)"}, status_code=404)

    return app


def app_factory() -> FastAPI:  # uvicorn --factory entry point
    return create_app()

