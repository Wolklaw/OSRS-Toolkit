from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from osrs_toolkit.calculators import conservative_buy_price
from osrs_toolkit.journal import LoadoutSnapshot, NpcLootRecord
from osrs_toolkit.models import ItemMapping, MarketPoint

# Strip dose counts (e.g. "Prayer potion(4)") so any charge level satisfies a checklist entry.
_DOSE_SUFFIX = re.compile(r"\s*\(\d+\)\s*$")

# Imbued items (e.g. "Slayer helmet (i)") should satisfy a checklist asking for the base item.
_IMBUED_SUFFIX = re.compile(r"\s*\(i\)\s*$")


def _normalize_name(name: str) -> str:
    return _IMBUED_SUFFIX.sub("", _DOSE_SUFFIX.sub("", name)).casefold().strip()


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
    """Community loot-value baseline minus the live cost of supplies consumed.

    ``gross_gp_per_hour`` is the community estimate from ``PvmActivity``. ``net_gp_per_hour``
    subtracts supply cost priced from the live market snapshot.
    """

    gross_gp_per_hour: int
    supply_cost_hour: int
    net_gp_per_hour: int
    priced: bool
    price_age_seconds: int
    # This account's own rate from Loot Log history, set by the caller via dataclasses.replace
    # once it has loot events — not by estimate_gp_per_hour. None means no data, not zero.
    observed_gp_per_hour: int | None = None


