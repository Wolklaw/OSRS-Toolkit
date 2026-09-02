from __future__ import annotations

from fastapi.testclient import TestClient


def test_the_manifest_is_served_and_points_at_real_icons(client: TestClient):
    response = client.get("/static/manifest.webmanifest")

    assert response.status_code == 200
    manifest = response.json()
    assert manifest["start_url"] == "/"
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200


def test_the_service_worker_is_served_from_the_root_scope(client: TestClient):
    response = client.get("/sw.js")

    assert response.status_code == 200
    assert "application/javascript" in response.headers["content-type"]


def test_the_base_template_links_the_manifest_and_registers_the_worker(client: TestClient):
    response = client.get("/")

    assert 'rel="manifest" href="/static/manifest.webmanifest"' in response.text
    assert "navigator.serviceWorker.register('/sw.js')" in response.text


def test_the_offline_page_renders_without_an_account(client: TestClient):
    response = client.get("/offline")

    assert response.status_code == 200
    assert "offline" in response.text.lower()


def test_the_service_worker_precaches_the_offline_page(client: TestClient):
    response = client.get("/sw.js")

    assert "'/offline'" in response.text
