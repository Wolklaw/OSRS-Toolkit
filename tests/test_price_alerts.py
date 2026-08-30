from __future__ import annotations

import dataclasses
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from osrs_toolkit.models import ItemMapping, MarketPoint

from toolkit_web.app import create_app
from toolkit_web.config import Settings

_WHIP_ID = 4151
_VALID_WEBHOOK = "https://discord.com/api/webhooks/123456/abcDEF"


def _market_snapshot(sell_price: int = 1_600_000, buy_price: int = 1_500_000):
    now = int(time.time())
    mapping = {
        _WHIP_ID: ItemMapping(
            item_id=_WHIP_ID, name="Abyssal whip", members=True, buy_limit=70, high_alch=100_000
        )
    }
    point = MarketPoint(
        item_id=_WHIP_ID,
        high=sell_price,
        low=buy_price,
        high_time=now,
        low_time=now,
        volume_5m=200,
        volume_1h=2_000,
        average_5m=(sell_price + buy_price) // 2,
        average_1h=(sell_price + buy_price) // 2,
        average_high_5m=sell_price,
        average_low_5m=buy_price,
        average_high_1h=sell_price,
        average_low_1h=buy_price,
    )
    return mapping, [point]


def _patched_market(**kwargs):
    return patch(
        "toolkit_web.market_cache.WikiMarketClient.fetch_snapshot",
        return_value=_market_snapshot(**kwargs),
    )


# -- Discord webhook on profile --------------------------------------------------------------


def test_saving_a_non_discord_url_is_refused(signed_in: TestClient):
    response = signed_in.put("/api/discord-webhook", json={"url": "https://evil.example/steal"})

    assert response.status_code == 422


def test_saving_a_real_discord_webhook_url_works(signed_in: TestClient):
    response = signed_in.put("/api/discord-webhook", json={"url": _VALID_WEBHOOK})

    assert response.status_code == 200
    assert _VALID_WEBHOOK in signed_in.get("/profile").text


def test_clearing_the_webhook_removes_it(signed_in: TestClient):
    signed_in.put("/api/discord-webhook", json={"url": _VALID_WEBHOOK})

    response = signed_in.delete("/api/discord-webhook")

    assert response.status_code == 200
    assert _VALID_WEBHOOK not in signed_in.get("/profile").text


# -- arming an alert on a watchlist item ------------------------------------------------------


def test_setting_an_alert_without_a_webhook_is_refused(signed_in: TestClient):
    with _patched_market():
        response = signed_in.put(
            f"/api/watchlist/{_WHIP_ID}/alert",
            json={"target_price": 1_000_000, "direction": "above"},
        )

    assert response.status_code == 422


def test_setting_an_alert_with_an_invalid_direction_is_refused(signed_in: TestClient):
    signed_in.put("/api/discord-webhook", json={"url": _VALID_WEBHOOK})

    response = signed_in.put(
        f"/api/watchlist/{_WHIP_ID}/alert",
        json={"target_price": 1_000_000, "direction": "sideways"},
    )

    assert response.status_code == 422


def test_setting_an_alert_watches_the_item_and_shows_it_on_the_page(signed_in: TestClient):
    signed_in.put("/api/discord-webhook", json={"url": _VALID_WEBHOOK})

    with _patched_market():
        response = signed_in.put(
            f"/api/watchlist/{_WHIP_ID}/alert",
            json={"target_price": 1_400_000, "direction": "below"},
        )
        assert response.status_code == 200
        page = signed_in.get("/watchlist").text

    assert "Abyssal whip" in page
    assert 'value="1400000"' in page


def test_clearing_an_alert_leaves_the_item_watched(signed_in: TestClient):
    signed_in.put("/api/discord-webhook", json={"url": _VALID_WEBHOOK})
    with _patched_market():
        signed_in.put(
            f"/api/watchlist/{_WHIP_ID}/alert",
            json={"target_price": 1_400_000, "direction": "below"},
        )

        response = signed_in.delete(f"/api/watchlist/{_WHIP_ID}/alert")
        assert response.status_code == 200
        page = signed_in.get("/watchlist").text

    assert "Abyssal whip" in page
    assert 'value="1400000"' not in page


