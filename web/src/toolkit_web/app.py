from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from toolkit_web import errors
from toolkit_web.config import Settings
from toolkit_web.deps import build as build_deps
from toolkit_web.routes import account, api, internal, journal, market, pages, plugin_api, quests
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
        return Response(
            content=(STATIC_DIR / "sw.js").read_text(),
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/offline", response_class=HTMLResponse)
    def offline_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "offline.html",
            {"account": None, "synced_characters": [], "active_character": None},
        )

    @app.middleware("http")
    async def no_store_when_signed_in(request: Request, call_next):
        response = await call_next(request)
        signed_in = COOKIE_NAME in request.cookies or request.headers.get("authorization")
        if signed_in:
            response.headers["Cache-Control"] = "private, no-store"
        return response

    for module in (pages, journal, market, account, api, internal, quests, plugin_api):
        app.include_router(module.build(deps))

    app.state.settings = settings
    app.state.accounts = deps.accounts
    return app
