from __future__ import annotations

from datetime import UTC, datetime, timedelta

from osrs_toolkit.journal import SkillsPoint
from osrs_toolkit.progression import (
    SKILL_ORDER,
    XP_AT_MAX,
    level_unlocks,
    skill_progress,
    xp_for_level,
    xp_per_day,
)
from osrs_toolkit.quests import all_quests, assess_quests, counts_as_quest

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _point(days_ago: float, xp: int, skill: str = "Slayer") -> SkillsPoint:
    return SkillsPoint(
        account_hash="h",
        account_name="n",
        captured_at=(NOW - timedelta(days=days_ago)).isoformat(),
        skills={skill: 70},
        xp={skill: xp},
    )


def test_the_xp_table_matches_the_games_known_thresholds() -> None:
    assert xp_for_level(1) == 0
    assert xp_for_level(2) == 83
    assert xp_for_level(50) == 101_333
    assert xp_for_level(92) == 6_517_253
    assert XP_AT_MAX == 13_034_431


def test_pace_is_the_xp_gained_per_day_across_the_last_week() -> None:
    assert xp_per_day([_point(4, 1_000_000), _point(0, 1_040_000)], "Slayer", NOW) == 10_000


def test_pace_ignores_old_points_and_needs_real_progress() -> None:
    assert xp_per_day([_point(20, 1), _point(0, 5_000)], "Slayer", NOW) is None
    assert xp_per_day([_point(3, 500), _point(0, 500)], "Slayer", NOW) is None
    assert xp_per_day([_point(0, 500)], "Slayer", NOW) is None


def test_progress_reports_xp_to_the_next_level_and_to_99_with_an_eta() -> None:
    rows = skill_progress(
        {"Slayer": 70},
        {"Slayer": 750_000},
        [_point(2, 730_000), _point(0, 750_000)],
        NOW,
    )

    row = rows[0]
    assert row.xp_to_next == xp_for_level(71) - 750_000
    assert row.xp_to_max == XP_AT_MAX - 750_000
    assert row.xp_per_day == 10_000
    assert round(row.days_to_max) == round((XP_AT_MAX - 750_000) / 10_000)


def test_a_maxed_skill_has_nothing_to_go() -> None:
    row = skill_progress({"Slayer": 99}, {"Slayer": XP_AT_MAX + 5}, [], NOW)[0]

    assert (row.xp_to_next, row.xp_to_max, row.days_to_max) == (None, 0, None)


def test_unlocks_list_quests_waiting_on_a_single_skill_level() -> None:
    quest = next(
        quest
        for quest in all_quests()
        if len(quest.skills) == 1
        and quest.quest_points_required == 0
        and counts_as_quest(quest)
        and max(quest.skills.values()) > 1
    )
    skill, need = next(iter(quest.skills.items()))
    levels = {name: 99 for name in SKILL_ORDER}
    levels[skill] = need - 1
    completed = {other.name for other in all_quests() if other.name != quest.name}

    unlocks = level_unlocks(assess_quests(levels, completed), levels, {})

    assert len(unlocks) == 1
    assert (unlocks[0].skill, unlocks[0].target) == (skill, need)
    assert unlocks[0].quests == (quest.name,)
    assert unlocks[0].xp_needed == xp_for_level(need) - xp_for_level(need - 1)
