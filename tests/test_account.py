from __future__ import annotations

import io
import urllib.error
from typing import Self
from unittest.mock import patch

import pytest

from osrs_toolkit.account import HISCORE_SKILLS, AccountLookupError, fetch_player


class FakeResponse(io.BytesIO):
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _hiscores_body(levels: dict[str, int]) -> bytes:
    lines = [f"1,{levels.get(skill, 1)},0" for skill in HISCORE_SKILLS]
    return "\n".join(lines).encode("utf-8")


def test_fetch_player_parses_hiscores_response() -> None:
    body = _hiscores_body({"Overall": 2277, "Attack": 99, "Sailing": 99})
    with patch("osrs_toolkit.account.urllib.request.urlopen", return_value=FakeResponse(body)):
        profile = fetch_player("Example Player")

    assert profile.name == "Example Player"
    assert profile.total_level == 2277
    assert profile.skills["Attack"] == 99
    assert profile.skills["Sailing"] == 99


def test_fetch_player_skips_malformed_skill_lines_instead_of_disabling_lookup() -> None:
    """A garbled or shifted hiscores line must not crash the whole parse (regression: P1)."""
    lines = [f"1,{100 + index},0" for index, _skill in enumerate(HISCORE_SKILLS)]
    lines[1] = "not,a,number"  # Attack's level field is unparsable.
    body = "\n".join(lines).encode("utf-8")
    with patch("osrs_toolkit.account.urllib.request.urlopen", return_value=FakeResponse(body)):
        profile = fetch_player("Example Player")

    assert "Attack" not in profile.skills
    assert profile.total_level == 100


def test_fetch_player_rejects_empty_name() -> None:
    with pytest.raises(AccountLookupError, match="Enter a character name"):
        fetch_player("   ")


def test_fetch_player_raises_when_response_has_no_overall_line() -> None:
    with (
        patch("osrs_toolkit.account.urllib.request.urlopen", return_value=FakeResponse(b"")),
        pytest.raises(AccountLookupError, match="unexpected response"),
    ):
        fetch_player("Example Player")


def test_fetch_player_reports_not_found() -> None:
    error = urllib.error.HTTPError("url", 404, "Not Found", hdrs=None, fp=None)  # type: ignore[arg-type]
    with (
        patch("osrs_toolkit.account.urllib.request.urlopen", side_effect=error),
        pytest.raises(AccountLookupError, match="not found"),
    ):
        fetch_player("Missing Player")
