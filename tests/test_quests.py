from __future__ import annotations

from pathlib import Path

from osrs_toolkit.journal import JournalRepository
from osrs_toolkit.quests import (
    all_quests,
    assess_quests,
    best_for_skill,
    counted_quest_names,
    do_next,
    is_miniquest,
    nearly_ready,
    quest_points,
    ready_miniquests,
    with_prerequisites,
    without_dependants,
)


def _guide(guides, name):
    return next(guide for guide in guides if guide.quest.name == name)


def test_the_bundled_data_is_well_formed() -> None:
    quests = all_quests()
    names = [quest.name for quest in quests]

    assert len(names) == len(set(names))
    assert len(quests) > 150
    for quest in quests:
        assert all(prerequisite in names for prerequisite in quest.prerequisites)


def test_a_quest_with_no_requirements_is_ready_for_a_brand_new_account() -> None:
    guides = assess_quests({}, set())

    assert _guide(guides, "Cook's Assistant").state == "Ready"


def test_a_missing_level_blocks_a_quest_and_says_by_how_much() -> None:
    guides = assess_quests({"Magic": 70}, set())

    guide = _guide(guides, "Dragon Slayer II")
    assert guide.state == "Blocked"
    magic = next(gap for gap in guide.skill_gaps if gap.skill == "Magic")
    assert (magic.have, magic.need, magic.short) == (70, 75, 5)


def test_an_unfinished_prerequisite_blocks_even_with_every_level() -> None:
    maxed = {skill: 99 for skill in ("Magic", "Smithing", "Mining", "Crafting", "Agility")}
    maxed.update({"Thieving": 99, "Construction": 99, "Hitpoints": 99})

    guide = _guide(assess_quests(maxed, set()), "Dragon Slayer II")

    assert guide.state == "Blocked"
    assert "Legends' Quest" in guide.missing_quests


def test_quest_points_are_counted_from_what_was_ticked() -> None:
    assert quest_points({"Cook's Assistant"}) == 1
    assert quest_points({"Not a quest"}) == 0


def test_ticking_a_quest_brings_its_whole_prerequisite_chain() -> None:
    chain = with_prerequisites({"Dragon Slayer II"})

    assert {"Dragon Slayer II", "Legends' Quest", "Heroes' Quest"} <= chain


def test_unticking_a_quest_removes_everything_that_depended_on_it() -> None:
    removed = without_dependants({"Legends' Quest"})

    assert "Dragon Slayer II" in removed
    assert "Cook's Assistant" not in removed


def test_do_next_only_offers_ready_quests_and_puts_the_biggest_unlockers_first() -> None:
    guides = assess_quests({}, set())

    picks = do_next(guides, 5)

    assert picks
    assert all(guide.state == "Ready" for guide in picks)
    assert [guide.unlocks for guide in picks] == sorted(
        (guide.unlocks for guide in picks), reverse=True
    )


def test_nearly_ready_is_levels_only_and_nearest_first() -> None:
    guides = assess_quests({"Magic": 70, "Smithing": 70, "Mining": 68}, set())

    close = nearly_ready(guides, 20, max_levels=10)

    assert all(guide.only_levels_missing for guide in close)
    shorts = [guide.levels_short for guide in close]
    assert shorts == sorted(shorts)


def test_done_quests_never_appear_in_the_suggestions() -> None:
    guides = assess_quests({}, {"Cook's Assistant"})

    assert _guide(guides, "Cook's Assistant").state == "Done"
    assert "Cook's Assistant" not in [guide.quest.name for guide in do_next(guides, 50)]


def test_best_for_skill_ranks_by_experience() -> None:
    guides = assess_quests({skill: 99 for skill in ("Cooking", "Crafting")}, set())

    picks = best_for_skill(guides, "Cooking", 5)

    rewards = [guide.quest.experience["Cooking"] for guide in picks]
    assert rewards == sorted(rewards, reverse=True)


def test_progress_is_kept_per_character_and_survives_a_reopen(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    repository = JournalRepository(path)

    repository.set_quests_completed("hash-a", ["Cook's Assistant", "Cook's Assistant"], True)
    repository.set_quests_completed("hash-b", ["Demon Slayer"], True)

    reopened = JournalRepository(path)
    assert reopened.list_completed_quests("hash-a") == {"Cook's Assistant"}
    assert reopened.list_completed_quests("hash-b") == {"Demon Slayer"}

    reopened.set_quests_completed("hash-a", ["Cook's Assistant"], False)
    assert reopened.list_completed_quests("hash-a") == frozenset()


def test_miniquests_are_kept_out_of_do_next_and_listed_on_their_own() -> None:
    guides = assess_quests({skill: 99 for skill in ("Magic", "Smithing", "Mining")}, set())

    assert not any(is_miniquest(guide.quest) for guide in do_next(guides, 500))
    assert all(is_miniquest(guide.quest) for guide in ready_miniquests(guides))


def test_the_quest_total_matches_the_games_own_count() -> None:
    assert len(counted_quest_names()) == 184
    assert quest_points(counted_quest_names()) == 343
