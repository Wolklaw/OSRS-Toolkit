from __future__ import annotations

from pathlib import Path

from osrs_toolkit import __version__
from osrs_toolkit.release_notes import (
    ReleaseNotes,
    ReleaseSection,
    catch_up_notes,
    catch_up_to_html,
    load_release_notes,
    notes_for_version,
    notes_to_html,
    parse_changelog,
    summarize,
)

SAMPLE = """# Changelog

All notable changes are documented here.

## [Unreleased]

## [1.5.3] - 2026-08-15

### Added

- A bullet that wraps onto a second line and should be
  joined back together.

### Fixed

- A `code span` and **bold text** and an <angle> bracket.

## [1.5.2] - 2026-08-14

### Fixed

- An earlier fix.

## [1.5.1] - 2026-08-13

### Highlights

- The short version of 1.5.1.

### Fixed

- The long version of 1.5.1, which goes on at some length about what was wrong before it
  was put right. It has a second sentence nobody needs to read here.

## [1.5.0] - 2026-08-12

### Added

- The oldest release.
"""


def test_parses_releases_in_file_order() -> None:
    releases = parse_changelog(SAMPLE)
    assert [release.version for release in releases] == ["1.5.3", "1.5.2", "1.5.1", "1.5.0"]
    assert releases[0].date == "2026-08-15"


def test_unreleased_heading_is_not_a_version() -> None:
    assert all(release.version.lower() != "unreleased" for release in parse_changelog(SAMPLE))


def test_wrapped_bullets_are_joined() -> None:
    added = parse_changelog(SAMPLE)[0].sections[0]
    assert added.heading == "Added"
    assert added.entries == ("A bullet that wraps onto a second line and should be joined back together.",)


def test_notes_for_version_ignores_a_v_prefix() -> None:
    releases = parse_changelog(SAMPLE)
    assert notes_for_version(releases, "v1.5.2") is not None
    assert notes_for_version(releases, "9.9.9") is None


def test_html_escapes_markup_but_keeps_changelog_formatting() -> None:
    html = notes_to_html(parse_changelog(SAMPLE)[0])
    assert "<code>code span</code>" in html
    assert "<b>bold text</b>" in html
    assert "&lt;angle&gt;" in html
    assert "<angle>" not in html


def test_a_release_that_wrote_its_own_summary_is_taken_at_its_word() -> None:
    """A "Highlights" section is the short version someone wrote deliberately; the long
    entries below it are for the changelog, not for this window."""
    points, omitted = summarize(notes_for_version(parse_changelog(SAMPLE), "1.5.1"))

    assert [point.text for point in points] == ["The short version of 1.5.1."]
    assert omitted == 0


def test_an_entry_without_a_summary_is_cut_back_to_its_first_sentence() -> None:
    points, _omitted = summarize(notes_for_version(parse_changelog(SAMPLE), "1.5.2"))

    assert points[0].label == "Fixed"
    assert points[0].text == "An earlier fix."


def test_a_long_first_sentence_is_shortened_on_a_word_boundary() -> None:
    long_entry = ReleaseNotes(
        "9.9.9", "", (ReleaseSection("Fixed", ("word " * 60 + "end. Second sentence.",)),)
    )

    text = summarize(long_entry)[0][0].text

    assert text.endswith("…")
    assert "Second sentence" not in text
    assert len(text) <= 131
    assert not text.rstrip("…").endswith(" "), "cut mid-word or left a trailing space"


def test_only_the_first_few_points_are_shown_and_the_rest_are_counted() -> None:
    crowded = ReleaseNotes(
        "9.9.9", "", (ReleaseSection("Fixed", tuple(f"Fix {number}." for number in range(7))),)
    )

    points, omitted = summarize(crowded, limit=4)

    assert len(points) == 4
    assert omitted == 3
    assert "and 3 more changes in the full changelog" in notes_to_html(crowded)


def test_no_kind_of_change_crowds_out_another() -> None:
    """Taking the first four entries as written would describe a release that fixed four
    things and changed three entirely in bug fixes."""
    mixed = ReleaseNotes(
        "9.9.9",
        "",
        (
            ReleaseSection("Fixed", tuple(f"Fix {number}." for number in range(4))),
            ReleaseSection("Changed", tuple(f"Change {number}." for number in range(3))),
        ),
    )

    points, _omitted = summarize(mixed, limit=4)

    assert [point.label for point in points] == ["Fixed", "Changed", "Fixed", "Changed"]


def test_a_full_stop_inside_a_quoted_figure_is_not_the_end_of_the_line() -> None:
    quoted = ReleaseNotes(
        "9.9.9", "", (ReleaseSection("Fixed", ('A row showed "Est. 12,345 gp" as its P/L.',)),)
    )

    assert summarize(quoted)[0][0].text == 'A row showed "Est. 12,345 gp" as its P/L.'


def test_the_catch_up_covers_every_version_missed() -> None:
    caught_up = catch_up_notes(parse_changelog(SAMPLE), current="1.5.3", since="1.5.1")

    assert [release.version for release in caught_up] == ["1.5.3", "1.5.2"]


def test_the_catch_up_stops_at_the_version_being_run() -> None:
    """The changelog ships inside the app, so an entry above the running version would be
    describing a build the user does not have."""
    caught_up = catch_up_notes(parse_changelog(SAMPLE), current="1.5.1", since="1.5.0")

    assert [release.version for release in caught_up] == ["1.5.1"]


def test_the_catch_up_has_a_ceiling() -> None:
    caught_up = catch_up_notes(parse_changelog(SAMPLE), current="1.5.3", limit=2)

    assert [release.version for release in caught_up] == ["1.5.3", "1.5.2"]


def test_a_version_older_than_the_one_last_seen_still_says_what_it_is() -> None:
    """Rolling back to an earlier build leaves nothing "missed" to report; the window
    falls back to the release actually running rather than opening empty."""
    caught_up = catch_up_notes(parse_changelog(SAMPLE), current="1.5.1", since="1.5.3")

    assert [release.version for release in caught_up] == ["1.5.1"]


def test_the_catch_up_page_leads_with_the_new_version_and_lists_the_rest() -> None:
    html = catch_up_to_html(catch_up_notes(parse_changelog(SAMPLE), current="1.5.3", since="1.5.0"))

    assert "What's new in 1.5.3" in html
    assert "Also new since you last opened the app" in html
    assert "1.5.2" in html and "1.5.1" in html
    assert "full changelog" in html.lower(), "no pointer to the detail this leaves out"


def test_the_catch_up_page_skips_the_history_when_there_is_none() -> None:
    html = catch_up_to_html(catch_up_notes(parse_changelog(SAMPLE), current="1.5.3", limit=1))

    assert "What's new in 1.5.3" in html
    assert "Also new since" not in html


def test_release_with_no_entries_reports_empty() -> None:
    releases = parse_changelog("## [2.0.0] - 2026-01-01\n\n### Added\n")
    assert releases[0].is_empty


def test_missing_changelog_yields_no_notes(tmp_path: Path) -> None:
    assert load_release_notes(tmp_path / "absent.md") == []


def test_shipped_changelog_describes_the_current_version() -> None:
    """The "What's new" window is empty for a release nobody wrote notes for."""
    changelog = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
    notes = notes_for_version(load_release_notes(changelog), __version__)
    assert notes is not None, f"CHANGELOG.md has no section for version {__version__}"
    assert not notes.is_empty
