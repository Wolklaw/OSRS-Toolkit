from __future__ import annotations

from pathlib import Path

import pytest

from osrs_toolkit.journal import JournalRepository
from osrs_toolkit.reports import REPORTS, ReportContext


@pytest.mark.parametrize("key", list(REPORTS))
def test_every_report_builds_from_an_empty_journal(key: str, tmp_path: Path) -> None:
    context = ReportContext(JournalRepository(tmp_path / "journal.db"), None, {}, [])

    sections = REPORTS[key].build(context, None)

    assert sections
    assert all(section.title for section in sections)
