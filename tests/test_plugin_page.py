"""The plugin page after the Plugin Hub merged the submission on 2026-08-25.

What this page used to be -- Java, a source ZIP, ``gradlew shadowJar``, running a jar that
started its own client, and the Jagex Launcher credentials dance that a command-line client
forced on people -- is all gone, because the Hub does it now. These cover what replaced it and,
just as importantly, that none of the old story came back.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from toolkit_web.app import PLUGIN_BRANCH, PLUGIN_HUB_NAME, PLUGIN_SOURCE_URL


def test_the_plugin_page_reads_signed_out(client: TestClient):
    """Somebody deciding whether this is worth signing up for is exactly who needs it."""
    response = client.get("/plugin")

    assert response.status_code == 200
    assert "Sign in with Discord" not in response.text


def test_it_sends_people_to_the_plugin_hub(client: TestClient):
    page = client.get("/plugin").text

    assert "Plugin Hub" in page
    assert "OSRS Toolkit Sync" in page


def test_it_names_the_wrench_and_the_search(client: TestClient):
    """"Install it from the Hub" is not directions. Where to click is."""
    page = client.get("/plugin").text

    assert "wrench" in page
    assert "Install" in page


def test_it_prepares_people_for_runelites_own_install_warning(client: TestClient):
    """RuneLite shows the manifest's warning line before installing. Somebody who meets that
    cold reads it as "this plugin is dodgy" rather than "this is the site I just signed up
    for", so the page says it first and links what is actually sent."""
    page = client.get("/plugin").text

    assert "IP address" in page
    assert 'href="/about"' in page


def test_it_does_not_tell_anyone_to_build_it(client: TestClient):
    """The whole point of the Hub listing. Any of this surviving sends somebody off to install
    Java for a plugin they can get in two clicks."""
    page = client.get("/plugin").text

    assert "gradlew" not in page
    assert "shadowJar" not in page
    assert "Temurin" not in page
    assert "build.gradle" not in page
    assert "java -ea" not in page


def test_it_no_longer_walks_anyone_through_writing_jagex_credentials_to_disk(
    client: TestClient,
):
    """That section only ever existed because a command-line client got no login from the
    Jagex Launcher. A Hub install is just RuneLite, so telling people to turn on a flag whose
    own name is "insecure" would now be advice with no upside at all."""
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
    """Nobody builds from it now, but a source link pointing at a branch that predates the web
    plugin would still be showing people the wrong code."""
    assert PLUGIN_SOURCE_URL.endswith(f"/tree/{PLUGIN_BRANCH}")


def test_the_hub_name_matches_the_manifest_filename():
    """What people type into the Hub's search box, and the filename in runelite/plugin-hub."""
    assert PLUGIN_HUB_NAME == "osrs-toolkit-sync"


def test_it_still_explains_pairing(client: TestClient):
    """Installing is only half of it: a plugin with no token sends nowhere."""
    page = client.get("/plugin").text

    assert "Pairing token" in page
    assert "Send to OSRS Toolkit Sync" in page


def test_both_setup_pages_are_reachable_from_the_navigation(signed_in: TestClient):
    page = signed_in.get("/").text

    assert 'href="/plugin"' in page
    assert 'href="/download"' in page
