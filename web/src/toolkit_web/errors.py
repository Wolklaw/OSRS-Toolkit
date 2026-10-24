"""What a failed request gets back: a page for a browser, a JSON body for everything else."""

from __future__ import annotations

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from toolkit_web.deps import Deps
from toolkit_web.presentation import ERROR_PAGES, _useful_detail
from toolkit_web.sessions import COOKIE_NAME


def install(app: FastAPI, deps: Deps) -> None:
    def wants_html(request: Request) -> bool:
        """Whether a browser is following a link, rather than a client reading JSON.

        The desktop window, the plugin and this app's own fetch() calls all read ``detail``
        off a JSON body, so only navigations get a page. Without this split a mistyped URL
        handed the visitor a raw JSON object for the browser to pretty-print, which is how
        a 404 came to look like a working screen.
        """
        if request.url.path.startswith(("/api/", "/internal/", "/auth/", "/static/")):
            return False
        return "text/html" in request.headers.get("accept", "")

    def error_page(request: Request, status_code: int, detail: object) -> Response:
        title, sub = ERROR_PAGES.get(status_code, ("Something went wrong", "That request failed."))
        account = None
        try:
            account = deps.optional_account(request.cookies.get(COOKIE_NAME), None)
        except Exception:  # noqa: BLE001 - an error page must never raise one of its own
            account = None
        return deps.templates.TemplateResponse(
            request,
            "error.html",
            {
                "account": account,
                # Deliberately not the character switcher's real contents: that costs a
                # round trip to the sync service, and an error page is the last place to
                # spend one. An empty list simply hides the switcher.
                "synced_characters": [],
                "active_character": None,
                "status_code": status_code,
                "error_title": title,
                "error_sub": sub,
                # The route's own message when it has one, otherwise the address that
                # failed -- repeating the subtitle underneath itself said nothing twice.
                "error_detail": _useful_detail(status_code, detail),
                "error_path": request.url.path,
            },
            status_code=status_code,
        )

    @app.exception_handler(StarletteHTTPException)
    def handle_http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if not wants_html(request):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        if exc.status_code == 401:
            # A signed-out visitor following a link to a page that needs an account wants
            # the sign-in button, not an explanation of what 401 means.
            return RedirectResponse("/", status_code=303)
        return error_page(request, exc.status_code, exc.detail)

    @app.exception_handler(RequestValidationError)
    def handle_validation_error(request: Request, exc: RequestValidationError) -> Response:
        if not wants_html(request):
            return JSONResponse({"detail": exc.errors()}, status_code=422)
        return error_page(request, 422, None)
