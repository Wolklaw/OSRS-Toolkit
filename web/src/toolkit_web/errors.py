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
        if request.url.path.startswith(("/api/", "/internal/", "/auth/", "/static/")):
            return False
        return "text/html" in request.headers.get("accept", "")

    def error_page(request: Request, status_code: int, detail: object) -> Response:
        title, sub = ERROR_PAGES.get(status_code, ("Something went wrong", "That request failed."))
        account = None
        try:
            account = deps.optional_account(request.cookies.get(COOKIE_NAME), None)
        except Exception:  # noqa: BLE001
            account = None
        return deps.templates.TemplateResponse(
            request,
            "error.html",
            {
                "account": account,
                "synced_characters": [],
                "active_character": None,
                "status_code": status_code,
                "error_title": title,
                "error_sub": sub,
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
            return RedirectResponse("/", status_code=303)
        return error_page(request, exc.status_code, exc.detail)

    @app.exception_handler(RequestValidationError)
    def handle_validation_error(request: Request, exc: RequestValidationError) -> Response:
        if not wants_html(request):
            return JSONResponse({"detail": exc.errors()}, status_code=422)
        return error_page(request, 422, None)
