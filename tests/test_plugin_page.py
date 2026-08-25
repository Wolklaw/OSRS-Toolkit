from __future__ import annotations

from fastapi.testclient import TestClient

from toolkit_web.app import PLUGIN_BRANCH, PLUGIN_SOURCE_URL, PLUGIN_ZIP_URL


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
    assert "java -jar build/libs/osrs-toolkit-sync-1.0.0-all.jar" in page


def test_it_warns_about_windows_extracting_a_doubled_folder(client: TestClient):
    """Windows' own extractor nests the zip's single top folder inside a second folder of the
    same name, so people open a terminal one level above gradlew and get "not recognized"."""
    page = client.get("/plugin").text

    assert "build.gradle" in page
    assert "not recognized" in page


def test_it_links_the_plugin_repository(client: TestClient):
    assert "https://github.com/Wolklaw/osrs-toolkit-runelite" in client.get("/plugin").text


def test_the_download_the_browse_link_and_the_folder_agree_on_a_branch(client: TestClient):
    """These three drifting apart is what broke this page once already: the ZIP came from one
    branch and the instructions described another, so people built a plugin with none of the
    settings the next section told them to fill in. All three are derived from one constant."""
    page = client.get("/plugin").text

    assert PLUGIN_ZIP_URL in page
    assert PLUGIN_SOURCE_URL in page
    assert f"osrs-toolkit-runelite-{PLUGIN_BRANCH}" in page


def test_the_source_urls_are_built_from_the_branch_rather_than_written_out():
    assert PLUGIN_ZIP_URL.endswith(f"/archive/refs/heads/{PLUGIN_BRANCH}.zip")
    assert PLUGIN_SOURCE_URL.endswith(f"/tree/{PLUGIN_BRANCH}")


def test_it_does_not_promise_the_plugin_lands_in_an_existing_runelite(client: TestClient):
    """The jar starts its own client. Saying otherwise would send people hunting a plugin
    list it was never added to."""
    assert "starts its own copy of RuneLite" in client.get("/plugin").text


def test_it_gives_real_steps_for_a_jagex_account(client: TestClient):
    """A command-line client gets no login from the Jagex Launcher, which is a dead end for
    most players. Naming the flag and where it writes is the whole answer."""
    page = client.get("/plugin").text

    assert "--insecure-write-credentials" in page
    assert ".runelite/credentials.properties" in page
    assert "2.6.3" in page


def test_it_credits_runelites_own_guide_for_those_steps(client: TestClient):
    assert "runelite/wiki/Using-Jagex-Accounts" in client.get("/plugin").text


def test_it_warns_that_the_credentials_file_bypasses_a_password(client: TestClient):
    """The flag is called "insecure" for a reason, and the page has to say why before
    somebody pastes the file into a support thread."""
    page = client.get("/plugin").text

    assert "login bypass" in page
    assert "End sessions" in page


def test_both_setup_pages_are_reachable_from_the_navigation(signed_in: TestClient):
    page = signed_in.get("/").text

    assert 'href="/plugin"' in page
    assert 'href="/download"' in page
