"""Runtime settings, all overridable from the environment.

Nothing here has a usable default for the pieces that need an account somewhere else — a
Discord application, a Cloudflare Turnstile widget. Those are None until set, and the app
turns the feature off rather than half-running it: no Discord keys means the OAuth routes
refuse instead of accepting a login nobody could complete, and no Turnstile secret means no
challenge is drawn and none is demanded.
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
    #: Lets this app mint a pairing token on a signed-in visitor's behalf while the sync
    #: service is closed to the public — see ``token_probe.mint_pairing_token``. None while
    #: the sync service is open, since nothing needs to be supplied at that point.
    sync_invite_code: str | None
    #: Issues a session for any Discord id the caller names, with no Discord involved at all.
    #: Exists so the app can be built, tested and demonstrated before a real Discord
    #: application exists to authenticate against. Must never be true where the service is
    #: reachable by anyone but the person developing it.
    dev_login_enabled: bool
    #: Cloudflare Turnstile, which guards account creation without Discord. Both unset means
    #: no challenge is drawn and none is demanded -- the local and test setups, where there is
    #: no Cloudflare account to get keys from and nothing to protect. Set the secret in
    #: production: the check is enforced exactly when a secret exists to check against.
    turnstile_site_key: str | None = None
    turnstile_secret_key: str | None = None
    #: Shared secret for ``POST /internal/check-alerts``, called by an external scheduler
    #: (Task Scheduler, cron) rather than a scheduler living inside this process — see
    #: ``app.py``'s alert-checking route. Unset means the route refuses entirely, the same
    #: "no key means the feature turns off" rule the Discord and Turnstile settings follow.
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
            # Generated fresh on every start where none is set, which is fine for local
            # development — a restart just signs everyone out — and wrong for production,
            # where TOOLKIT_SESSION_SECRET has to be set to something stable or every deploy
            # would log every user out.
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
