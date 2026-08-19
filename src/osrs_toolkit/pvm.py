from __future__ import annotations

import re
import time
from dataclasses import dataclass

from osrs_toolkit.calculators import conservative_buy_price
from osrs_toolkit.journal import LoadoutSnapshot
from osrs_toolkit.models import ItemMapping, MarketPoint

# Potions and some other consumables carry a trailing dose count in their real in-game name
# (e.g. "Prayer potion(4)", "Anti-venom+(2)"). Checklist requirements are written without a
# dose so any charge level satisfies them; strip it before comparing names.
_DOSE_SUFFIX = re.compile(r"\s*\(\d+\)\s*$")


def _normalize_name(name: str) -> str:
    return _DOSE_SUFFIX.sub("", name).casefold().strip()


@dataclass(frozen=True, slots=True)
class GearRequirement:
    """One requirement slot, satisfied by owning any one of a set of interchangeable items."""

    label: str
    any_of: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SupplyItem:
    """One consumable spent over an hour of the activity (e.g. prayer potions, food)."""

    item_id: int
    name: str
    quantity_per_hour: float


@dataclass(frozen=True, slots=True)
class PvmActivity:
    name: str
    wiki_url: str
    skill_requirements: dict[str, int]
    gear: tuple[GearRequirement, ...]
    gross_gp_per_hour: int
    supplies: tuple[SupplyItem, ...]
    notes: str


@dataclass(frozen=True, slots=True)
class GpEstimate:
    """Community loot-value baseline minus the live cost of what the trip actually consumes.

    ``gross_gp_per_hour`` is the community figure ``PvmActivity.gross_gp_per_hour`` already
    carries — a settled estimate of the loot itself, which this module has no better way to
    price than the community already has. ``net_gp_per_hour`` subtracts supply cost priced
    from the live market snapshot, so the number moves with real prices the same way every
    other calculator in this app does, without pretending to simulate a full drop table.
    """

    gross_gp_per_hour: int
    supply_cost_hour: int
    net_gp_per_hour: int
    priced: bool
    price_age_seconds: int


def estimate_gp_per_hour(
    activity: PvmActivity,
    mappings: dict[int, ItemMapping],
    points: list[MarketPoint],
    *,
    now: int | None = None,
) -> GpEstimate:
    """Net the activity's gross loot value against its live supply cost.

    Falls back to the gross figure alone (``priced=False``) when a supply item has no current
    market snapshot — a stale-but-present estimate beats hiding the row's numbers entirely
    because one potion was briefly missing a price.
    """
    del mappings  # Supplies are looked up by item_id; mappings only carry display names.
    point_by_id = {point.item_id: point for point in points}
    current_time = int(time.time()) if now is None else now
    supply_cost = 0.0
    ages: list[int] = []
    priced = True
    for supply in activity.supplies:
        point = point_by_id.get(supply.item_id)
        if point is None:
            priced = False
            continue
        supply_cost += conservative_buy_price(point) * supply.quantity_per_hour
        ages.append(current_time - point.high_time)
    rounded_cost = round(supply_cost)
    return GpEstimate(
        gross_gp_per_hour=activity.gross_gp_per_hour,
        supply_cost_hour=rounded_cost if priced else 0,
        net_gp_per_hour=(
            activity.gross_gp_per_hour - rounded_cost if priced else activity.gross_gp_per_hour
        ),
        priced=priced and bool(activity.supplies),
        price_age_seconds=max(ages, default=0),
    )


@dataclass(frozen=True, slots=True)
class MissingSkill:
    skill: str
    required_level: int
    current_level: int


@dataclass(frozen=True, slots=True)
class ActivityReadiness:
    activity: PvmActivity
    missing_skills: tuple[MissingSkill, ...]
    missing_gear: tuple[str, ...]
    assessed: bool = True
    """False when there is no loadout to compare against, which is not the same answer as
    "you are missing everything": nothing is known about what this account owns."""

    @property
    def is_ready(self) -> bool:
        return self.assessed and not self.missing_skills and not self.missing_gear


