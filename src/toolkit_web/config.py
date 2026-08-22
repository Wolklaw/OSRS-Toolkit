"""Runtime settings, all overridable from the environment.

Nothing here has a usable default for the pieces that need a real Discord application — those
are None until set, and the app refuses to start the OAuth routes without them rather than
silently accepting a login nobody could ever complete.
"""

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
    #: Issues a session for any Discord id the caller names, with no Discord involved at all.
    #: Exists so the app can be built, tested and demonstrated before a real Discord
    #: application exists to authenticate against. Must never be true where the service is
    #: reachable by anyone but the person developing it.
    dev_login_enabled: bool

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(os.environ.get("TOOLKIT_DATA_DIR", "data")).expanduser()
        return cls(
            discord_client_id=os.environ.get("DISCORD_CLIENT_ID") or None,
            discord_client_secret=os.environ.get("DISCORD_CLIENT_SECRET") or None,
            discord_redirect_uri=os.environ.get(
                "DISCORD_REDIRECT_URI", "https://runescope.app/auth/discord/callback"
            ),
            # Generated fresh on every start where none is set, which is fine for local
            # development — a restart just signs everyone out — and wrong for production,
            # where TOOLKIT_SESSION_SECRET has to be set to something stable or every deploy
            # would log every user out.
            session_secret=os.environ.get("TOOLKIT_SESSION_SECRET") or secrets.token_urlsafe(32),
            data_dir=data_dir,
            accounts_db_path=data_dir / "accounts.db",
            sync_base_url=os.environ.get("TOOLKIT_SYNC_BASE_URL", "https://sync.runescope.app"),
            dev_login_enabled=_bool("TOOLKIT_DEV_LOGIN", False),
        )

    def journal_path(self, discord_user_id: str) -> Path:
        safe = "".join(c for c in discord_user_id if c.isalnum()) or "unknown"
        return self.data_dir / "journals" / safe / "toolkit.db"
