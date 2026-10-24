from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    discord_client_id: str | None
    discord_client_secret: str | None
    discord_redirect_uri: str
    session_secret: str
    data_dir: Path
    accounts_db_path: Path
    sync_base_url: str
    sync_invite_code: str | None
    dev_login_enabled: bool
    turnstile_site_key: str | None = None
    turnstile_secret_key: str | None = None
    internal_task_token: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(os.environ.get("TOOLKIT_DATA_DIR", "data")).expanduser()
        return cls(
            discord_client_id=os.environ.get("DISCORD_CLIENT_ID") or None,
            discord_client_secret=os.environ.get("DISCORD_CLIENT_SECRET") or None,
            discord_redirect_uri=os.environ.get(
                "DISCORD_REDIRECT_URI", "https://runescope.app/auth/discord/callback"
            ),
            session_secret=os.environ.get("TOOLKIT_SESSION_SECRET") or secrets.token_urlsafe(32),
            data_dir=data_dir,
            accounts_db_path=data_dir / "accounts.db",
            sync_base_url=os.environ.get("TOOLKIT_SYNC_BASE_URL", "https://sync.runescope.app"),
            sync_invite_code=os.environ.get("TOOLKIT_SYNC_INVITE_CODE") or None,
            dev_login_enabled=_bool("TOOLKIT_DEV_LOGIN", False),
            turnstile_site_key=os.environ.get("TURNSTILE_SITE_KEY") or None,
            turnstile_secret_key=os.environ.get("TURNSTILE_SECRET_KEY") or None,
            internal_task_token=os.environ.get("TOOLKIT_INTERNAL_TASK_TOKEN") or None,
        )

    def journal_dir(self, discord_user_id: str) -> Path:
        safe = "".join(c for c in discord_user_id if c.isalnum()) or "unknown"
        return self.data_dir / "journals" / safe

    def journal_path(self, discord_user_id: str) -> Path:
        return self.journal_dir(discord_user_id) / "toolkit.db"
