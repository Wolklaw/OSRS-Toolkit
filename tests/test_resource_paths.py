"""Where the app looks for the files that ship alongside it.

A compiled release and a source checkout keep the changelog and the icons in different
places, and getting it wrong fails quietly: the "What's new" window comes up empty and the
window icon falls back to Qt's default, with nothing logged and no error raised.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from osrs_toolkit import app


def test_a_source_checkout_reads_from_the_project_root() -> None:
    assert app._resource_path("CHANGELOG.md").is_file()
    assert app._resource_path("assets/osrs_toolkit.ico").is_file()


def test_a_compiled_build_reads_beside_its_executable(tmp_path: Path) -> None:
    """Regression guard for the move off PyInstaller in 1.1.

    The old lookup asked for ``sys._MEIPASS``, which only PyInstaller sets. Under any other
    packager it fell through to a path derived from ``__file__`` — two directories up from
    the module, which is the project root in a checkout and nowhere in particular in a
    release. Nuitka marks compiled modules with ``__compiled__`` and puts data files next
    to the executable, so both halves of that are what this checks.
    """
    with (
        patch.dict(app.__dict__, {"__compiled__": object()}),
        patch("osrs_toolkit.app.sys.executable", str(tmp_path / "OSRS Toolkit.exe")),
    ):
        assert app._resource_path("CHANGELOG.md") == tmp_path / "CHANGELOG.md"
        assert app._resource_path("assets/spin-up.svg") == tmp_path / "assets/spin-up.svg"
