from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from osrs_toolkit import __version__

HISCORE_SKILLS = [
    "Overall", "Attack", "Defence", "Strength", "Hitpoints", "Ranged", "Prayer",
    "Magic", "Cooking", "Woodcutting", "Fletching", "Fishing", "Firemaking",
    "Crafting", "Smithing", "Mining", "Herblore", "Agility", "Thieving",
    "Slayer", "Farming", "Runecraft", "Hunter", "Construction", "Sailing",
]


class AccountLookupError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PlayerProfile:
    name: str
    skills: dict[str, int]

    @property
    def total_level(self) -> int:
        return self.skills.get("Overall", 0)


def fetch_player(name: str, timeout: float = 10.0) -> PlayerProfile:
    clean_name = name.strip()
    if not clean_name:
        raise AccountLookupError("Enter a character name.")
    encoded = urllib.parse.quote_plus(clean_name)
    url = f"https://secure.runescape.com/m=hiscore_oldschool/index_lite.ws?player={encoded}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"OSRSToolkit/{__version__}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            lines = response.read().decode("utf-8").splitlines()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise AccountLookupError("Character was not found on the OSRS hiscores.") from exc
        raise AccountLookupError("The OSRS hiscores are unavailable right now.") from exc
    except OSError as exc:
        raise AccountLookupError("Could not connect to the OSRS hiscores.") from exc

    skills: dict[str, int] = {}
    for skill, line in zip(HISCORE_SKILLS, lines, strict=False):
        fields = line.split(",")
        if len(fields) < 2:
            continue
        try:
            level = int(fields[1])
        except ValueError:
            continue
        skills[skill] = level if skill == "Overall" else max(1, level)
    if "Overall" not in skills:
        raise AccountLookupError("The hiscores returned an unexpected response.")
    return PlayerProfile(clean_name, skills)
