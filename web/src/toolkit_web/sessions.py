"""Who is asking, carried in a cookie the server can trust without a lookup.

The cookie holds a Discord user id and nothing else, signed so a client cannot forge or alter
it and timestamped so a session eventually expires on its own. There is no server-side session
table to keep in sync with it — trusting the signature is what a session store would otherwise
exist to provide, at the cost of a server restart signing everyone out only if the secret key
itself is not kept stable (see ``Settings.session_secret``).
"""

from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE_NAME = "toolkit_session"
MAX_AGE_SECONDS = 30 * 24 * 60 * 60  # 30 days — a login should outlast a short visit


class SessionSigner:
    def __init__(self, secret: str) -> None:
        self._serializer = URLSafeTimedSerializer(secret, salt="toolkit-web-session")

    def issue(self, discord_user_id: str) -> str:
        return self._serializer.dumps({"discord_user_id": discord_user_id})

    def read(self, cookie_value: str | None) -> str | None:
        """The signed-in user's Discord id, or ``None`` for anyone not validly signed in.

        A missing cookie, a tampered one, an expired one, and one signed under a since-rotated
        secret all mean the same thing to every caller: there is no session. None of them are
        worth telling apart, so none of them raise.
        """
        if not cookie_value:
            return None
        try:
            data = self._serializer.loads(cookie_value, max_age=MAX_AGE_SECONDS)
        except (BadSignature, SignatureExpired):
            return None
        discord_user_id = data.get("discord_user_id") if isinstance(data, dict) else None
        return discord_user_id if isinstance(discord_user_id, str) else None