# Item IDs referenced by the supply lists below, kept together so a wrong ID is easy to audit.
_PRAYER_POTION_4 = 2434
_SHARK = 385
_SARADOMIN_BREW_4 = 6685


def _prayer_and_food(
    prayer_per_hour: float, food_item_id: int, food_name: str, food_per_hour: float
) -> tuple[SupplyItem, ...]:
    return (
        SupplyItem(_PRAYER_POTION_4, "Prayer potion(4)", prayer_per_hour),
        SupplyItem(food_item_id, food_name, food_per_hour),
    )


PVM_ACTIVITIES: tuple[PvmActivity, ...] = (
    PvmActivity(
        name="Vorkath",
        wiki_url="https://oldschool.runescape.wiki/w/Vorkath",
        skill_requirements={"Hitpoints": 75, "Ranged": 70, "Prayer": 43, "Defence": 60},
        gear=(
            GearRequirement("Anti-dragon breath protection", ("Anti-dragon shield", "Dragonfire shield", "Dragonfire ward")),
            GearRequirement("Ranged weapon", ("Toxic blowpipe", "Armadyl crossbow", "Zaryte crossbow", "Twisted bow")),
            GearRequirement("Anti-venom", ("Anti-venom", "Anti-venom+")),
        ),
        gross_gp_per_hour=1_800_000,
        supplies=_prayer_and_food(4, _SARADOMIN_BREW_4, "Saradomin brew(4)", 6),
        notes="Requires Dragon Slayer II. Community GP/hr estimate for an efficient ranged kill rotation.",
    ),
    PvmActivity(
        name="Zulrah",
        wiki_url="https://oldschool.runescape.wiki/w/Zulrah",
        skill_requirements={"Hitpoints": 75, "Ranged": 70, "Magic": 70, "Prayer": 43},
        gear=(
            GearRequirement("Ranged weapon", ("Toxic blowpipe", "Armadyl crossbow", "Zaryte crossbow")),
            GearRequirement("Magic weapon", ("Trident of the seas", "Trident of the swamp", "Sanguinesti staff")),
            GearRequirement("Anti-venom", ("Anti-venom", "Anti-venom+")),
            GearRequirement("Serpentine helm or equivalent poison immunity", ("Serpentine helm", "Anti-venom+")),
        ),
        gross_gp_per_hour=1_600_000,
        supplies=_prayer_and_food(4, _SARADOMIN_BREW_4, "Saradomin brew(4)", 5),
        notes="Requires Regicide. Rotation-based fight with prayer and gear switches; GP/hr assumes a memorized rotation.",
    ),
    PvmActivity(
        name="General Graardor (Bandos)",
        wiki_url="https://oldschool.runescape.wiki/w/General_Graardor",
        skill_requirements={"Hitpoints": 70, "Strength": 70, "Defence": 65, "Prayer": 43},
        gear=(
            GearRequirement("Strong melee weapon", ("Armadyl godsword", "Ghrazi rapier", "Abyssal whip", "Abyssal tentacle")),
            GearRequirement("Protection prayer access", ("Prayer potion",)),
        ),
        gross_gp_per_hour=900_000,
        supplies=_prayer_and_food(3, _SHARK, "Shark", 10),
        notes="Access requires killing through Bandos minions. GP/hr varies a lot with team size and unique drop luck.",
    ),
    PvmActivity(
        name="Kree'arra (Armadyl)",
        wiki_url="https://oldschool.runescape.wiki/w/Kree%27arra",
        skill_requirements={"Hitpoints": 70, "Ranged": 70, "Defence": 65, "Prayer": 43},
        gear=(
            GearRequirement("Ranged weapon", ("Toxic blowpipe", "Armadyl crossbow", "Zaryte crossbow", "Twisted bow")),
            GearRequirement("Protection prayer access", ("Prayer potion",)),
        ),
        gross_gp_per_hour=900_000,
        supplies=_prayer_and_food(3, _SHARK, "Shark", 10),
        notes="Access requires killing through Armadyl minions. GP/hr varies a lot with team size and unique drop luck.",
    ),
    PvmActivity(
        name="Cerberus",
        wiki_url="https://oldschool.runescape.wiki/w/Cerberus",
        skill_requirements={"Hitpoints": 75, "Slayer": 91, "Defence": 70, "Prayer": 43},
        gear=(
            GearRequirement("Strong melee weapon", ("Abyssal tentacle", "Ghrazi rapier", "Abyssal whip", "Scythe of vitur")),
            GearRequirement("Souls/spirits protection", ("Antifire potion", "Super antifire potion")),
        ),
        gross_gp_per_hour=1_700_000,
        supplies=_prayer_and_food(4, _SARADOMIN_BREW_4, "Saradomin brew(4)", 5),
        notes="Requires 91 Slayer to damage at all, regardless of task. Prayer flicking between attack styles is expected for efficient kills.",
    ),
    PvmActivity(
        name="King Black Dragon",
        wiki_url="https://oldschool.runescape.wiki/w/King_Black_Dragon",
        skill_requirements={"Hitpoints": 65, "Prayer": 43},
        gear=(
            GearRequirement("Anti-dragon breath protection", ("Anti-dragon shield", "Dragonfire shield", "Dragonfire ward", "Super antifire potion")),
        ),
        gross_gp_per_hour=350_000,
        supplies=_prayer_and_food(2, _SHARK, "Shark", 6),
        notes="No formal requirements. A reliable entry point into dragon-tier PvM before Vorkath.",
    ),
    PvmActivity(
        name="Giant Mole",
        wiki_url="https://oldschool.runescape.wiki/w/Giant_Mole",
        skill_requirements={"Hitpoints": 50},
        gear=(
            GearRequirement("Strong melee weapon", ("Abyssal whip", "Abyssal tentacle", "Dragon scimitar")),
        ),
        gross_gp_per_hour=450_000,
        supplies=_prayer_and_food(1, _SHARK, "Shark", 4),
        notes="Low-requirement melee boss, good for practicing prayer flicking before harder content.",
    ),
    PvmActivity(
        name="Barrows",
        wiki_url="https://oldschool.runescape.wiki/w/Barrows",
        skill_requirements={"Hitpoints": 50, "Prayer": 43},
        gear=(
            GearRequirement("Strong melee weapon", ("Abyssal whip", "Abyssal tentacle", "Dragon scimitar", "Ghrazi rapier")),
            GearRequirement("Protection prayer access", ("Prayer potion",)),
        ),
        gross_gp_per_hour=650_000,
        supplies=_prayer_and_food(3, _SHARK, "Shark", 8),
        notes="Requires Priest in Peril. GP/hr assumes an efficient chest-to-chest rotation, not counting reward-set flips.",
    ),
    PvmActivity(
        name="Kalphite Queen",
        wiki_url="https://oldschool.runescape.wiki/w/Kalphite_Queen",
        skill_requirements={"Hitpoints": 50},
        gear=(
            GearRequirement("Ranged or Magic weapon", ("Toxic blowpipe", "Trident of the seas", "Zaryte crossbow")),
        ),
        gross_gp_per_hour=400_000,
        supplies=_prayer_and_food(1, _SHARK, "Shark", 6),
        notes="No formal requirements; the Kalphite Lair is free-to-play accessible. Her ranged form has high melee defence, so ranged or magic is the accepted approach.",
    ),
    PvmActivity(
        name="Dagannoth Kings",
        wiki_url="https://oldschool.runescape.wiki/w/Dagannoth_Kings",
        skill_requirements={"Hitpoints": 70, "Prayer": 43},
        gear=(
            GearRequirement("Melee weapon", ("Abyssal whip", "Abyssal tentacle", "Ghrazi rapier")),
            GearRequirement("Ranged weapon", ("Toxic blowpipe", "Armadyl crossbow")),
            GearRequirement("Protection prayer access", ("Prayer potion",)),
        ),
        gross_gp_per_hour=700_000,
        supplies=_prayer_and_food(3, _SHARK, "Shark", 9),
        notes="Rex, Prime, and Supreme each favor a different combat style, so an efficient trip switches gear between them. GP/hr varies with unique drop luck.",
    ),
    PvmActivity(
        name="Corporeal Beast",
        wiki_url="https://oldschool.runescape.wiki/w/Corporeal_Beast",
        skill_requirements={"Hitpoints": 75, "Strength": 70, "Prayer": 43},
        gear=(
            GearRequirement("Strong melee weapon", ("Ghrazi rapier", "Abyssal tentacle", "Scythe of vitur")),
            GearRequirement("Spirit shield or equivalent", ("Spectral spirit shield", "Elysian spirit shield", "Arcane spirit shield")),
        ),
        gross_gp_per_hour=1_200_000,
        supplies=_prayer_and_food(4, _SARADOMIN_BREW_4, "Saradomin brew(4)", 5),
        notes="Usually fought in a group to bypass its high defence; solo trips are slow without a spirit shield. GP/hr swings enormously with unique drop luck.",
    ),
    PvmActivity(
        name="Thermonuclear Smoke Devil",
        wiki_url="https://oldschool.runescape.wiki/w/Thermonuclear_smoke_devil",
        skill_requirements={"Slayer": 93, "Hitpoints": 70},
        gear=(
            GearRequirement("Ranged or Magic weapon", ("Toxic blowpipe", "Trident of the seas", "Armadyl crossbow")),
            GearRequirement("Face mask or equivalent", ("Slayer helmet", "Facemask")),
        ),
        gross_gp_per_hour=500_000,
        supplies=_prayer_and_food(2, _SHARK, "Shark", 8),
        notes="Requires 93 Slayer; only damageable during a Smoke Devil Slayer task or with a Ring of visibility.",
    ),
    PvmActivity(
        name="Kraken",
        wiki_url="https://oldschool.runescape.wiki/w/Kraken",
        skill_requirements={"Slayer": 87, "Magic": 68},
        gear=(
            GearRequirement("Magic weapon", ("Trident of the seas", "Trident of the swamp", "Sanguinesti staff")),
        ),
        gross_gp_per_hour=600_000,
        supplies=_prayer_and_food(2, _SHARK, "Shark", 8),
        notes="Requires 87 Slayer; melee is ineffective against its main form.",
    ),
    PvmActivity(
        name="Alchemical Hydra",
        wiki_url="https://oldschool.runescape.wiki/w/Alchemical_Hydra",
        skill_requirements={"Slayer": 95, "Hitpoints": 75, "Defence": 70},
        gear=(
            GearRequirement("Strong melee weapon", ("Dragon hunter lance", "Scythe of vitur", "Abyssal tentacle")),
            GearRequirement("Ranged weapon", ("Dragon hunter crossbow", "Armadyl crossbow")),
        ),
        gross_gp_per_hour=1_900_000,
        supplies=_prayer_and_food(4, _SARADOMIN_BREW_4, "Saradomin brew(4)", 6),
        notes="Requires 95 Slayer. High-intensity gear-switching fight; GP/hr assumes a memorized rotation.",
    ),
    PvmActivity(
        name="Grotesque Guardians",
        wiki_url="https://oldschool.runescape.wiki/w/Grotesque_Guardians",
        skill_requirements={"Slayer": 75, "Hitpoints": 70, "Prayer": 43},
        gear=(
            GearRequirement("Strong melee weapon", ("Ghrazi rapier", "Abyssal tentacle", "Blade of saeldor")),
            GearRequirement("Protection prayer access", ("Prayer potion",)),
        ),
        gross_gp_per_hour=1_100_000,
        supplies=_prayer_and_food(3, _SHARK, "Shark", 8),
        notes="Requires 75 Slayer. Only fought at night in-game, or by carrying a dusk-forcing item during the day.",
    ),
    PvmActivity(
        name="Sarachnis",
        wiki_url="https://oldschool.runescape.wiki/w/Sarachnis",
        skill_requirements={"Hitpoints": 50},
        gear=(
            GearRequirement("Strong melee weapon", ("Abyssal whip", "Rune scimitar", "Dragon scimitar")),
        ),
        gross_gp_per_hour=350_000,
        supplies=_prayer_and_food(1, _SHARK, "Shark", 5),
        notes="No formal requirements. A reliable early boss for the Sarachnis cudgel and clue scrolls.",
    ),
    PvmActivity(
        name="Vet'ion",
        wiki_url="https://oldschool.runescape.wiki/w/Vet%27ion",
        skill_requirements={"Hitpoints": 70, "Prayer": 43},
        gear=(
            GearRequirement("Strong melee weapon", ("Abyssal whip", "Abyssal tentacle", "Ghrazi rapier")),
            GearRequirement("Protection prayer access", ("Prayer potion",)),
        ),
        gross_gp_per_hour=450_000,
        supplies=_prayer_and_food(2, _SHARK, "Shark", 7),
        notes="Wilderness boss (levels 30-32) — other players can attack you here. Requires Priest in Peril for prayer access nearby.",
    ),
    PvmActivity(
        name="Callisto",
        wiki_url="https://oldschool.runescape.wiki/w/Callisto",
        skill_requirements={"Hitpoints": 70, "Prayer": 43},
        gear=(
            GearRequirement("Strong melee weapon", ("Abyssal whip", "Abyssal tentacle", "Ghrazi rapier")),
            GearRequirement("Protection prayer access", ("Prayer potion",)),
        ),
        gross_gp_per_hour=450_000,
        supplies=_prayer_and_food(2, _SHARK, "Shark", 7),
        notes="Wilderness boss (level 470) — other players can attack you here. High defence; melee is the accepted approach.",
    ),
    PvmActivity(
        name="Venenatis",
        wiki_url="https://oldschool.runescape.wiki/w/Venenatis",
        skill_requirements={"Hitpoints": 70, "Ranged": 70, "Prayer": 43},
        gear=(
            GearRequirement("Ranged or melee weapon", ("Toxic blowpipe", "Abyssal whip", "Armadyl crossbow")),
            GearRequirement("Anti-venom", ("Anti-venom", "Anti-venom+")),
        ),
        gross_gp_per_hour=450_000,
        supplies=_prayer_and_food(2, _SHARK, "Shark", 7),
        notes="Wilderness boss (level 464) — other players can attack you here. Applies venom; anti-venom is strongly recommended.",
    ),
    PvmActivity(
        name="Chaos Elemental",
        wiki_url="https://oldschool.runescape.wiki/w/Chaos_Elemental",
        skill_requirements={"Hitpoints": 60},
        gear=(
            GearRequirement("Strong melee or ranged weapon", ("Abyssal whip", "Toxic blowpipe", "Rune crossbow")),
        ),
        gross_gp_per_hour=300_000,
        supplies=_prayer_and_food(1, _SHARK, "Shark", 6),
        notes="Wilderness boss (level 305) — other players can attack you here. Randomly teleports players around its arena.",
    ),
)