def estimate_gp_per_hour(
    activity: PvmActivity,
    mappings: dict[int, ItemMapping],
    points: list[MarketPoint],
    *,
    now: int | None = None,
) -> GpEstimate:
    """Net the activity's gross loot value against its live supply cost.

    Falls back to the gross figure alone (``priced=False``) when a supply item has no current
    market snapshot, rather than hiding the row.
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


def observed_gp_per_hour(events: list[NpcLootRecord]) -> int | None:
    """This account's own gp/hr, from the loot it actually received for one NPC.

    Spans earliest to latest event, not value / kill count (a delivery isn't always one
    kill). ``None`` with fewer than two events or if they all landed at the same instant.
    """
    if len(events) < 2:
        return None
    timestamps = sorted(_as_instant(event.occurred_at) for event in events)
    elapsed_hours = (timestamps[-1] - timestamps[0]).total_seconds() / 3600
    if elapsed_hours <= 0:
        return None
    total_value = sum(event.total_value for event in events)
    return round(total_value / elapsed_hours)


def _as_instant(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


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
    """False when there is no loadout to compare against (unknown, not "missing everything")."""

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


# Modern BiS-ish melee weapons, shared across "strong melee weapon" entries so newer gear
# isn't flagged missing just because a boss's list predates it.
_META_MELEE_WEAPONS: tuple[str, ...] = (
    "Osmumten's fang",
    "Scythe of vitur",
    "Ghrazi rapier",
    "Soulreaper axe",
    "Blade of saeldor",
    "Abyssal tentacle",
    "Abyssal whip",
)


PVM_ACTIVITIES: tuple[PvmActivity, ...] = (
    PvmActivity(
        name="Vorkath",
        wiki_url="https://oldschool.runescape.wiki/w/Vorkath/Strategies",
        skill_requirements={"Hitpoints": 75, "Ranged": 70, "Prayer": 43, "Defence": 60},
        gear=(
            GearRequirement(
                "Anti-dragon breath protection",
                ("Anti-dragon shield", "Dragonfire shield", "Dragonfire ward"),
            ),
            GearRequirement(
                "Ranged weapon",
                ("Toxic blowpipe", "Armadyl crossbow", "Zaryte crossbow", "Twisted bow"),
            ),
            GearRequirement("Anti-venom", ("Anti-venom", "Anti-venom+")),
        ),
        gross_gp_per_hour=1_800_000,
        supplies=_prayer_and_food(4, _SARADOMIN_BREW_4, "Saradomin brew(4)", 6),
        notes="Requires Dragon Slayer II. Community GP/hr estimate for an efficient ranged kill rotation.",
    ),
    PvmActivity(
        name="Zulrah",
        wiki_url="https://oldschool.runescape.wiki/w/Zulrah/Strategies",
        skill_requirements={"Hitpoints": 75, "Ranged": 70, "Magic": 70, "Prayer": 43},
        gear=(
            GearRequirement(
                "Ranged weapon", ("Toxic blowpipe", "Armadyl crossbow", "Zaryte crossbow")
            ),
            GearRequirement(
                "Magic weapon",
                (
                    "Trident of the seas",
                    "Trident of the swamp",
                    "Sanguinesti staff",
                    "Tumeken's shadow",
                ),
            ),
            GearRequirement("Anti-venom", ("Anti-venom", "Anti-venom+")),
            GearRequirement(
                "Serpentine helm or equivalent poison immunity", ("Serpentine helm", "Anti-venom+")
            ),
        ),
        gross_gp_per_hour=1_600_000,
        supplies=_prayer_and_food(4, _SARADOMIN_BREW_4, "Saradomin brew(4)", 5),
        notes="Requires Regicide. Rotation-based fight with prayer and gear switches; GP/hr assumes a memorized rotation.",
    ),
    PvmActivity(
        name="General Graardor (Bandos)",
        wiki_url="https://oldschool.runescape.wiki/w/General_Graardor/Strategies",
        skill_requirements={"Hitpoints": 70, "Strength": 70, "Defence": 65, "Prayer": 43},
        gear=(
            GearRequirement("Strong melee weapon", ("Armadyl godsword",) + _META_MELEE_WEAPONS),
            GearRequirement("Protection prayer access", ("Prayer potion",)),
        ),
        gross_gp_per_hour=900_000,
        supplies=_prayer_and_food(3, _SHARK, "Shark", 10),
        notes="Access requires killing through Bandos minions. GP/hr varies a lot with team size and unique drop luck.",
    ),
    PvmActivity(
        name="Kree'arra (Armadyl)",
        wiki_url="https://oldschool.runescape.wiki/w/Kree%27arra/Strategies",
        skill_requirements={"Hitpoints": 70, "Ranged": 70, "Defence": 65, "Prayer": 43},
        gear=(
            GearRequirement(
                "Ranged weapon",
                ("Toxic blowpipe", "Armadyl crossbow", "Zaryte crossbow", "Twisted bow"),
            ),
            GearRequirement("Protection prayer access", ("Prayer potion",)),
        ),
        gross_gp_per_hour=900_000,
        supplies=_prayer_and_food(3, _SHARK, "Shark", 10),
        notes="Access requires killing through Armadyl minions. GP/hr varies a lot with team size and unique drop luck.",
    ),
    PvmActivity(
        name="Cerberus",
        wiki_url="https://oldschool.runescape.wiki/w/Cerberus/Strategies",
        skill_requirements={"Hitpoints": 75, "Slayer": 91, "Defence": 70, "Prayer": 43},
        gear=(
            GearRequirement("Strong melee weapon", _META_MELEE_WEAPONS),
            GearRequirement(
                "Antifire protection (lava pools)", ("Antifire potion", "Super antifire potion")
            ),
        ),
        gross_gp_per_hour=1_700_000,
        supplies=_prayer_and_food(4, _SARADOMIN_BREW_4, "Saradomin brew(4)", 5),
        notes="Requires 91 Slayer to damage at all, regardless of task. Its ghostly souls drain prayer or hitpoints on contact — a spectral spirit shield or Ward of Arceuus cuts that drain, though neither is required.",
    ),
    PvmActivity(
        name="King Black Dragon",
        wiki_url="https://oldschool.runescape.wiki/w/King_Black_Dragon/Strategies",
        skill_requirements={"Hitpoints": 65, "Prayer": 43},
        gear=(
            GearRequirement(
                "Anti-dragon breath protection",
                (
                    "Anti-dragon shield",
                    "Dragonfire shield",
                    "Dragonfire ward",
                    "Super antifire potion",
                ),
            ),
        ),
        gross_gp_per_hour=350_000,
        supplies=_prayer_and_food(2, _SHARK, "Shark", 6),
        notes="No formal requirements. A reliable entry point into dragon-tier PvM before Vorkath.",
    ),
    PvmActivity(
        name="Giant Mole",
        wiki_url="https://oldschool.runescape.wiki/w/Giant_Mole/Strategies",
        skill_requirements={"Hitpoints": 50},
        gear=(GearRequirement("Strong melee weapon", ("Dragon scimitar",) + _META_MELEE_WEAPONS),),
        gross_gp_per_hour=450_000,
        supplies=_prayer_and_food(1, _SHARK, "Shark", 4),
        notes="Low-requirement melee boss, good for practicing prayer flicking before harder content.",
    ),
    PvmActivity(
        name="Barrows",
        wiki_url="https://oldschool.runescape.wiki/w/Barrows/Strategies",
        skill_requirements={"Hitpoints": 50, "Prayer": 43},
        gear=(
            GearRequirement("Strong melee weapon", ("Dragon scimitar",) + _META_MELEE_WEAPONS),
            GearRequirement("Protection prayer access", ("Prayer potion",)),
        ),
        gross_gp_per_hour=650_000,
        supplies=_prayer_and_food(3, _SHARK, "Shark", 8),
        notes="Requires Priest in Peril. GP/hr assumes an efficient chest-to-chest rotation, not counting reward-set flips.",
    ),
    PvmActivity(
        name="Kalphite Queen",
        wiki_url="https://oldschool.runescape.wiki/w/Kalphite_Queen/Strategies",
        skill_requirements={"Hitpoints": 50},
        gear=(
            GearRequirement(
                "Ranged or Magic weapon",
                (
                    "Toxic blowpipe",
                    "Trident of the seas",
                    "Zaryte crossbow",
                    "Twisted bow",
                    "Tumeken's shadow",
                ),
            ),
        ),
        gross_gp_per_hour=400_000,
        supplies=_prayer_and_food(1, _SHARK, "Shark", 6),
        notes="No formal requirements; the Kalphite Lair is free-to-play accessible. Her ranged form has high melee defence, so ranged or magic is the accepted approach.",
    ),
    PvmActivity(
        name="Dagannoth Kings",
        wiki_url="https://oldschool.runescape.wiki/w/Dagannoth_Kings/Strategies",
        skill_requirements={"Hitpoints": 70, "Prayer": 43},
        gear=(
            GearRequirement("Melee weapon", _META_MELEE_WEAPONS),
            GearRequirement(
                "Ranged weapon", ("Toxic blowpipe", "Armadyl crossbow", "Zaryte crossbow")
            ),
            GearRequirement("Protection prayer access", ("Prayer potion",)),
        ),
        gross_gp_per_hour=700_000,
        supplies=_prayer_and_food(3, _SHARK, "Shark", 9),
        notes="Rex, Prime, and Supreme each favor a different combat style, so an efficient trip switches gear between them. GP/hr varies with unique drop luck.",
    ),
    PvmActivity(
        name="Corporeal Beast",
        wiki_url="https://oldschool.runescape.wiki/w/Corporeal_Beast/Strategies",
        skill_requirements={"Hitpoints": 75, "Strength": 70, "Prayer": 43},
        gear=(
            GearRequirement("Strong melee weapon", _META_MELEE_WEAPONS),
            GearRequirement(
                "Spirit shield or equivalent",
                ("Spectral spirit shield", "Elysian spirit shield", "Arcane spirit shield"),
            ),
        ),
        gross_gp_per_hour=1_200_000,
        supplies=_prayer_and_food(4, _SARADOMIN_BREW_4, "Saradomin brew(4)", 5),
        notes="Usually fought in a group to bypass its high defence; solo trips are slow without a spirit shield. GP/hr swings enormously with unique drop luck.",
    ),
    PvmActivity(
        name="Thermonuclear Smoke Devil",
        wiki_url="https://oldschool.runescape.wiki/w/Thermonuclear_smoke_devil/Strategies",
        skill_requirements={"Slayer": 93, "Hitpoints": 70},
        gear=(
            GearRequirement(
                "Ranged or Magic weapon",
                ("Toxic blowpipe", "Trident of the seas", "Armadyl crossbow", "Tumeken's shadow"),
            ),
            GearRequirement("Face mask or equivalent", ("Slayer helmet", "Facemask")),
        ),
        gross_gp_per_hour=500_000,
        supplies=_prayer_and_food(2, _SHARK, "Shark", 8),
        notes="Requires 93 Slayer; only damageable during a Smoke Devil Slayer task or with a Ring of visibility.",
    ),
    PvmActivity(
        name="Kraken",
        wiki_url="https://oldschool.runescape.wiki/w/Kraken/Strategies",
        skill_requirements={"Slayer": 87, "Magic": 68},
        gear=(
            GearRequirement(
                "Magic weapon",
                (
                    "Trident of the seas",
                    "Trident of the swamp",
                    "Sanguinesti staff",
                    "Tumeken's shadow",
                ),
            ),
        ),
        gross_gp_per_hour=600_000,
        supplies=_prayer_and_food(2, _SHARK, "Shark", 8),
        notes="Requires 87 Slayer; melee is ineffective against its main form.",
    ),
    PvmActivity(
        name="Alchemical Hydra",
        wiki_url="https://oldschool.runescape.wiki/w/Alchemical_Hydra/Strategies",
        skill_requirements={"Slayer": 95, "Hitpoints": 75, "Defence": 70},
        gear=(
            GearRequirement("Strong melee weapon", ("Dragon hunter lance",) + _META_MELEE_WEAPONS),
            GearRequirement(
                "Ranged weapon", ("Dragon hunter crossbow", "Armadyl crossbow", "Zaryte crossbow")
            ),
        ),
        gross_gp_per_hour=1_900_000,
        supplies=_prayer_and_food(4, _SARADOMIN_BREW_4, "Saradomin brew(4)", 6),
        notes="Requires 95 Slayer. High-intensity gear-switching fight; GP/hr assumes a memorized rotation.",
    ),
    PvmActivity(
        name="Grotesque Guardians",
        wiki_url="https://oldschool.runescape.wiki/w/Grotesque_Guardians/Strategies",
        skill_requirements={"Slayer": 75, "Hitpoints": 70, "Prayer": 43},
        gear=(
            GearRequirement("Strong melee weapon", _META_MELEE_WEAPONS),
            GearRequirement("Protection prayer access", ("Prayer potion",)),
        ),
        gross_gp_per_hour=1_100_000,
        supplies=_prayer_and_food(3, _SHARK, "Shark", 8),
        notes="Requires 75 Slayer. Only fought at night in-game, or by carrying a dusk-forcing item during the day.",
    ),
    PvmActivity(
        name="Sarachnis",
        wiki_url="https://oldschool.runescape.wiki/w/Sarachnis/Strategies",
        skill_requirements={"Hitpoints": 50},
        gear=(
            GearRequirement(
                "Strong melee weapon", ("Rune scimitar", "Dragon scimitar") + _META_MELEE_WEAPONS
            ),
        ),
        gross_gp_per_hour=350_000,
        supplies=_prayer_and_food(1, _SHARK, "Shark", 5),
        notes="No formal requirements. A reliable early boss for the Sarachnis cudgel and clue scrolls.",
    ),
    PvmActivity(
        name="Vet'ion",
        wiki_url="https://oldschool.runescape.wiki/w/Vet%27ion/Strategies",
        skill_requirements={"Hitpoints": 70, "Prayer": 43},
        gear=(
            GearRequirement("Strong melee weapon", _META_MELEE_WEAPONS),
            GearRequirement("Protection prayer access", ("Prayer potion",)),
        ),
        gross_gp_per_hour=450_000,
        supplies=_prayer_and_food(2, _SHARK, "Shark", 7),
        notes="Wilderness boss (levels 30-32) — other players can attack you here. Requires Priest in Peril for prayer access nearby.",
    ),
    PvmActivity(
        name="Callisto",
        wiki_url="https://oldschool.runescape.wiki/w/Callisto/Strategies",
        skill_requirements={"Hitpoints": 70, "Prayer": 43},
        gear=(
            GearRequirement("Strong melee weapon", _META_MELEE_WEAPONS),
            GearRequirement("Protection prayer access", ("Prayer potion",)),
        ),
        gross_gp_per_hour=450_000,
        supplies=_prayer_and_food(2, _SHARK, "Shark", 7),
        notes="Wilderness boss (level 470) — other players can attack you here. High defence; melee is the accepted approach.",
    ),
    PvmActivity(
        name="Venenatis",
        wiki_url="https://oldschool.runescape.wiki/w/Venenatis/Strategies",
        skill_requirements={"Hitpoints": 70, "Ranged": 70, "Prayer": 43},
        gear=(
            GearRequirement(
                "Ranged or melee weapon",
                ("Toxic blowpipe", "Armadyl crossbow", "Zaryte crossbow", "Twisted bow")
                + _META_MELEE_WEAPONS,
            ),
            GearRequirement("Anti-venom", ("Anti-venom", "Anti-venom+")),
        ),
        gross_gp_per_hour=450_000,
        supplies=_prayer_and_food(2, _SHARK, "Shark", 7),
        notes="Wilderness boss (level 464) — other players can attack you here. Applies venom; anti-venom is strongly recommended (plain anti-poison does not cure or block venom).",
    ),
    PvmActivity(
        name="Chaos Elemental",
        wiki_url="https://oldschool.runescape.wiki/w/Chaos_Elemental/Strategies",
        skill_requirements={"Hitpoints": 60},
        gear=(
            GearRequirement(
                "Strong melee or ranged weapon",
                ("Toxic blowpipe", "Rune crossbow", "Zaryte crossbow") + _META_MELEE_WEAPONS,
            ),
        ),
        gross_gp_per_hour=300_000,
        supplies=_prayer_and_food(1, _SHARK, "Shark", 6),
        notes="Wilderness boss (level 305) — other players can attack you here. Randomly teleports players around its arena.",
    ),
    PvmActivity(
        name="Duke Sucellus",
        wiki_url="https://oldschool.runescape.wiki/w/Duke_Sucellus/Strategies",
        skill_requirements={"Hitpoints": 75, "Defence": 90, "Prayer": 70},
        gear=(
            GearRequirement("Strong melee weapon", _META_MELEE_WEAPONS),
            GearRequirement("Face mask or equivalent", ("Slayer helmet", "Facemask")),
        ),
        gross_gp_per_hour=8_300_000,
        supplies=_prayer_and_food(4, _SARADOMIN_BREW_4, "Saradomin brew(4)", 6),
        notes="Requires Desert Treasure II. Face protection roughly halves the poisonous gas vent damage during the fight; Protect from Melee cuts the icicle and slam damage.",
    ),
    PvmActivity(
        name="Vardorvis",
        wiki_url="https://oldschool.runescape.wiki/w/Vardorvis/Strategies",
        skill_requirements={"Hitpoints": 75, "Defence": 90, "Prayer": 70},
        gear=(
            GearRequirement("Strong melee weapon", _META_MELEE_WEAPONS),
            GearRequirement("Protection prayer access", ("Prayer potion",)),
        ),
        gross_gp_per_hour=7_800_000,
        supplies=_prayer_and_food(4, _SARADOMIN_BREW_4, "Saradomin brew(4)", 6),
        notes="Requires Desert Treasure II. Protect from Melee is the default prayer; switch to Protect from Missiles for the Head Gaze special attack.",
    ),
    PvmActivity(
        name="The Leviathan",
        wiki_url="https://oldschool.runescape.wiki/w/The_Leviathan/Strategies",
        skill_requirements={"Hitpoints": 75, "Ranged": 90, "Defence": 70, "Prayer": 74},
        gear=(
            GearRequirement(
                "Ranged weapon",
                ("Twisted bow", "Zaryte crossbow", "Armadyl crossbow", "Toxic blowpipe"),
            ),
            GearRequirement("Protection prayer access", ("Prayer potion",)),
        ),
        gross_gp_per_hour=6_300_000,
        supplies=_prayer_and_food(4, _SARADOMIN_BREW_4, "Saradomin brew(4)", 6),
        notes="Requires Desert Treasure II. Melee cannot reach the Leviathan — ranged only. Also requires Shadow spellbook access to stun it between phases.",
    ),
    PvmActivity(
        name="The Whisperer",
        wiki_url="https://oldschool.runescape.wiki/w/The_Whisperer/Strategies",
        skill_requirements={"Hitpoints": 75, "Magic": 90, "Prayer": 77},
        gear=(
            GearRequirement(
                "Magic weapon", ("Sanguinesti staff", "Trident of the swamp", "Tumeken's shadow")
            ),
            GearRequirement("Blackstone fragment", ("Blackstone fragment",)),
        ),
        gross_gp_per_hour=6_000_000,
        supplies=_prayer_and_food(4, _SARADOMIN_BREW_4, "Saradomin brew(4)", 6),
        notes="Requires Desert Treasure II. The Blackstone fragment is mandatory to start the fight. Her sanity meter drains in the Shadow Realm — running it out is fatal.",
    ),
    PvmActivity(
        name="Phantom Muspah",
        wiki_url="https://oldschool.runescape.wiki/w/Phantom_Muspah/Strategies",
        skill_requirements={"Hitpoints": 75, "Ranged": 90, "Defence": 80, "Prayer": 74},
        gear=(
            GearRequirement(
                "Ranged weapon",
                ("Twisted bow", "Zaryte crossbow", "Armadyl crossbow", "Toxic blowpipe"),
            ),
            GearRequirement("Protection prayer access", ("Prayer potion",)),
        ),
        gross_gp_per_hour=5_900_000,
        supplies=_prayer_and_food(4, _SARADOMIN_BREW_4, "Saradomin brew(4)", 6),
        notes="Requires Secrets of the North. Melee is not recommended; the boss punishes it in its enraged form. Protect from Magic blocks its corruption effect outright.",
    ),
    PvmActivity(
        name="Nex",
        wiki_url="https://oldschool.runescape.wiki/w/Nex/Strategies",
        skill_requirements={"Hitpoints": 70, "Ranged": 90, "Defence": 90, "Prayer": 74},
        gear=(
            GearRequirement(
                "Ranged weapon",
                ("Twisted bow", "Zaryte crossbow", "Armadyl crossbow", "Toxic blowpipe"),
            ),
            GearRequirement(
                "Magic weapon", ("Sanguinesti staff", "Trident of the swamp", "Tumeken's shadow")
            ),
            GearRequirement("Face mask or equivalent", ("Slayer helmet", "Facemask", "Gas mask")),
        ),
        gross_gp_per_hour=6_500_000,
        supplies=_prayer_and_food(4, _SARADOMIN_BREW_4, "Saradomin brew(4)", 6),
        notes="Effectively requires a team or duo — 3,400 hitpoints and five Ancient Magicks phases make solo unrealistic. GP/hr shown is a team rate; duo splits run notably higher.",
    ),
    PvmActivity(
        name="Scurrius",
        wiki_url="https://oldschool.runescape.wiki/w/Scurrius/Strategies",
        skill_requirements={"Hitpoints": 60, "Prayer": 43},
        gear=(GearRequirement("Protection prayer access", ("Prayer potion",)),),
        gross_gp_per_hour=190_000,
        supplies=_prayer_and_food(1, _SHARK, "Shark", 5),
        notes="No formal requirements beyond 60 combat. Attacks rotate between melee, ranged, and magic, so quick prayer switching matters more than any one weapon.",
    ),
    PvmActivity(
        name="Blue Moon",
        wiki_url="https://oldschool.runescape.wiki/w/Moons_of_Peril/Strategies",
        skill_requirements={"Hitpoints": 75},
        gear=(
            GearRequirement(
                "Crush weapon",
                (
                    "Scythe of vitur",
                    "Dual macuahuitl",
                    "Glacial temotli",
                    "Inquisitor's mace",
                    "Soulreaper axe",
                    "Zamorakian hasta",
                    "Abyssal bludgeon",
                    "Ursine chainmace",
                    "Elder maul",
                    "Dragon warhammer",
                    "Bone mace",
                ),
            ),
        ),
        gross_gp_per_hour=1_750_000,
        supplies=(SupplyItem(_SHARK, "Shark", 5),),
        notes="Requires the Perilous Moons quest. Weak to crush. Protection prayers don't block her attacks, so gear and food carry the fight, not prayer flicking.",
    ),
    PvmActivity(
        name="Blood Moon",
        wiki_url="https://oldschool.runescape.wiki/w/Moons_of_Peril/Strategies",
        skill_requirements={"Hitpoints": 75},
        gear=(
            GearRequirement(
                "Slash weapon",
                (
                    "Scythe of vitur",
                    "Soulreaper axe",
                    "Noxious halberd",
                    "Blade of saeldor",
                    "Ghrazi rapier",
                    "Abyssal tentacle",
                    "Abyssal whip",
                    "Zamorakian hasta",
                    "Sulphur blades",
                    "Dragon scimitar",
                    "Zombie axe",
                    "Arkan blade",
                ),
            ),
        ),
        gross_gp_per_hour=1_750_000,
        supplies=(SupplyItem(_SHARK, "Shark", 5),),
        notes="Requires the Perilous Moons quest. Weak to slash. Protection prayers don't block her attacks, so gear and food carry the fight, not prayer flicking.",
    ),
    PvmActivity(
        name="Eclipse Moon",
        wiki_url="https://oldschool.runescape.wiki/w/Moons_of_Peril/Strategies",
        skill_requirements={"Hitpoints": 75},
        gear=(
            GearRequirement(
                "Stab weapon",
                (
                    "Soulreaper axe",
                    "Ghrazi rapier",
                    "Scythe of vitur",
                    "Blade of saeldor",
                    "Noxious halberd",
                    "Voidwaker",
                    "Osmumten's fang",
                    "Belle's folly",
                    "Zamorakian hasta",
                    "Abyssal dagger",
                    "Dragon dagger",
                ),
            ),
        ),
        gross_gp_per_hour=1_750_000,
        supplies=(SupplyItem(_SHARK, "Shark", 5),),
        notes="Requires the Perilous Moons quest. Weak to stab. Protection prayers don't block her attacks, so gear and food carry the fight, not prayer flicking.",
    ),
)


def assess_readiness(activity: PvmActivity, snapshot: LoadoutSnapshot | None) -> ActivityReadiness:
    """Compare a loadout snapshot against one activity's checklist.

    Gear is matched by item name (case-insensitive, dose suffix ignored) across equipment,
    inventory, and bank. Without a snapshot, returns unassessed rather than reporting every
    requirement missing.
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
        f"{requirement.label} ({', '.join(requirement.any_of)})"
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
