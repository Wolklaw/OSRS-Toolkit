from __future__ import annotations

import re

from fastapi.testclient import TestClient


def test_creating_an_account_returns_a_code_and_signs_in(client: TestClient):
    response = client.post("/auth/recovery/create", json={"display_name": "Wolklaw"})

    assert response.status_code == 200
    code = response.json()["code"]
    assert re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", code)
    assert client.get("/api/me").json()["username"] == "Wolklaw"


def test_a_blank_display_name_falls_back_to_a_generic_one(client: TestClient):
    client.post("/auth/recovery/create", json={"display_name": ""})

    assert client.get("/api/me").json()["username"] == "Player"


def test_the_code_never_appears_anywhere_but_the_creation_response(client: TestClient):
    """Nothing server-side should ever be able to show this code again — that is the whole
    point of it working with no email address behind it."""
    response = client.post("/auth/recovery/create", json={})
    code = response.json()["code"]

    me = client.get("/api/me")
    assert code not in me.text


def test_signing_back_in_with_the_same_code_reaches_the_same_account(client: TestClient):
    created = client.post("/auth/recovery/create", json={"display_name": "Wolklaw"})
    code = created.json()["code"]
    client.post("/auth/logout")
    assert client.get("/api/me").status_code == 401

    response = client.post("/auth/recovery/login", json={"code": code})

    assert response.status_code == 200
    assert client.get("/api/me").json()["username"] == "Wolklaw"


def test_signing_in_reaches_the_same_journal_the_account_already_has(client: TestClient):
    from osrs_toolkit.journal import JournalRepository

    from toolkit_web.config import Settings

    created = client.post("/auth/recovery/create", json={})
    code = created.json()["code"]
    settings: Settings = client.app.state.settings
    me = client.get("/api/me").json()
    JournalRepository(settings.journal_path(me["discord_user_id"])).track(
        4151, "Abyssal whip", 1, target_buy=1_000_000, target_sell=1_100_000
    )
    client.post("/auth/logout")

    client.post("/auth/recovery/login", json={"code": code})

    [position] = client.get("/api/positions").json()["positions"]
    assert position["item_name"] == "Abyssal whip"


def test_the_code_is_not_case_or_dash_sensitive_when_signing_back_in(client: TestClient):
    created = client.post("/auth/recovery/create", json={"display_name": "Wolklaw"})
    code = created.json()["code"]
    client.post("/auth/logout")

    mangled = code.lower().replace("-", " ")
    response = client.post("/auth/recovery/login", json={"code": mangled})

    assert response.status_code == 200


def test_a_wrong_code_is_refused(client: TestClient):
    response = client.post("/auth/recovery/login", json={"code": "NOPE-NOPE-NOPE-NOPE"})

    assert response.status_code == 401


def test_a_blank_code_is_refused(client: TestClient):
    assert client.post("/auth/recovery/login", json={"code": ""}).status_code == 422


def test_two_created_accounts_never_collide(client: TestClient):
    first = client.post("/auth/recovery/create", json={"display_name": "First"}).json()["code"]
    second = client.post("/auth/recovery/create", json={"display_name": "Second"}).json()["code"]

    assert first != second

    client.post("/auth/recovery/login", json={"code": first})
    assert client.get("/api/me").json()["username"] == "First"

    client.post("/auth/recovery/login", json={"code": second})
    assert client.get("/api/me").json()["username"] == "Second"


def test_a_recovery_account_never_collides_with_a_discord_id(client: TestClient):
    """Discord ids are bare numeric snowflakes; a recovery account's id is namespaced so a
    coincidence at that layer can never sign one person in as someone else."""
    from toolkit_web.app import _recovery_account_id

    assert not _recovery_account_id("anything").removeprefix("local:").isdigit()


def test_the_landing_page_shows_both_paths_when_signed_out(client: TestClient):
    response = client.get("/")

    assert "Sign in with Discord" in response.text
    assert "Already have a code" in response.text
    assert "Create account" in response.text