def assess_readiness(
    activity: PvmActivity, snapshot: LoadoutSnapshot | None
) -> ActivityReadiness:
    """Compare a loadout snapshot against one activity's checklist.

    Gear is matched by item name (case-insensitive, dose suffix ignored) across equipment,
    inventory, and bank — everything a bank trip away counts as "owned" for planning purposes.

    Without a snapshot there is no comparison to make. Treating that as an account with
    nothing in the bank and level 1 in everything would report every requirement as missing,
    which reads as a verdict on the account rather than on what is known about it.
    """
    if snapshot is None:
        return ActivityReadiness(activity, (), (), assessed=False)
    owned_names = frozenset(_owned_names(snapshot))
    skills = snapshot.skills
    missing_skills = tuple(
        MissingSkill(skill, required, skills.get(skill, 1))
        for skill, required in activity.skill_requirements.items()
        if skills.get(skill, 1) < required
    )
    missing_gear = tuple(
        requirement.label
        for requirement in activity.gear
        if not any(_normalize_name(item_name) in owned_names for item_name in requirement.any_of)
    )
    return ActivityReadiness(activity, missing_skills, missing_gear)


def assess_all(snapshot: LoadoutSnapshot | None) -> list[ActivityReadiness]:
    return [assess_readiness(activity, snapshot) for activity in PVM_ACTIVITIES]


def _owned_names(snapshot: LoadoutSnapshot) -> set[str]:
    return {
        _normalize_name(item.item_name)
        for item in (*snapshot.equipment, *snapshot.inventory, *snapshot.bank)
    }
