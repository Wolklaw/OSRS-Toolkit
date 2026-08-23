from __future__ import annotations

from fastapi.testclient import TestClient


def test_the_plugin_page_reads_signed_out(client: TestClient):
    """Somebody deciding whether this is worth signing up for is exactly who needs it."""
    response = client.get("/plugin")

    assert response.status_code == 200
    assert "Sign in with Discord" not in response.text


def test_it_says_the_hub_listing_is_not_available_yet(client: TestClient):
    """The page would be useless if it only listed a route that does not work yet."""
    page = client.get("/plugin").text

    assert "Not on the Plugin Hub yet" in page


def test_it_carries_the_build_and_run_commands(client: TestClient):
    page = client.get("/plugin").text

    assert "gradlew shadowJar" in page
    assert "java -jar build/libs/osrs-toolkit-sync-unspecified-all.jar" in page


def test_it_links_the_plugin_repository(client: TestClient):
    assert "https://github.com/Wolklaw/osrs-toolkit-runelite" in client.get("/plugin").text


def test_it_does_not_promise_the_plugin_lands_in_an_existing_runelite(client: TestClient):
    """The jar starts its own client. Saying otherwise would send people hunting a plugin
    list it was never added to."""
    assert "starts its own copy of RuneLite" in client.get("/plugin").text


def test_both_setup_pages_are_reachable_from_the_navigation(signed_in: TestClient):
    page = signed_in.get("/").text

    assert 'href="/plugin"' in page
    assert 'href="/download"' in page
