from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from osrs_toolkit.journal import JournalRepository

from toolkit_web.config import Settings


def test_the_landing_page_shows_a_sign_in_link_when_signed_out(client: TestClient):
    response = client.get("/")

    assert response.status_code == 200
    assert "Sign in with Discord" in response.text
    assert "/auth/discord/login" in response.text


def test_the_root_page_shows_the_dashboard_once_signed_in(signed_in: TestClient):
    response = signed_in.get("/")

    assert response.status_code == 200
    assert "Sign in with Discord" not in response.text
    assert "Wolklaw" in response.text


def test_an_unpaired_account_is_offered_the_connect_flow(signed_in: TestClient):
    response = signed_in.get("/")

    assert "Not set up" in response.text
    assert "Get a token" in response.text


def test_a_paired_account_is_not_asked_to_connect_again(signed_in: TestClient):
    from unittest.mock import patch

    with patch("toolkit_web.app.probe_token", return_value=True):
        signed_in.put("/api/pairing-token", json={"token": "good-token"})

    response = signed_in.get("/")

    assert "Get a token" not in response.text


def test_an_empty_journal_shows_the_empty_state(signed_in: TestClient):
    response = signed_in.get("/")

    assert "Nothing tracked yet" in response.text
    # No stray Jinja template errors leaking into the page from a None/empty computation.
    assert "Undefined" not in response.text


def test_a_tracked_position_appears_on_the_dashboard_with_its_numbers(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    repository.track(4151, "Abyssal whip", 2, target_buy=1_000_000, target_sell=1_200_000)

    response = signed_in.get("/")

    assert "Abyssal whip" in response.text
    # 2 * 1,000,000 target buy, no fills recorded yet — the planned outlay is what should show.
    assert "2,000,000" in response.text


def test_net_worth_shows_the_most_recent_reading_not_an_older_one(
    signed_in: TestClient, settings: Settings
):
    """The headline stat is the latest reading. An older one is allowed to reappear as a
    history-chart axis label now — that is the chart doing its job — so this checks the
    specific stat tile rather than the page as a whole."""
    repository = JournalRepository(settings.journal_path("111"))
    for value in (1_000_000, 5_000_000):
        repository.save_loadout_snapshot(_bare_snapshot(total_value=value))

    response = signed_in.get("/")

    assert '<div class="value">5,000,000 gp</div>' in response.text
    assert '<div class="value">1,000,000 gp</div>' not in response.text


def test_the_performance_page_renders_with_no_history_yet(signed_in: TestClient):
    response = signed_in.get("/performance")

    assert response.status_code == 200
    assert "Undefined" not in response.text
    assert "Nothing realized in this period yet" in response.text


def test_the_performance_page_shows_a_completed_positions_realized_profit(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    position_id = repository.track(
        4151, "Abyssal whip", 1, target_buy=1_000_000, target_sell=1_200_000
    )
    repository.update_tracked(position_id, "Completed", 1_000_000, 1_200_000)

    response = signed_in.get("/performance")

    assert response.status_code == 200
    assert "Undefined" not in response.text
    # By-strategy groups even a single position (minimum_positions=1); the default
    # strategy label from track() is what should show up there.
    assert "Balanced (1" in response.text


def test_the_performance_page_groups_repeat_items_once_there_are_two(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    for _ in range(2):
        position_id = repository.track(
            4151, "Abyssal whip", 1, target_buy=1_000_000, target_sell=1_200_000
        )
        repository.update_tracked(position_id, "Completed", 1_000_000, 1_200_000)

    response = signed_in.get("/performance")

    assert "Abyssal whip" in response.text


def test_pvm_shows_supplies_spend_grouped_by_item(signed_in: TestClient, settings: Settings):
    repository = JournalRepository(settings.journal_path("111"))
    position_id = repository.track(
        1755, "Prayer potion(4)", 10, target_buy=10_000, target_sell=10_000
    )
    repository.update_tracked(position_id, "Supplies", 10_000, None, buy_fills=[(10, 10_000)])

    response = signed_in.get("/pvm")

    assert response.status_code == 200
    assert "Undefined" not in response.text
    assert "Prayer potion(4)" in response.text
    assert "100,000" in response.text  # 10 * 10,000 spent


def test_the_item_history_page_degrades_cleanly_when_the_wiki_is_unreachable(
    signed_in: TestClient,
):
    from unittest.mock import patch

    from osrs_toolkit.market import MarketDataError

    with patch(
        "toolkit_web.market_cache.WikiMarketClient.fetch_timeseries",
        side_effect=MarketDataError("offline"),
    ):
        response = signed_in.get("/item/4151")

    assert response.status_code == 200
    assert "No price history available" in response.text


def test_the_item_history_page_renders_a_chart_when_prices_are_available(
    signed_in: TestClient,
):
    from unittest.mock import patch

    from osrs_toolkit.models import TimeseriesPoint

    points = [
        TimeseriesPoint(timestamp=1_000, average_high=100, average_low=90),
        TimeseriesPoint(timestamp=2_000, average_high=110, average_low=95),
    ]
    with patch("toolkit_web.market_cache.WikiMarketClient.fetch_timeseries", return_value=points):
        response = signed_in.get("/item/4151")

    assert response.status_code == 200
    assert "<svg" in response.text
    assert "No price history available" not in response.text


def test_csv_export_includes_tracked_and_manual_trades(signed_in: TestClient, settings: Settings):
    repository = JournalRepository(settings.journal_path("111"))
    repository.track(4151, "Abyssal whip", 1, target_buy=1_000_000, target_sell=1_100_000)
    repository.add("Dragon bones", quantity=10, buy_price=2_000, sell_price=2_200)

    response = signed_in.get("/api/export.csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "Abyssal whip" in response.text
    assert "Dragon bones" in response.text


def _bare_snapshot(total_value: int):
    from osrs_toolkit.journal import LoadoutItem, LoadoutSnapshot

    return LoadoutSnapshot(
        account_hash="abc123",
        account_name="Wolklaw",
        captured_at=datetime.now(UTC).isoformat(),
        equipment=(
            LoadoutItem(item_id=995, item_name="Coins", quantity=total_value, unit_value=1),
        ),
        inventory=(),
        bank=(),
        skills={},
    )


def test_the_wordmark_is_one_flex_item_so_it_does_not_read_rune_scope(signed_in: TestClient):
    """`.brand` is a flex row with a gap for the icon. Left bare, "Rune" and <em>Scope</em>
    are two anonymous flex items and that gap opens between them, so the logo renders as
    "Rune Scope". Wrapping the wordmark is what keeps it one item, and one word."""
    page = signed_in.get("/").text

    assert '<span class="wordmark">Rune<em>Scope</em></span>' in page
    assert '<span class="mark">RS</span>Rune' not in page


def test_the_signed_out_header_wordmark_is_wrapped_too(client: TestClient):
    """There are two copies of the brand — sidebar and signed-out topbar — and fixing one is
    the easy way to leave the other reading "Rune Scope"."""
    page = client.get("/about").text

    assert '<span class="wordmark">Rune<em>Scope</em></span>' in page
    assert '<span class="mark">RS</span>Rune' not in page
