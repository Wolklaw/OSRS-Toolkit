"""Desktop credentials: minting them, signing in with them, and what they may not do.

The desktop window has no browser and so no session cookie. It presents a token minted here
instead. These cover the two halves that matter: that the token really does stand in for the
account on the read API, and that it deliberately does not stand in for it everywhere.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _mint(signed_in: TestClient, label: str = "Desktop app") -> str:
    response = signed_in.post("/api/desktop-tokens", json={"label": label})
    assert response.status_code == 201
    return response.json()["token"]


def test_token_is_returned_once_and_never_again(signed_in: TestClient) -> None:
    token = _mint(signed_in, "Desktop on wolkpc")
    assert token

    listed = signed_in.get("/api/desktop-tokens").json()["tokens"]
    assert len(listed) == 1
    assert listed[0]["label"] == "Desktop on wolkpc"
    # The secret itself must not survive anywhere it could be read back out.
    assert token not in str(listed)


def test_bearer_token_reaches_the_journal_api(client: TestClient, signed_in: TestClient) -> None:
    token = _mint(signed_in)
    # A brand new client: no cookie jar, nothing but the header, the way the desktop arrives.
    with TestClient(signed_in.app, base_url="https://testserver") as bare:
        response = bare.get("/api/trades", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert "trades" in response.json()


def test_bearer_token_resolves_to_its_own_account(signed_in: TestClient) -> None:
    """The token must name the account that minted it, not merely *an* account."""
    token = _mint(signed_in)
    signed_in.post(
        "/api/trades",
        json={"item_name": "Rune scimitar", "quantity": 1, "buy_price": 10, "sell_price": 20},
    )

    with TestClient(signed_in.app, base_url="https://testserver") as bare:
        trades = bare.get("/api/trades", headers={"Authorization": f"Bearer {token}"}).json()
        assert [trade["item_name"] for trade in trades["trades"]] == ["Rune scimitar"]

        me = bare.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()
        assert me["username"] == "Wolklaw"


def test_desktop_token_can_write_a_trade_back(signed_in: TestClient) -> None:
    """Read-write, not read-only: a trade recorded in the desktop window has to land here."""
    token = _mint(signed_in)
    with TestClient(signed_in.app, base_url="https://testserver") as bare:
        created = bare.post(
            "/api/trades",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "item_name": "Dragon bones",
                "quantity": 100,
                "buy_price": 2000,
                "sell_price": 2600,
            },
        )
        assert created.status_code == 201

    # and the browser session sees it, because it is the same journal
    trades = signed_in.get("/api/trades").json()["trades"]
    assert any(trade["item_name"] == "Dragon bones" for trade in trades)


def test_garbage_and_malformed_headers_are_refused(client: TestClient) -> None:
    for header in ("Bearer not-a-real-token", "Bearer ", "Basic abc123", "not-a-header", ""):
        response = client.get("/api/trades", headers={"Authorization": header})
        assert response.status_code == 401, header


def test_desktop_token_cannot_manage_desktop_tokens(signed_in: TestClient) -> None:
    """A leaked desktop credential must not be able to outlive its own revocation by
    minting a second one, nor sign the real desktop out."""
    token = _mint(signed_in)
    existing = signed_in.get("/api/desktop-tokens").json()["tokens"][0]["id"]

    with TestClient(signed_in.app, base_url="https://testserver") as bare:
        header = {"Authorization": f"Bearer {token}"}
        assert bare.post("/api/desktop-tokens", headers=header, json={}).status_code == 401
        assert bare.get("/api/desktop-tokens", headers=header).status_code == 401
        assert bare.delete(f"/api/desktop-tokens/{existing}", headers=header).status_code == 401


def test_revoking_stops_the_token_working(signed_in: TestClient) -> None:
    token = _mint(signed_in)
    token_id = signed_in.get("/api/desktop-tokens").json()["tokens"][0]["id"]

    assert signed_in.delete(f"/api/desktop-tokens/{token_id}").status_code == 200

    with TestClient(signed_in.app, base_url="https://testserver") as bare:
        response = bare.get("/api/trades", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
    assert signed_in.get("/api/desktop-tokens").json()["tokens"] == []


def test_revoking_someone_elses_token_is_a_404(client: TestClient, signed_in: TestClient) -> None:
    _mint(signed_in)
    token_id = signed_in.get("/api/desktop-tokens").json()["tokens"][0]["id"]

    other = client.post("/auth/dev-login", json={"discord_user_id": "222", "username": "Someone"})
    assert other.status_code == 200
    assert client.delete(f"/api/desktop-tokens/{token_id}").status_code == 404


def test_last_used_at_starts_empty_and_is_stamped_by_use(signed_in: TestClient) -> None:
    token = _mint(signed_in)
    assert signed_in.get("/api/desktop-tokens").json()["tokens"][0]["last_used_at"] is None

    with TestClient(signed_in.app, base_url="https://testserver") as bare:
        bare.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert signed_in.get("/api/desktop-tokens").json()["tokens"][0]["last_used_at"] is not None
