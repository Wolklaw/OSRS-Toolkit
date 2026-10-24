from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE_NAME = "toolkit_session"
MAX_AGE_SECONDS = 30 * 24 * 60 * 60


class SessionSigner:
    def __init__(self, secret: str) -> None:
        self._serializer = URLSafeTimedSerializer(secret, salt="toolkit-web-session")

    def issue(self, discord_user_id: str) -> str:
        return self._serializer.dumps({"discord_user_id": discord_user_id})

    def read(self, cookie_value: str | None) -> str | None:
        if not cookie_value:
            return None
        try:
            data = self._serializer.loads(cookie_value, max_age=MAX_AGE_SECONDS)
        except (BadSignature, SignatureExpired):
            return None
        discord_user_id = data.get("discord_user_id") if isinstance(data, dict) else None
        return discord_user_id if isinstance(discord_user_id, str) else None
