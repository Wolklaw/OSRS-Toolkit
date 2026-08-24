"""Compile the application into a standalone Windows build with Nuitka.

Nuitka replaced PyInstaller in 1.1 to avoid false-positive virus warnings: PyInstaller wraps
every app in the same bootloader stub, so scanners that flag one flag all of them. Nuitka
compiles to a native binary with no shared stub.

Also avoids UPX compression (a heuristic scanners weigh heavily on its own) and includes a
full VERSIONINFO resource, which the old PyInstaller spec left out. Neither substitutes for
a code signature — releases are unsigned and the README says so.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = PROJECT_ROOT / "build" / "nuitka"
DIST_DIR = PROJECT_ROOT / "dist" / "OSRS Toolkit"
# The package directory, not __main__.py directly — Nuitka warns about compiling a loose
# script that just imports the package. With -m it compiles the package and runs __main__.
ENTRY_POINT = PROJECT_ROOT / "src" / "osrs_toolkit"

PUBLISHER = "OSRS Toolkit"
COPYRIGHT = "Copyright (C) 2026 Wolklaw. Licensed under the GNU GPL v3."


def _version() -> str:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from osrs_toolkit import __version__

    return __version__


def nuitka_arguments(version: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        # Without this, PySide6's needed plugins/translations/QML bits are left out and
        # the app dies on start-up for want of the platform plugin.
        "--enable-plugin=pyside6",
        # Disables Nuitka's dev-time runtime self-checks, which only confuse an end user.
        "--deployment",
        "--windows-console-mode=disable",
        f"--windows-icon-from-ico={PROJECT_ROOT / 'assets' / 'osrs_toolkit.ico'}",
        # VERSIONINFO resource — shown in file Properties and read by reputation checks.
        f"--company-name={PUBLISHER}",
        "--product-name=OSRS Toolkit",
        f"--file-version={version}",
        f"--product-version={version}",
        "--file-description=OSRS Toolkit — Old School RuneScape market companion",
        f"--copyright={COPYRIGHT}",
        # Assertions out, __debug__ false.
        "--python-flag=-O",
        # Run the package's __main__ as a module, matching the entry point above.
        "--python-flag=-m",
        # Read at runtime by the "What's new" window, resolved beside the executable.
        f"--include-data-files={PROJECT_ROOT / 'CHANGELOG.md'}=CHANGELOG.md",
        f"--include-data-dir={PROJECT_ROOT / 'assets'}=assets",
        f"--output-dir={BUILD_DIR}",
        "--output-filename=OSRS Toolkit.exe",
        "--assume-yes-for-downloads",
        str(ENTRY_POINT),
    ]


def _compiled_dist() -> Path:
    """Locate what Nuitka just produced (named after the entry point, e.g.
    ``osrs_toolkit.dist`` — globbed so a renamed entry point breaks loudly, not silently)."""
    candidates = sorted(BUILD_DIR.glob("*.dist"))
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected exactly one compiled build in {BUILD_DIR}, found {len(candidates)}."
        )
    return candidates[0]


def main() -> int:
    version = _version()
    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    subprocess.run(nuitka_arguments(version), check=True, cwd=PROJECT_ROOT)

    # The installer script and portable ZIP both expect dist\OSRS Toolkit.
    compiled = _compiled_dist()
    shutil.rmtree(DIST_DIR, ignore_errors=True)
    DIST_DIR.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(compiled), str(DIST_DIR))

    executable = DIST_DIR / "OSRS Toolkit.exe"
    if not executable.is_file():
        raise RuntimeError(f"The compiled build has no executable at {executable}.")
    print(f"Compiled OSRS Toolkit {version} into {DIST_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
