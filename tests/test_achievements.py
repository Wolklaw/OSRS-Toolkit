from __future__ import annotations

import pytest

from osrs_toolkit.achievements import combat_tier_for, summarise_progress
from osrs_toolkit.journal import ProgressRecord


@pytest.mark.parametrize(
    ("points", "tier"),
    [
        (0, None),
        (40, None),
        (41, "Easy"),
        (415, "Medium"),
        (416, "Hard"),
        (1_064, "Elite"),
        (1_841, "Master"),
        (2_630, "Grandmaster"),
    ],
)
def test_combat_tier_follows_the_point_thresholds(points: int, tier: str | None) -> None:
    assert combat_tier_for(points) == tier


def test_summary_counts_quests_diary_tiers_and_the_task() -> None:
    record = ProgressRecord(
        account_hash="h",
        account_name="n",
        captured_at="2026-10-25T10:00:00+00:00",
        quest_points=10,
        quests={"A": "finished", "B": "finished", "C": "in_progress", "D": "not_started"},
        diaries={"Varrock": 4, "Ardougne": 1, "Desert": 0},
        combat_points=0,
        combat_tiers={},
        slayer={"task_name": None, "remaining": 0, "points": 5, "tasks_completed": 2},
    )

    summary = summarise_progress(record)

    assert (summary.quests_finished, summary.quests_in_progress) == (2, 1)
    assert (summary.diary_tiers_complete, summary.diary_tiers_total) == (5, 12)
    assert [row.label for row in summary.diaries] == ["Easy", "None", "Elite"]
    assert summary.slayer.task is None
