from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from toolkit_web.app import create_app
from toolkit_web.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        discord_client_id="test-client-id",
        discord_client_secret="test-client-secret",
        discord_redirect_uri="https://runescope.app/auth/discord/callback",
        session_secret="test-secret",
        data_dir=tmp_path,
        accounts_db_path=tmp_path / "accounts.db",
        sync_base_url="https://sync.example.invalid",
        dev_login_enabled=True,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    # The oauth_state and session cookies are both Secure, correctly, since this is served
    # over HTTPS in production. Testing over plain http://testserver would mean the client's
    # cookie jar silently refusing to send them back, hiding what production actually does —
    # so tests run over https:// too, the same as the deployed service.
    with TestClient(create_app(settings), base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture
def signed_in(client: TestClient) -> TestClient:
    """A client already signed in as one dev-login account, session cookie and all."""
    response = client.post(
        "/auth/dev-login", json={"discord_user_id": "111", "username": "Wolklaw"}
    )
    assert response.status_code == 200
    return client
