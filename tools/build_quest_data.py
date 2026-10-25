from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ENDPOINT = "https://oldschool.runescape.wiki/api.php"
USER_AGENT = "OSRSToolkit quest data builder - https://github.com/Wolklaw/OSRS-Toolkit"
PAGE_SIZE = 500
TARGET = Path(__file__).resolve().parent.parent / "src" / "osrs_toolkit" / "quest_data.py"

TRAINABLE_SKILLS = frozenset(
    {
        "Agility",
        "Attack",
        "Construction",
        "Cooking",
        "Crafting",
        "Defence",
        "Farming",
        "Firemaking",
        "Fishing",
        "Fletching",
        "Herblore",
        "Hitpoints",
        "Hunter",
        "Magic",
        "Mining",
        "Prayer",
        "Ranged",
        "Runecraft",
        "Sailing",
        "Slayer",
        "Smithing",
        "Strength",
        "Thieving",
        "Woodcutting",
    }
)
SKILL_SPAN = re.compile(r'data-skill="([^"]+)"\s+data-level="(\d+)"')
LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
DIRECT_LINE = re.compile(r"^\*\*(?!\*)\s*(.*)$")
SINGLE_LINE = re.compile(r"^\*(?!\*)\s*(?:Completion of|Completed)\b(.*)$", re.IGNORECASE)


def fetch_rows() -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        query = (
            "bucket('quest').select('page_name','official_difficulty','official_length',"
            f"'requirements','json').limit({PAGE_SIZE}).offset({offset}).run()"
        )
        url = f"{ENDPOINT}?" + urllib.parse.urlencode(
            {"action": "bucket", "format": "json", "query": query}
        )
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=60) as response:
            page = json.load(response)["bucket"]
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def fetch_wikitext(names: list[str]) -> dict[str, str]:
    pages: dict[str, str] = {}
    for start in range(0, len(names), 40):
        url = f"{ENDPOINT}?" + urllib.parse.urlencode(
            {
                "action": "query",
                "prop": "revisions",
                "rvslots": "main",
                "rvprop": "content",
                "titles": "|".join(names[start : start + 40]),
                "format": "json",
                "formatversion": "2",
            }
        )
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.load(response)
        for page in payload["query"]["pages"]:
            revisions = page.get("revisions") or []
            if revisions:
                pages[page["title"]] = revisions[0]["slots"]["main"]["content"]
    return pages


REWARD_BLOCK = re.compile(r"\{\{Quest rewards(.*?)\n\}\}", re.DOTALL)
QUEST_POINTS = re.compile(r"\|\s*qp\s*=\s*(\d+)")
EXPERIENCE_LINE = re.compile(r"\{\{SCP\|([A-Za-z]+)\|([\d,]+)[^}]*\}\}\s*\[\[experience\]\]")


def parse_rewards(wikitext: str) -> tuple[int, dict[str, int]]:
    block = REWARD_BLOCK.search(wikitext)
    if block is None:
        return 0, {}
    body = block.group(1)
    points = QUEST_POINTS.search(body)
    experience: dict[str, int] = {}
    for skill, amount in EXPERIENCE_LINE.findall(body):
        experience[skill] = experience.get(skill, 0) + int(amount.replace(",", ""))
    return (int(points.group(1)) if points else 0), dict(sorted(experience.items()))


def plain(text: str) -> str:
    if "<" in text:
        return ""
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"''+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_requirements(raw: str, known: set[str]) -> tuple[dict[str, int], int, list[str]]:
    skills: dict[str, int] = {}
    quest_points = 0
    for skill, level in SKILL_SPAN.findall(raw):
        if skill == "Quest points":
            quest_points = max(quest_points, int(level))
        elif skill in TRAINABLE_SKILLS:
            skills[skill] = max(skills.get(skill, 0), int(level))

    prerequisites: list[str] = []
    in_quest_list = False
    for line in raw.splitlines():
        if re.match(r"^\*(?!\*).*quests?\b.*:\s*$", line, re.IGNORECASE):
            in_quest_list = True
            continue
        if re.match(r"^\*(?!\*)", line):
            in_quest_list = False
        candidate = None
        if in_quest_list:
            direct = DIRECT_LINE.match(line)
            candidate = direct.group(1) if direct else None
        else:
            single = SINGLE_LINE.match(line)
            candidate = single.group(1) if single else None
        if candidate is None:
            continue
        for target in LINK.findall(candidate):
            name = target.strip().replace("_", " ")
            if name in known and name not in prerequisites:
                prerequisites.append(name)
    return skills, quest_points, prerequisites


def build() -> list[dict]:
    rows = [
        row
        for row in fetch_rows()
        if row.get("official_difficulty") and not row["page_name"].endswith("/Full guide")
    ]
    known = {row["page_name"] for row in rows}
    wikitext = fetch_wikitext(sorted(known))
    quests = []
    for row in rows:
        detail = json.loads(row.get("json") or "{}")
        skills, required_points, prerequisites = parse_requirements(
            row.get("requirements") or "", known
        )
        reward_points, experience = parse_rewards(wikitext.get(row["page_name"], ""))
        if row["page_name"].startswith("Recipe for Disaster/"):
            reward_points = 0
        quests.append(
            {
                "name": row["page_name"],
                "difficulty": row["official_difficulty"],
                "length": row.get("official_length") or "",
                "start": plain(detail.get("start") or ""),
                "skills": dict(sorted(skills.items())),
                "quest_points_required": required_points,
                "prerequisites": prerequisites,
                "quest_points_awarded": reward_points,
                "experience": experience,
            }
        )
    return sorted(quests, key=lambda quest: quest["name"])


def main() -> None:
    quests = build()
    body = json.dumps(quests, indent=1, ensure_ascii=False)
    TARGET.write_text(f"from __future__ import annotations\n\nQUESTS = {body}\n", encoding="utf-8")
    subprocess.run([sys.executable, "-m", "ruff", "format", str(TARGET)], check=True)
    print(f"{len(quests)} quests written to {TARGET}")


if __name__ == "__main__":
    main()
