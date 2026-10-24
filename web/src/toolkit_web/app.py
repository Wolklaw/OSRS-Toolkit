"""HTTP surface.

Sign-in is Discord OAuth and nothing else — no password, no email address held anywhere. A
signed session cookie carries a Discord user id; everything past that reads or writes that
user's own journal, importing from the sync service first so a page is never more than one
request stale. See ``toolkit_web.importing`` for why that is a request-time step rather than a
background worker.

The routes themselves live in ``toolkit_web.routes``, one module per group, each handed the
``Deps`` this builds. What stays here is the wiring plus the two responses that belong to the
app rather than to any group of routes: the service worker and the page it falls back to.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from toolkit_web import errors
from toolkit_web.config import Settings
from toolkit_web.deps import build as build_deps
from toolkit_web.routes import account, api, internal, journal, market, pages
from toolkit_web.sessions import COOKIE_NAME

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    deps = build_deps(settings)
    templates = deps.templates

    app = FastAPI(
        title="OSRS Toolkit",
        summary="Web front end for the OSRS Toolkit trade journal.",
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    errors.install(app, deps)

    @app.get("/sw.js")
    def service_worker() -> Response:
        # Served from root, not /static, so its default scope covers the whole app.
        return Response(
            content=(STATIC_DIR / "sw.js").read_text(),
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/offline", response_class=HTMLResponse)
    def offline_page(request: Request) -> HTMLResponse:
        """What the service worker shows for a navigation the network couldn't answer.

        Cached once, at install, and never carries account data -- there is no session to
        read when this is what's rendering, and every signed-in page is already marked
        no-store, so nothing about this one page could safely be personalised anyway.
        """
        return templates.TemplateResponse(
            request,
            "offline.html",
            {"account": None, "synced_characters": [], "active_character": None},
        )

    @app.middleware("http")
    async def no_store_when_signed_in(request: Request, call_next):
        """Mark every response carrying account data as uncacheable.

        Every signed-in page here is account-specific, and nothing in the response previously
        said so — docs/deploying.md flags that as the reason a cache rule covering HTML on
        this domain could serve one account's dashboard to another. Cloudflare's default
        cache level does not cache HTML, so this changes nothing today; it is what makes
        turning caching on for the public pages a safe thing to do rather than a sharp edge.
        """
        response = await call_next(request)
        signed_in = COOKIE_NAME in request.cookies or request.headers.get("authorization")
        if signed_in:
            response.headers["Cache-Control"] = "private, no-store"
        return response

    for module in (pages, journal, market, account, api, internal):
        app.include_router(module.build(deps))

    app.state.settings = settings
    app.state.accounts = deps.accounts
    return app
