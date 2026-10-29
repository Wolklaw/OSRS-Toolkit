from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from toolkit_web.app import create_app
from toolkit_web.config import Settings


@pytest.fixture(autouse=True)
def _clear_process_caches():
    from toolkit_web import importing

    importing._activity_cache.clear()
    importing._characters_cache.clear()
    yield
    importing._activity_cache.clear()
    importing._characters_cache.clear()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        discord_client_id="test-client-id",
        discord_client_secret="test-client-secret",
        discord_redirect_uri="https://runescope.app/auth/discord/callback",
        session_secret="test-secret",
        data_dir=tmp_path,
        accounts_db_path=tmp_path / "accounts.db",
        upscape_scores_db_path=tmp_path / "upscape-scores.db",
        sync_base_url="https://sync.example.invalid",
        sync_invite_code="test-invite-code",
        dev_login_enabled=True,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings), base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture
def signed_in(client: TestClient) -> TestClient:
    response = client.post(
        "/auth/dev-login", json={"discord_user_id": "111", "username": "Wolklaw"}
    )
    assert response.status_code == 200
    return client
