from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from osrs_toolkit.quests import (
    DIFFICULTY_ORDER,
    all_quests,
    assess_quests,
    best_for_skill,
    do_next,
    nearly_ready,
    quest_points,
    with_prerequisites,
    without_dependants,
)

from toolkit_web.accounts_db import Account
from toolkit_web.deps import Deps
from toolkit_web.importing import journal_for

QUEST_NAMES = frozenset(quest.name for quest in all_quests())


def build(deps: Deps) -> APIRouter:
    router = APIRouter()
    settings = deps.settings
    templates = deps.templates
    CurrentAccount = deps.CurrentAccount
    ActiveCharacter = deps.ActiveCharacter

    def resolve(account: Account, active: str | None):
        repository = journal_for(settings, account)
        filter_hash = deps.character_switcher_context(account, active)["active_character"]
        snapshot = (
            repository.get_loadout_snapshot(filter_hash)
            if filter_hash
            else repository.get_latest_loadout_snapshot()
        )
        return repository, (snapshot.account_hash if snapshot else filter_hash), snapshot

    @router.get("/quests", response_class=HTMLResponse)
    def quests_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        switcher = deps.character_switcher_context(account, active)
        repository, account_hash, snapshot = resolve(account, active)
        completed = repository.list_completed_quests(account_hash) if account_hash else frozenset()
        levels = snapshot.skills if snapshot else {}
        guides = assess_quests(levels, completed)
        ordered = sorted(
            guides,
            key=lambda guide: (
                DIFFICULTY_ORDER.index(guide.quest.difficulty)
                if guide.quest.difficulty in DIFFICULTY_ORDER
                else len(DIFFICULTY_ORDER),
                guide.quest.name,
            ),
        )
        skill_picks = {
            skill: best_for_skill(guides, skill, 3)
            for skill in sorted({s for guide in guides for s in guide.quest.experience})
        }
        return templates.TemplateResponse(
            request,
            "quests.html",
            {
                "account": account,
                "has_character": account_hash is not None,
                "has_levels": bool(levels),
                "guides": ordered,
                "next_up": do_next(guides),
                "almost": nearly_ready(guides),
                "skill_picks": {skill: rows for skill, rows in skill_picks.items() if rows},
                "done_count": len(completed & QUEST_NAMES),
                "total_count": len(QUEST_NAMES),
                "ready_count": sum(1 for guide in guides if guide.state == "Ready"),
                "quest_points": quest_points(completed),
                **switcher,
            },
        )

    @router.post("/api/quests")
    def set_quests(
        payload: dict,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> dict:
        names = payload.get("quest_names")
        completed = payload.get("completed")
        if (
            not isinstance(names, list)
            or not names
            or not all(isinstance(name, str) and name in QUEST_NAMES for name in names)
            or not isinstance(completed, bool)
        ):
            raise HTTPException(
                status_code=422, detail="quest_names must be known quests and completed a boolean"
            )
        repository, account_hash, _snapshot = resolve(account, active)
        if account_hash is None:
            raise HTTPException(
                status_code=409, detail="Pick or sync a character before ticking quests."
            )
        affected = with_prerequisites(names) if completed else without_dependants(names)
        repository.set_quests_completed(account_hash, sorted(affected), completed)
        return {"ok": True, "updated": len(affected)}

    return router
