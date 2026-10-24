from __future__ import annotations

import html
import re
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path

_RELEASE_HEADING = re.compile(r"^##\s+\[([^\]]+)\]\s*(?:[-–]\s*(.*))?$")
_SECTION_HEADING = re.compile(r"^###\s+(.+)$")
_BULLET = re.compile(r"^[-*]\s+(.*)$")
_CODE_SPAN = re.compile(r"`([^`]+)`")
_BOLD_SPAN = re.compile(r"\*\*([^*]+)\*\*")
_LINK_SPAN = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+(?=[A-Z"“])')

_PLACEHOLDER_VERSIONS = {"unreleased"}

HIGHLIGHTS_HEADING = "highlights"
MUTED = "#91a0b4"


@dataclass(frozen=True)
class ReleaseSection:
    heading: str
    entries: tuple[str, ...]


@dataclass(frozen=True)
class ReleaseNotes:
    version: str
    date: str
    sections: tuple[ReleaseSection, ...]

    @property
    def is_empty(self) -> bool:
        return not any(section.entries for section in self.sections)


def parse_changelog(text: str) -> list[ReleaseNotes]:
    releases: list[ReleaseNotes] = []
    version = ""
    date = ""
    sections: list[ReleaseSection] = []
    heading = ""
    entries: list[str] = []

    def close_section() -> None:
        nonlocal heading, entries
        if heading and entries:
            sections.append(ReleaseSection(heading, tuple(entries)))
        heading = ""
        entries = []

    def close_release() -> None:
        nonlocal version, date, sections
        close_section()
        if version:
            releases.append(ReleaseNotes(version, date, tuple(sections)))
        version = ""
        date = ""
        sections = []

    for line in text.splitlines():
        release_match = _RELEASE_HEADING.match(line)
        if release_match:
            close_release()
            candidate = release_match.group(1).strip()
            if candidate.lower() in _PLACEHOLDER_VERSIONS:
                continue
            version = candidate.removeprefix("v")
            date = (release_match.group(2) or "").strip()
            continue
        if not version:
            continue

        section_match = _SECTION_HEADING.match(line)
        if section_match:
            close_section()
            heading = section_match.group(1).strip()
            continue

        bullet_match = _BULLET.match(line)
        if bullet_match:
            entries.append(bullet_match.group(1).strip())
            continue
        if entries and line.startswith((" ", "\t")) and line.strip():
            entries[-1] = f"{entries[-1]} {line.strip()}"

    close_release()
    return releases


def load_release_notes(path: Path) -> list[ReleaseNotes]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return parse_changelog(text)


def notes_for_version(releases: list[ReleaseNotes], version: str) -> ReleaseNotes | None:
    wanted = version.strip().removeprefix("v")
    return next((release for release in releases if release.version == wanted), None)


def version_key(version: str) -> tuple[int, ...]:
    digits = re.fullmatch(r"v?(\d+(?:\.\d+)*)", version.strip())
    if not digits:
        return ()
    return tuple(int(part) for part in digits.group(1).split("."))


def catch_up_notes(
    releases: Sequence[ReleaseNotes],
    *,
    current: str,
    since: str = "",
    limit: int = 5,
) -> list[ReleaseNotes]:
    running = version_key(current)
    missed = version_key(since) if since else ()
    described = [
        release
        for release in releases
        if not release.is_empty and version_key(release.version) <= running
    ]
    caught_up = [
        release for release in described if not missed or version_key(release.version) > missed
    ]
    if not caught_up:
        running_notes = notes_for_version(described, current)
        caught_up = [running_notes] if running_notes else []
    return caught_up[:limit]


@dataclass(frozen=True)
class Highlight:
    label: str
    text: str


def summarize(notes: ReleaseNotes, limit: int = 4) -> tuple[tuple[Highlight, ...], int]:
    for section in notes.sections:
        if section.heading.strip().lower() == HIGHLIGHTS_HEADING:
            written = tuple(Highlight("", entry) for entry in section.entries)
            return written[:limit], max(0, len(written) - limit)
    grouped = [
        [Highlight(section.heading, _opening_sentence(entry)) for entry in section.entries]
        for section in notes.sections
    ]
    points = tuple(
        point for round_ in zip_longest(*grouped) for point in round_ if point is not None
    )
    return points[:limit], max(0, len(points) - limit)


def _opening_sentence(text: str, limit: int = 130) -> str:
    sentence = _SENTENCE_END.split(text.strip(), maxsplit=1)[0].strip()
    if len(sentence) <= limit:
        return sentence
    return f"{sentence[:limit].rsplit(' ', 1)[0].rstrip(' ,;:—-')}…"


def _inline_html(text: str) -> str:
    escaped = html.escape(text)
    escaped = _LINK_SPAN.sub(r'<a href="\2">\1</a>', escaped)
    escaped = _BOLD_SPAN.sub(r"<b>\1</b>", escaped)
    return _CODE_SPAN.sub(r"<code>\1</code>", escaped)


def notes_to_html(notes: ReleaseNotes, limit: int = 4) -> str:
    points, omitted = summarize(notes, limit)
    bullets = []
    for point in points:
        label = (
            f"<span style='color:{MUTED};'>{html.escape(point.label)} — </span>"
            if point.label
            else ""
        )
        bullets.append(f"<li style='margin-bottom:6px;'>{label}{_inline_html(point.text)}</li>")
    if omitted:
        bullets.append(
            f"<li style='margin-bottom:6px;color:{MUTED};'>and {omitted} more "
            f"{'change' if omitted == 1 else 'changes'} in the full changelog</li>"
        )
    return f"<ul style='margin-top:0;'>{''.join(bullets)}</ul>"


def catch_up_to_html(releases: Sequence[ReleaseNotes]) -> str:
    if not releases:
        return ""
    newest, earlier = releases[0], releases[1:]
    parts = [f"<h2 style='margin-bottom:2px;'>What's new in {html.escape(newest.version)}</h2>"]
    if newest.date:
        parts.append(
            f"<p style='color:{MUTED};margin-top:0;'>Released {html.escape(newest.date)}</p>"
        )
    parts.append(notes_to_html(newest))
    if earlier:
        parts.append("<h3 style='margin-bottom:2px;'>Also new since you last opened the app</h3>")
        for release in earlier:
            date = f" <span style='color:{MUTED};'>· {html.escape(release.date)}</span>"
            parts.append(
                f"<p style='margin-bottom:0;'><b>{html.escape(release.version)}</b>"
                f"{date if release.date else ''}</p>"
            )
            parts.append(notes_to_html(release, limit=3))
    parts.append(
        f"<p style='color:{MUTED};'>These are the headlines. “Full changelog” below "
        "opens every entry in full.</p>"
    )
    return "".join(parts)
