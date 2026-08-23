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
    assert "java -jar build/libs/osrs-toolkit-sync-1.0.0-all.jar" in page


def test_it_links_the_plugin_repository(client: TestClient):
    assert "https://github.com/Wolklaw/osrs-toolkit-runelite" in client.get("/plugin").text


def test_every_way_of_getting_the_source_names_the_branch(client: TestClient):
    """The repository's default branch is the older, file-based plugin — no service address,
    no pairing token. An unqualified "download the ZIP" hands somebody a build that cannot
    reach this site at all, so neither route here is allowed to omit the branch."""
    page = client.get("/plugin").text

    assert "archive/refs/heads/web-sync.zip" in page
    assert "git clone -b web-sync" in page
    assert "/tree/web-sync" in page


def test_it_never_offers_a_default_branch_download(client: TestClient):
    page = client.get("/plugin").text

    assert "archive/refs/heads/main.zip" not in page
    # A bare clone takes the default branch, which is exactly the wrong one here.
    assert "git clone https://github.com/Wolklaw/osrs-toolkit-runelite" not in page


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
