from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from osrs_toolkit import app


def test_a_source_checkout_reads_from_the_project_root() -> None:
    assert app._resource_path("CHANGELOG.md").is_file()
    assert app._resource_path("assets/osrs_toolkit.ico").is_file()


def test_a_compiled_build_reads_beside_its_executable(tmp_path: Path) -> None:
    with (
        patch.dict(app.__dict__, {"__compiled__": object()}),
        patch("osrs_toolkit.app.sys.executable", str(tmp_path / "OSRS Toolkit.exe")),
    ):
        assert app._resource_path("CHANGELOG.md") == tmp_path / "CHANGELOG.md"
        assert app._resource_path("assets/spin-up.svg") == tmp_path / "assets/spin-up.svg"
