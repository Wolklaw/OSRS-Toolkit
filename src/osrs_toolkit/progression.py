from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import floor

from osrs_toolkit.journal import SkillsPoint
from osrs_toolkit.quests import QuestGuide, counts_as_quest

MAX_LEVEL = 99
RATE_WINDOW_DAYS = 7
MINIMUM_RATE_SPAN = timedelta(hours=1)

SKILL_ORDER = (
    "Attack",
    "Hitpoints",
    "Mining",
    "Strength",
    "Agility",
    "Smithing",
    "Defence",
    "Herblore",
    "Fishing",
    "Ranged",
    "Thieving",
    "Cooking",
    "Prayer",
    "Crafting",
    "Firemaking",
    "Magic",
    "Fletching",
    "Woodcutting",
    "Runecraft",
    "Slayer",
    "Farming",
    "Construction",
    "Hunter",
)


def xp_for_level(level: int) -> int:
    level = max(1, min(level, 126))
    total = sum(floor(step + 300 * 2 ** (step / 7)) for step in range(1, level))
    return total // 4


XP_AT_MAX = xp_for_level(MAX_LEVEL)


@dataclass(frozen=True, slots=True)
class SkillProgress:
    skill: str
    level: int
    xp: int
    xp_to_next: int | None
    xp_to_max: int
    percent_to_max: float
    xp_per_day: float | None
    days_to_max: float | None


@dataclass(frozen=True, slots=True)
class LevelUnlock:
    skill: str
    current: int
    target: int
    xp_needed: int
    quests: tuple[str, ...]


def xp_per_day(history: Iterable[SkillsPoint], skill: str, now: datetime) -> float | None:
    cutoff = now - timedelta(days=RATE_WINDOW_DAYS)
    points = []
    for point in history:
        if skill not in point.xp:
            continue
        stamp = datetime.fromisoformat(point.captured_at)
        stamp = stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp
        if stamp >= cutoff:
            points.append((stamp, point.xp[skill]))
    if len(points) < 2:
        return None
    points.sort()
    (first_at, first_xp), (last_at, last_xp) = points[0], points[-1]
    span = last_at - first_at
    gained = last_xp - first_xp
    if span < MINIMUM_RATE_SPAN or gained <= 0:
        return None
    return gained / (span.total_seconds() / 86_400)


def skill_progress(
    levels: Mapping[str, int],
    xp: Mapping[str, int],
    history: Iterable[SkillsPoint],
    now: datetime,
) -> list[SkillProgress]:
    points = list(history)
    ordered = [skill for skill in SKILL_ORDER if skill in levels]
    ordered += sorted(skill for skill in levels if skill not in SKILL_ORDER)
    rows = []
    for skill in ordered:
        level = levels[skill]
        current_xp = xp.get(skill, xp_for_level(level))
        to_max = max(0, XP_AT_MAX - current_xp)
        rate = xp_per_day(points, skill, now)
        rows.append(
            SkillProgress(
                skill=skill,
                level=level,
                xp=current_xp,
                xp_to_next=(
                    max(0, xp_for_level(level + 1) - current_xp) if level < MAX_LEVEL else None
                ),
                xp_to_max=to_max,
                percent_to_max=min(100.0, current_xp / XP_AT_MAX * 100),
                xp_per_day=rate,
                days_to_max=(to_max / rate) if rate and to_max else None,
            )
        )
    return rows


def level_unlocks(
    guides: Iterable[QuestGuide],
    levels: Mapping[str, int],
    xp: Mapping[str, int],
) -> list[LevelUnlock]:
    grouped: dict[tuple[str, int], list[str]] = {}
    for guide in guides:
        if (
            guide.state != "Blocked"
            or not counts_as_quest(guide.quest)
            or not guide.only_levels_missing
            or len(guide.skill_gaps) != 1
        ):
            continue
        gap = guide.skill_gaps[0]
        grouped.setdefault((gap.skill, gap.need), []).append(guide.quest.name)
    unlocks = []
    for (skill, target), names in grouped.items():
        current = levels.get(skill, 1)
        current_xp = xp.get(skill, xp_for_level(current))
        unlocks.append(
            LevelUnlock(
                skill=skill,
                current=current,
                target=target,
                xp_needed=max(0, xp_for_level(target) - current_xp),
                quests=tuple(sorted(names)),
            )
        )
    unlocks.sort(key=lambda row: (-len(row.quests), row.xp_needed, row.skill))
    return unlocks
