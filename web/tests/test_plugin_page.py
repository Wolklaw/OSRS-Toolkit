from __future__ import annotations

from fastapi.testclient import TestClient

from toolkit_web.routes.pages import PLUGIN_BRANCH, PLUGIN_HUB_NAME, PLUGIN_SOURCE_URL


def test_the_plugin_page_reads_signed_out(client: TestClient):
    response = client.get("/plugin")

    assert response.status_code == 200
    assert "Sign in with Discord" not in response.text


def test_it_sends_people_to_the_plugin_hub(client: TestClient):
    page = client.get("/plugin").text

    assert "Plugin Hub" in page
    assert "OSRS Toolkit Sync" in page


def test_it_names_the_wrench_and_the_search(client: TestClient):
    page = client.get("/plugin").text

    assert "wrench" in page
    assert "Install" in page


def test_it_prepares_people_for_runelites_own_install_warning(client: TestClient):
    page = client.get("/plugin").text

    assert "IP address" in page
    assert 'href="/about"' in page


def test_it_does_not_tell_anyone_to_build_it(client: TestClient):
    page = client.get("/plugin").text

    assert "gradlew" not in page
    assert "shadowJar" not in page
    assert "Temurin" not in page
    assert "build.gradle" not in page
    assert "java -ea" not in page


def test_it_no_longer_walks_anyone_through_writing_jagex_credentials_to_disk(
    client: TestClient,
):
    page = client.get("/plugin").text

    assert "--insecure-write-credentials" not in page
    assert "credentials.properties" not in page


def test_it_does_not_still_claim_the_submission_is_pending(client: TestClient):
    page = client.get("/plugin").text

    assert "Not on the Plugin Hub yet" not in page
    assert "in review" not in page


def test_it_links_the_plugin_repository(client: TestClient):
    assert "https://github.com/Wolklaw/osrs-toolkit-runelite" in client.get("/plugin").text


def test_the_source_link_is_built_from_the_branch_rather_than_written_out():
    assert PLUGIN_SOURCE_URL.endswith(f"/tree/{PLUGIN_BRANCH}")


def test_the_hub_name_matches_the_manifest_filename():
    assert PLUGIN_HUB_NAME == "osrs-toolkit-sync"


def test_it_still_explains_pairing(client: TestClient):
    page = client.get("/plugin").text

    assert "Get a token" in page
    assert "Paste the token" in page
    assert "Send to OSRS Toolkit Sync" in page


def test_both_setup_pages_are_reachable_from_the_navigation(signed_in: TestClient):
    page = signed_in.get("/").text

    assert 'href="/plugin"' in page
    assert 'href="/download"' in page