# -- /internal/check-alerts -------------------------------------------------------------------


@pytest.fixture
def alerting_client(settings: Settings) -> TestClient:
    armed_settings = dataclasses.replace(settings, internal_task_token="test-internal-token")
    with TestClient(create_app(armed_settings), base_url="https://testserver") as test_client:
        yield test_client


def _sign_in_and_arm(client: TestClient, *, direction: str, target_price: int) -> None:
    client.post("/auth/dev-login", json={"discord_user_id": "111", "username": "Wolklaw"})
    client.put("/api/discord-webhook", json={"url": _VALID_WEBHOOK})
    with _patched_market():
        response = client.put(
            f"/api/watchlist/{_WHIP_ID}/alert",
            json={"target_price": target_price, "direction": direction},
        )
        assert response.status_code == 200


def test_check_alerts_is_missing_without_a_configured_token(client: TestClient):
    assert client.post("/internal/check-alerts").status_code == 404


def test_check_alerts_refuses_the_wrong_token(alerting_client: TestClient):
    response = alerting_client.post("/internal/check-alerts", headers={"X-Internal-Token": "wrong"})

    assert response.status_code == 401


def test_check_alerts_fires_a_triggered_alert(alerting_client: TestClient):
    _sign_in_and_arm(alerting_client, direction="above", target_price=1_000_000)

    with (
        _patched_market(sell_price=1_600_000, buy_price=1_500_000),
        patch("toolkit_web.app.send_webhook_message", return_value=True) as sender,
    ):
        response = alerting_client.post(
            "/internal/check-alerts", headers={"X-Internal-Token": "test-internal-token"}
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "checked": 1, "sent": 1}
    sender.assert_called_once()
    assert sender.call_args[0][0] == _VALID_WEBHOOK
    assert "Abyssal whip" in sender.call_args[0][1]


def test_check_alerts_does_not_fire_before_the_target_is_reached(alerting_client: TestClient):
    _sign_in_and_arm(alerting_client, direction="above", target_price=10_000_000)

    with (
        _patched_market(sell_price=1_600_000, buy_price=1_500_000),
        patch("toolkit_web.app.send_webhook_message", return_value=True) as sender,
    ):
        response = alerting_client.post(
            "/internal/check-alerts", headers={"X-Internal-Token": "test-internal-token"}
        )

    assert response.json() == {"ok": True, "checked": 1, "sent": 0}
    sender.assert_not_called()


def test_check_alerts_respects_the_cooldown_after_firing(alerting_client: TestClient):
    _sign_in_and_arm(alerting_client, direction="above", target_price=1_000_000)

    with (
        _patched_market(sell_price=1_600_000, buy_price=1_500_000),
        patch("toolkit_web.app.send_webhook_message", return_value=True) as sender,
    ):
        first = alerting_client.post(
            "/internal/check-alerts", headers={"X-Internal-Token": "test-internal-token"}
        )
        second = alerting_client.post(
            "/internal/check-alerts", headers={"X-Internal-Token": "test-internal-token"}
        )

    assert first.json()["sent"] == 1
    assert second.json()["sent"] == 0
    sender.assert_called_once()


def test_check_alerts_does_not_touch_the_market_when_nothing_is_armed(
    alerting_client: TestClient,
):
    """The scheduler runs on a longer interval than the market cache's TTL, so a tick that
    reached for prices would find the cache stale and pay for a real fetch every time —
    to then discover there was nothing to check."""
    with patch("toolkit_web.market_cache.WikiMarketClient.fetch_snapshot") as fetch:
        response = alerting_client.post(
            "/internal/check-alerts", headers={"X-Internal-Token": "test-internal-token"}
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "checked": 0, "sent": 0}
    fetch.assert_not_called()
