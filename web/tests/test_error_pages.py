from __future__ import annotations

from fastapi.testclient import TestClient

BROWSER = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


def test_a_mistyped_address_renders_a_page_not_a_json_object(client: TestClient):
    """A browser following a bad link used to get ``{"detail": "Not Found"}``, which the
    browser's own JSON viewer then drew as if it were a working screen."""
    response = client.get("/fewqfwe", headers=BROWSER)

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "Nothing here" in response.text
    assert "Go back" in response.text


def test_an_api_client_still_gets_json(client: TestClient):
    response = client.get("/api/nothing-here", headers=BROWSER)

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Not Found"}


def test_a_client_that_did_not_ask_for_html_still_gets_json(client: TestClient):
    response = client.get("/fewqfwe", headers={"accept": "application/json"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_a_signed_out_visitor_is_sent_to_the_landing_page(client: TestClient):
    """401 on a page is a missing sign-in, so the useful answer is the sign-in button, not
    an explanation of what 401 means."""
    response = client.get("/trades", headers=BROWSER, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_a_signed_out_api_call_still_gets_its_401(client: TestClient):
    response = client.get("/api/trades", headers=BROWSER)

    assert response.status_code == 401
    assert response.json() == {"detail": "Not signed in"}


def test_the_error_page_keeps_a_signed_in_visitor_signed_in(signed_in: TestClient):
    response = signed_in.get("/fewqfwe", headers=BROWSER)

    assert response.status_code == 404
    assert "Dashboard" in response.text
