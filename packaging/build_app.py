"""Compile the application into a standalone Windows build with Nuitka.

Nuitka replaced PyInstaller in 1.1 for one reason: false-positive virus warnings.
PyInstaller puts every application it packages behind the same bootloader stub, so a
scanner that learns to distrust that stub distrusts every program built with it, this one
included, on the strength of what other people shipped. Nuitka compiles the Python into a
native binary instead, leaving no shared stub to recognise.

Two smaller changes here are worth as much as the packager swap:

* Nothing is UPX-compressed. The old spec packed both the executable and the collected
  folder, and a UPX-packed section is a heuristic scanners weigh heavily on its own — it
  is how a program hides its contents, and almost nothing legitimate needs to.
* The executable carries a full VERSIONINFO resource, which the old spec left out
  entirely (``version=None``). An unsigned binary that also declines to say who made it,
  what it is, or what version it claims to be has nothing for a reputation check to hold.

None of it substitutes for a code signature, which is the only thing that clears
SmartScreen's "Unknown publisher" prompt. Releases are unsigned and the README says so.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = PROJECT_ROOT / "build" / "nuitka"
DIST_DIR = PROJECT_ROOT / "dist" / "OSRS Toolkit"
# The package directory, not its __main__.py. Nuitka warns about the latter: handed the
# file alone it compiles a loose script that happens to import the package, and resolves
# that import through whatever is installed in the environment. Handed the directory with
# -m, it compiles the package and runs __main__ inside it, which is what "osrs-toolkit"
# means everywhere else in this project.
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
        # The Qt plugin knows which of PySide6's plugins, translations, and QML bits a
        # build actually needs. Without it the compile succeeds and the application dies
        # on start-up for want of the platform plugin.
        "--enable-plugin=pyside6",
        # Turns off the runtime self-checks Nuitka runs to help during development, which
        # exist to warn a developer and only confuse someone who installed a release.
        "--deployment",
        "--windows-console-mode=disable",
        f"--windows-icon-from-ico={PROJECT_ROOT / 'assets' / 'osrs_toolkit.ico'}",
        # The VERSIONINFO resource. Windows shows these in the file's Properties, and a
        # reputation check reads them.
        f"--company-name={PUBLISHER}",
        "--product-name=OSRS Toolkit",
        f"--file-version={version}",
        f"--product-version={version}",
        "--file-description=OSRS Toolkit — Old School RuneScape market companion",
        f"--copyright={COPYRIGHT}",
        # Matches the old spec's optimize=1: assertions out, __debug__ false.
        "--python-flag=-O",
        # Run the package's __main__ as a module, the way the entry point above expects.
        "--python-flag=-m",
        # Read at runtime by the "What's new" window, which resolves it beside the
        # executable. assets/ holds runtime art only — installer artwork lives in
        # packaging/wizard/ precisely so this line cannot pick it up.
        f"--include-data-files={PROJECT_ROOT / 'CHANGELOG.md'}=CHANGELOG.md",
        f"--include-data-dir={PROJECT_ROOT / 'assets'}=assets",
        f"--output-dir={BUILD_DIR}",
        "--output-filename=OSRS Toolkit.exe",
        "--assume-yes-for-downloads",
        str(ENTRY_POINT),
    ]


def _compiled_dist() -> Path:
    """Locate what Nuitka just produced.

    The folder is named after the entry point rather than after ``--output-filename``,
    so it is ``osrs_toolkit.dist`` here. Globbing instead of hard-coding that means
    renaming the entry point one day breaks the build loudly, rather than leaving this
    pointing at a directory that is no longer written.
    """
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

    # The installer script and the portable ZIP both read dist\OSRS Toolkit, so the
    # compiled folder moves there under the name they already expect.
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
