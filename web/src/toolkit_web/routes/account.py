from __future__ import annotations

import secrets
import shutil

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from toolkit_web import discord_oauth, turnstile
from toolkit_web.accounts_db import Account
from toolkit_web.deps import Deps
from toolkit_web.discord_webhook import is_discord_webhook_url
from toolkit_web.importing import journal_for, plugin_activity
from toolkit_web.presentation import (
    _RECOVERY_ID_PREFIX,
    _generate_recovery_code,
    _recovery_account_id,
)
from toolkit_web.serialize import serialize
from toolkit_web.sessions import COOKIE_NAME, MAX_AGE_SECONDS
from toolkit_web.token_probe import mint_pairing_token, probe_token, revoke_pairing_token

STATE_COOKIE = "oauth_state"


def build(deps: Deps) -> APIRouter:
    router = APIRouter()
    settings = deps.settings
    accounts = deps.accounts
    signer = deps.signer
    templates = deps.templates
    CurrentAccount = deps.CurrentAccount
    BrowserAccount = deps.BrowserAccount
    ActiveCharacter = deps.ActiveCharacter

    @router.get("/profile", response_class=HTMLResponse)
    def profile_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        switcher = deps.character_switcher_context(account, active)
        repository = journal_for(settings, account)
        return templates.TemplateResponse(
            request,
            "profile.html",
            {
                "account": account,
                "plugin_state": plugin_activity(settings, account),
                "is_recovery_account": account.discord_user_id.startswith(_RECOVERY_ID_PREFIX),
                "characters": [
                    serialize(character)
                    for character in accounts.list_characters(account.discord_user_id)
                ],
                "position_count": len(repository.list_tracked()),
                "trade_count": len(repository.list_all()),
                "watchlist_count": len(accounts.list_watchlist_item_ids(account.discord_user_id)),
                "desktop_tokens": [
                    {
                        "id": token.token_sha256,
                        "label": token.label,
                        "created_at": token.created_at,
                        "last_used_at": token.last_used_at,
                    }
                    for token in accounts.list_desktop_tokens(account.discord_user_id)
                ],
                **switcher,
            },
        )

    @router.get("/auth/discord/login")
    def discord_login() -> Response:
        if not settings.discord_client_id:
            raise HTTPException(
                status_code=503,
                detail="Discord sign-in is not configured on this server yet.",
            )
        state = secrets.token_urlsafe(24)
        url = discord_oauth.authorize_url(
            settings.discord_client_id, settings.discord_redirect_uri, state
        )
        response = RedirectResponse(url, status_code=302)
        response.set_cookie(
            STATE_COOKIE, state, max_age=600, httponly=True, secure=True, samesite="lax"
        )
        return response

    @router.get("/auth/discord/callback")
    def discord_callback(
        request: Request,
        code: str | None = None,
        state: str | None = None,
        oauth_state: str | None = Cookie(default=None, alias=STATE_COOKIE),
    ) -> Response:
        if not settings.discord_client_id or not settings.discord_client_secret:
            raise HTTPException(status_code=503, detail="Discord sign-in is not configured.")
        if (
            not code
            or not state
            or not oauth_state
            or not secrets.compare_digest(state, oauth_state)
        ):
            raise HTTPException(status_code=400, detail="Invalid or expired sign-in attempt.")
        access_token = discord_oauth.exchange_code(
            settings.discord_client_id,
            settings.discord_client_secret,
            settings.discord_redirect_uri,
            code,
        )
        user = discord_oauth.fetch_user(access_token) if access_token else None
        if user is None:
            raise HTTPException(status_code=502, detail="Discord did not confirm the sign-in.")
        accounts.upsert_login(user.id, user.username, user.avatar_url)
        session = signer.issue(user.id)
        response = RedirectResponse("/", status_code=302)
        response.delete_cookie(STATE_COOKIE)
        response.set_cookie(
            COOKIE_NAME,
            session,
            max_age=MAX_AGE_SECONDS,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return response

    if settings.dev_login_enabled:
        @router.post("/auth/dev-login")
        def dev_login(payload: dict) -> Response:
            discord_user_id = payload.get("discord_user_id")
            username = payload.get("username", "Dev User")
            avatar = payload.get("avatar")
            if not isinstance(discord_user_id, str) or not discord_user_id:
                raise HTTPException(status_code=422, detail="discord_user_id is required")
            accounts.upsert_login(discord_user_id, username, avatar)
            response = JSONResponse({"ok": True})
            response.set_cookie(
                COOKIE_NAME,
                signer.issue(discord_user_id),
                max_age=MAX_AGE_SECONDS,
                httponly=True,
                secure=True,
                samesite="lax",
            )
            return response

    @router.post("/auth/recovery/create")
    def create_recovery_account(payload: dict | None = None) -> dict:
        payload = payload or {}
        if settings.turnstile_secret_key:
            token = payload.get("turnstile_token")
            verdict = turnstile.verify(
                settings.turnstile_secret_key, token if isinstance(token, str) else ""
            )
            if verdict is None:
                raise HTTPException(
                    status_code=503, detail="Couldn't check the box just now, try again."
                )
            if not verdict:
                raise HTTPException(
                    status_code=400, detail="Tick the box to show you are human, then try again."
                )
        display_name = payload.get("display_name")
        display_name = display_name.strip() if isinstance(display_name, str) else ""
        code = _generate_recovery_code()
        account_id = _recovery_account_id(code)
        accounts.upsert_login(account_id, display_name or "Player", None)
        response = JSONResponse({"ok": True, "code": code})
        response.set_cookie(
            COOKIE_NAME,
            signer.issue(account_id),
            max_age=MAX_AGE_SECONDS,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return response

    @router.post("/auth/recovery/login")
    def recovery_login(payload: dict) -> Response:
        code = payload.get("code")
        if not isinstance(code, str) or not code.strip():
            raise HTTPException(status_code=422, detail="Enter your recovery code.")
        account = accounts.get(_recovery_account_id(code))
        if account is None:
            raise HTTPException(status_code=401, detail="That code was not recognized.")
        accounts.upsert_login(account.discord_user_id, account.username, account.avatar)
        response = JSONResponse({"ok": True})
        response.set_cookie(
            COOKIE_NAME,
            signer.issue(account.discord_user_id),
            max_age=MAX_AGE_SECONDS,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return response

    @router.post("/auth/logout")
    def logout() -> Response:
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE_NAME)
        return response

    @router.get("/api/me")
    def me(account: Account = CurrentAccount) -> dict:
        return {
            "discord_user_id": account.discord_user_id,
            "username": account.username,
            "avatar_url": account.avatar,
            "paired": account.paired,
        }

    @router.patch("/api/me")
    def rename_account(payload: dict, account: Account = CurrentAccount) -> dict:
        if not account.discord_user_id.startswith(_RECOVERY_ID_PREFIX):
            raise HTTPException(
                status_code=409,
                detail="Your name comes from Discord, change it there and sign in again.",
            )
        username = payload.get("username")
        if not isinstance(username, str) or not username.strip():
            raise HTTPException(status_code=422, detail="Enter a display name.")
        accounts.set_display_name(account.discord_user_id, username.strip()[:40])
        return {"ok": True}

    @router.post("/api/account/delete")
    def delete_account(account: Account = BrowserAccount) -> Response:
        if account.pairing_token:
            revoke_pairing_token(settings.sync_base_url, account.pairing_token)
        shutil.rmtree(settings.journal_dir(account.discord_user_id), ignore_errors=True)
        accounts.delete_account(account.discord_user_id)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE_NAME)
        return response

    @router.put("/api/pairing-token")
    def set_pairing_token(payload: dict, account: Account = BrowserAccount) -> dict:
        token = payload.get("token")
        if not isinstance(token, str) or not token.strip():
            raise HTTPException(status_code=422, detail="token is required")
        token = token.strip()
        valid = probe_token(settings.sync_base_url, token)
        if valid is False:
            raise HTTPException(status_code=422, detail="That pairing token was not accepted.")
        accounts.set_pairing_token(account.discord_user_id, token)
        return {"ok": True, "verified": valid is True}

    @router.delete("/api/pairing-token")
    def clear_pairing_token(account: Account = BrowserAccount) -> dict:
        accounts.set_pairing_token(account.discord_user_id, None)
        return {"ok": True}

    @router.put("/api/discord-webhook")
    def set_discord_webhook(payload: dict, account: Account = BrowserAccount) -> dict:
        url = payload.get("url")
        if not isinstance(url, str) or not url.strip():
            raise HTTPException(status_code=422, detail="url is required")
        url = url.strip()
        if not is_discord_webhook_url(url):
            raise HTTPException(
                status_code=422,
                detail="That doesn't look like a Discord webhook URL "
                "(discord.com/api/webhooks/...).",
            )
        accounts.set_discord_webhook(account.discord_user_id, url)
        return {"ok": True}

    @router.delete("/api/discord-webhook")
    def clear_discord_webhook(account: Account = BrowserAccount) -> dict:
        accounts.set_discord_webhook(account.discord_user_id, None)
        return {"ok": True}

    @router.post("/api/pairing-token/generate")
    def generate_pairing_token(account: Account = BrowserAccount) -> dict:
        previous = account.pairing_token
        if previous:
            journal_for(settings, account)
        token = mint_pairing_token(settings.sync_base_url, settings.sync_invite_code)
        if token is None:
            raise HTTPException(
                status_code=502, detail="Couldn't reach the sync service, try again shortly."
            )
        accounts.set_pairing_token(account.discord_user_id, token)
        retired = bool(previous) and revoke_pairing_token(settings.sync_base_url, previous)
        return {"ok": True, "token": token, "previous_revoked": retired}

    @router.get("/api/desktop-tokens")
    def list_desktop_tokens(account: Account = BrowserAccount) -> dict:
        return {
            "tokens": [
                {
                    "id": token.token_sha256,
                    "label": token.label,
                    "created_at": token.created_at,
                    "last_used_at": token.last_used_at,
                }
                for token in accounts.list_desktop_tokens(account.discord_user_id)
            ]
        }

    @router.post("/api/desktop-tokens", status_code=201)
    def create_desktop_token(payload: dict, account: Account = BrowserAccount) -> dict:
        label = payload.get("label")
        label = label.strip()[:60] if isinstance(label, str) and label.strip() else "Desktop app"
        token = accounts.create_desktop_token(account.discord_user_id, label)
        return {"ok": True, "token": token, "label": label}

    @router.delete("/api/desktop-tokens/{token_id}")
    def revoke_desktop_token(token_id: str, account: Account = BrowserAccount) -> dict:
        if not accounts.revoke_desktop_token(account.discord_user_id, token_id):
            raise HTTPException(status_code=404, detail="No such desktop credential.")
        return {"ok": True}

    return router
