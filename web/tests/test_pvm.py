from __future__ import annotations

import html
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from osrs_toolkit.journal import JournalRepository, LoadoutSnapshot
from osrs_toolkit.pvm import PVM_ACTIVITIES

from toolkit_web.config import Settings


def test_the_pvm_page_prompts_for_a_snapshot_when_there_is_none(signed_in: TestClient):
    response = signed_in.get("/pvm")

    assert response.status_code == 200
    assert "No snapshot yet" in response.text


def test_an_empty_loadout_is_not_ready_for_a_single_activity(
    signed_in: TestClient, settings: Settings
):
    """Level 1 in everything and nothing owned cannot clear a real boss checklist — every
    row has to read "Not ready", never "Ready", so an empty snapshot never claims readiness
    it has no basis for."""
    repository = JournalRepository(settings.journal_path("111"))
    repository.save_loadout_snapshot(_empty_snapshot())

    response = signed_in.get("/pvm")

    assert response.status_code == 200
    assert response.text.count("Not ready") == len(PVM_ACTIVITIES)
    # A ready cell renders exactly >Ready</td> — distinct from the >Ready</th> column
    # header and from >Not ready</td>, so this cannot collide with either.
    assert ">Ready</td>" not in response.text


def test_the_pvm_page_lists_every_activity_by_name(signed_in: TestClient, settings: Settings):
    repository = JournalRepository(settings.journal_path("111"))
    repository.save_loadout_snapshot(_empty_snapshot())

    response = signed_in.get("/pvm")
    unescaped = html.unescape(response.text)

    for activity in PVM_ACTIVITIES:
        assert activity.name in unescaped


def test_the_pvm_page_needs_a_session(client: TestClient):
    assert client.get("/pvm").status_code == 401


def _empty_snapshot() -> LoadoutSnapshot:
    return LoadoutSnapshot(
        account_hash="abc123",
        account_name="Wolklaw",
        captured_at=datetime.now(UTC).isoformat(),
        equipment=(),
        inventory=(),
        bank=(),
        skills={},
    )
