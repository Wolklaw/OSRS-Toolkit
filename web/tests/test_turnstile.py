from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from typing import Self
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from toolkit_web import turnstile
from toolkit_web.app import create_app
from toolkit_web.config import Settings


@pytest.fixture
def guarded(settings: Settings) -> Iterator[TestClient]:
    configured = dataclasses.replace(
        settings, turnstile_site_key="site-key", turnstile_secret_key="secret-key"
    )
    with TestClient(create_app(configured), base_url="https://testserver") as client:
        yield client


def test_no_keys_means_no_challenge_and_no_widget(client: TestClient):
    assert "cf-turnstile" not in client.get("/").text
    assert client.post("/auth/recovery/create", json={"display_name": "Wolklaw"}).status_code == 200


def test_a_site_key_draws_the_widget_on_the_landing_page(guarded: TestClient):
    page = guarded.get("/").text

    assert 'data-sitekey="site-key"' in page
    assert "challenges.cloudflare.com/turnstile/v0/api.js" in page


def test_creating_an_account_without_a_token_is_refused(guarded: TestClient):
    with patch("urllib.request.urlopen") as urlopen:
        response = guarded.post("/auth/recovery/create", json={"display_name": "Bot"})

    assert response.status_code == 400
    urlopen.assert_not_called()
    assert guarded.get("/api/me").status_code == 401


def test_a_token_cloudflare_rejects_is_refused(guarded: TestClient):
    with patch.object(turnstile, "verify", return_value=False):
        response = guarded.post(
            "/auth/recovery/create", json={"display_name": "Bot", "turnstile_token": "forged"}
        )

    assert response.status_code == 400
    assert guarded.get("/api/me").status_code == 401


def test_a_token_cloudflare_accepts_creates_the_account(guarded: TestClient):
    with patch.object(turnstile, "verify", return_value=True) as verify:
        response = guarded.post(
            "/auth/recovery/create", json={"display_name": "Wolklaw", "turnstile_token": "solved"}
        )

    assert response.status_code == 200
    assert response.json()["code"]
    assert guarded.get("/api/me").json()["username"] == "Wolklaw"
    verify.assert_called_once_with("secret-key", "solved")


def test_cloudflare_being_unreachable_asks_for_a_retry_rather_than_blaming_the_visitor(
    guarded: TestClient,
):
    with patch.object(turnstile, "verify", return_value=None):
        response = guarded.post(
            "/auth/recovery/create", json={"display_name": "Wolklaw", "turnstile_token": "solved"}
        )

    assert response.status_code == 503
    assert guarded.get("/api/me").status_code == 401


def test_signing_back_in_with_a_code_needs_no_challenge(guarded: TestClient):
    with patch.object(turnstile, "verify", return_value=True):
        code = guarded.post("/auth/recovery/create", json={"turnstile_token": "solved"}).json()[
            "code"
        ]
    guarded.post("/auth/logout")

    assert guarded.post("/auth/recovery/login", json={"code": code}).status_code == 200


class _Response:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_verify_reads_cloudflares_verdict():
    with patch("urllib.request.urlopen", return_value=_Response({"success": True})):
        assert turnstile.verify("secret", "token") is True

    with patch("urllib.request.urlopen", return_value=_Response({"success": False})):
        assert turnstile.verify("secret", "token") is False


def test_verify_reports_an_unreachable_cloudflare_separately_from_a_rejection():
    with patch("urllib.request.urlopen", side_effect=TimeoutError):
        assert turnstile.verify("secret", "token") is None


def test_verify_refuses_an_empty_token_without_a_round_trip():
    with patch("urllib.request.urlopen") as urlopen:
        assert turnstile.verify("secret", "") is False

    urlopen.assert_not_called()
