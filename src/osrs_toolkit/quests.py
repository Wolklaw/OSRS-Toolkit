from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache

from osrs_toolkit.quest_data import QUESTS

DIFFICULTY_ORDER = (
    "Novice",
    "Intermediate",
    "Experienced",
    "Master",
    "Grandmaster",
    "Special",
)
NEARLY_READY_LEVELS = 10
LENGTH_ORDER = ("Very Short", "Short", "Medium", "Long", "Very Long")
MINIQUESTS = frozenset(
    {
        "Alfred Grimhand's Barcrawl",
        "Architectural Alliance",
        "Barbarian Training",
        "Bear Your Soul",
        "Curse of the Empty Lord",
        "Daddy's Home",
        "Enter the Abyss",
        "Family Pest",
        "His Faithful Servants",
        "Hopespear's Will",
        "In Search of Knowledge",
        "Into the Tombs",
        "Lair of Tarn Razorlor",
        "Mage Arena I",
        "Mage Arena II",
        "Skippy and the Mogres",
        "The Enchanted Key",
        "The Fractured Archive (miniquest)",
        "The Frozen Door",
        "The General's Shadow",
        "Vale Totems (miniquest)",
    }
)


@dataclass(frozen=True, slots=True)
class Quest:
    name: str
    difficulty: str
    length: str
    start: str
    skills: Mapping[str, int]
    quest_points_required: int
    prerequisites: tuple[str, ...]
    quest_points_awarded: int
    experience: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class SkillGap:
    skill: str
    have: int
    need: int

    @property
    def short(self) -> int:
        return self.need - self.have


@dataclass(frozen=True, slots=True)
class QuestGuide:
    quest: Quest
    state: str
    skill_gaps: tuple[SkillGap, ...]
    missing_quests: tuple[str, ...]
    quest_point_gap: int
    unlocks: int

    @property
    def levels_short(self) -> int:
        return sum(gap.short for gap in self.skill_gaps)

    @property
    def only_levels_missing(self) -> bool:
        return not self.missing_quests and self.quest_point_gap == 0 and bool(self.skill_gaps)


@lru_cache(maxsize=1)
def all_quests() -> tuple[Quest, ...]:
    return tuple(
        Quest(
            name=entry["name"],
            difficulty=entry["difficulty"],
            length=entry["length"],
            start=entry["start"],
            skills=dict(entry["skills"]),
            quest_points_required=entry["quest_points_required"],
            prerequisites=tuple(entry["prerequisites"]),
            quest_points_awarded=entry["quest_points_awarded"],
            experience=dict(entry["experience"]),
        )
        for entry in QUESTS
    )


@lru_cache(maxsize=1)
def _by_name() -> dict[str, Quest]:
    return {quest.name: quest for quest in all_quests()}


@lru_cache(maxsize=1)
def _dependants() -> dict[str, tuple[str, ...]]:
    result: dict[str, list[str]] = {}
    for quest in all_quests():
        for prerequisite in quest.prerequisites:
            result.setdefault(prerequisite, []).append(quest.name)
    return {name: tuple(children) for name, children in result.items()}


def with_prerequisites(names: Iterable[str]) -> frozenset[str]:
    by_name = _by_name()
    seen: set[str] = set()
    stack = [name for name in names if name in by_name]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        stack.extend(by_name[name].prerequisites)
    return frozenset(seen)


def without_dependants(names: Iterable[str]) -> frozenset[str]:
    dependants = _dependants()
    seen: set[str] = set()
    stack = list(names)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        stack.extend(dependants.get(name, ()))
    return frozenset(seen)


def is_miniquest(quest: Quest) -> bool:
    return quest.name in MINIQUESTS


def counts_as_quest(quest: Quest) -> bool:
    return not is_miniquest(quest) and "/" not in quest.name


def counted_quest_names() -> frozenset[str]:
    return frozenset(quest.name for quest in all_quests() if counts_as_quest(quest))


def quest_points(completed: Iterable[str]) -> int:
    by_name = _by_name()
    return sum(by_name[name].quest_points_awarded for name in set(completed) if name in by_name)


def _unlock_counts(completed: frozenset[str]) -> dict[str, int]:
    return {
        quest.name: len(without_dependants([quest.name]) - {quest.name} - completed)
        for quest in all_quests()
    }


def assess_quests(levels: Mapping[str, int], completed: Iterable[str]) -> list[QuestGuide]:
    done = frozenset(completed)
    points = quest_points(done)
    unlocks = _unlock_counts(done)
    guides: list[QuestGuide] = []
    for quest in all_quests():
        if quest.name in done:
            guides.append(QuestGuide(quest, "Done", (), (), 0, 0))
            continue
        gaps = tuple(
            SkillGap(skill, levels.get(skill, 1), need)
            for skill, need in sorted(quest.skills.items())
            if levels.get(skill, 1) < need
        )
        missing = tuple(name for name in quest.prerequisites if name not in done)
        point_gap = max(0, quest.quest_points_required - points)
        ready = not gaps and not missing and point_gap == 0
        guides.append(
            QuestGuide(
                quest,
                "Ready" if ready else "Blocked",
                gaps,
                missing,
                point_gap,
                unlocks[quest.name],
            )
        )
    return guides


def _difficulty_rank(guide: QuestGuide) -> int:
    difficulty = guide.quest.difficulty
    return (
        DIFFICULTY_ORDER.index(difficulty)
        if difficulty in DIFFICULTY_ORDER
        else len(DIFFICULTY_ORDER)
    )


def _length_rank(guide: QuestGuide) -> int:
    length = guide.quest.length
    return LENGTH_ORDER.index(length) if length in LENGTH_ORDER else len(LENGTH_ORDER)


def do_next(guides: Iterable[QuestGuide], limit: int = 10) -> list[QuestGuide]:
    ready = [guide for guide in guides if guide.state == "Ready" and counts_as_quest(guide.quest)]
    ready.sort(
        key=lambda guide: (
            -guide.unlocks,
            _length_rank(guide),
            -guide.quest.quest_points_awarded,
            _difficulty_rank(guide),
            guide.quest.name,
        )
    )
    return ready[:limit]


def ready_miniquests(guides: Iterable[QuestGuide]) -> list[QuestGuide]:
    ready = [guide for guide in guides if guide.state == "Ready" and is_miniquest(guide.quest)]
    ready.sort(key=lambda guide: (_length_rank(guide), _difficulty_rank(guide), guide.quest.name))
    return ready


def nearly_ready(
    guides: Iterable[QuestGuide], limit: int = 10, max_levels: int = NEARLY_READY_LEVELS
) -> list[QuestGuide]:
    close = [
        guide
        for guide in guides
        if guide.state == "Blocked"
        and guide.only_levels_missing
        and guide.levels_short <= max_levels
    ]
    close.sort(key=lambda guide: (guide.levels_short, -guide.unlocks, guide.quest.name))
    return close[:limit]


def best_for_skill(guides: Iterable[QuestGuide], skill: str, limit: int = 5) -> list[QuestGuide]:
    rewarding = [
        guide
        for guide in guides
        if guide.state == "Ready" and guide.quest.experience.get(skill, 0) > 0
    ]
    rewarding.sort(key=lambda guide: (-guide.quest.experience[skill], guide.quest.name))
    return rewarding[:limit]
