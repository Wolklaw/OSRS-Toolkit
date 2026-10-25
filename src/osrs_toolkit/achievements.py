from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from osrs_toolkit.journal import LoadoutItem, ProgressRecord

DIARY_TIERS = ("Easy", "Medium", "Hard", "Elite")
COMBAT_TIER_POINTS = (
    ("Grandmaster", 2_630),
    ("Master", 1_841),
    ("Elite", 1_064),
    ("Hard", 416),
    ("Medium", 161),
    ("Easy", 41),
)
CONTAINER_LABELS = {
    "looting_bag": "Looting bag",
    "seed_vault": "Seed vault",
    "seed_box": "Seed box",
    "tackle_box": "Tackle box",
}


@dataclass(frozen=True, slots=True)
class DiaryRow:
    region: str
    tiers_complete: int
    label: str


@dataclass(frozen=True, slots=True)
class SlayerSummary:
    task: str | None
    remaining: int
    points: int
    tasks_completed: int


@dataclass(frozen=True, slots=True)
class ContainerRow:
    label: str
    stacks: int
    value: int


@dataclass(frozen=True, slots=True)
class ProgressSummary:
    captured_at: str
    quest_points: int
    quests_finished: int
    quests_in_progress: int
    diary_tiers_complete: int
    diary_tiers_total: int
    diaries: tuple[DiaryRow, ...]
    combat_points: int
    combat_tier: str | None
    slayer: SlayerSummary


def combat_tier_for(points: int) -> str | None:
    for name, threshold in COMBAT_TIER_POINTS:
        if points >= threshold:
            return name
    return None


def summarise_progress(record: ProgressRecord) -> ProgressSummary:
    diaries = tuple(
        DiaryRow(
            region=region,
            tiers_complete=tiers,
            label=DIARY_TIERS[tiers - 1] if tiers > 0 else "None",
        )
        for region, tiers in sorted(record.diaries.items())
    )
    task = record.slayer.get("task_name")
    return ProgressSummary(
        captured_at=record.captured_at,
        quest_points=record.quest_points,
        quests_finished=sum(1 for state in record.quests.values() if state == "finished"),
        quests_in_progress=sum(1 for state in record.quests.values() if state == "in_progress"),
        diary_tiers_complete=sum(row.tiers_complete for row in diaries),
        diary_tiers_total=len(diaries) * len(DIARY_TIERS),
        diaries=diaries,
        combat_points=record.combat_points,
        combat_tier=combat_tier_for(record.combat_points),
        slayer=SlayerSummary(
            task=task if isinstance(task, str) else None,
            remaining=int(record.slayer.get("remaining") or 0),
            points=int(record.slayer.get("points") or 0),
            tasks_completed=int(record.slayer.get("tasks_completed") or 0),
        ),
    )


def container_rows(containers: Mapping[str, tuple[LoadoutItem, ...]]) -> list[ContainerRow]:
    return [
        ContainerRow(
            label=CONTAINER_LABELS.get(name, name.replace("_", " ").title()),
            stacks=len(items),
            value=sum(item.total_value for item in items),
        )
        for name, items in sorted(containers.items())
    ]
