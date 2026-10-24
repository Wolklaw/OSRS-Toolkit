from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = PROJECT_ROOT / "build" / "nuitka"
DIST_DIR = PROJECT_ROOT / "dist" / "OSRS Toolkit"
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
        "--enable-plugin=pyside6",
        "--deployment",
        "--windows-console-mode=disable",
        f"--windows-icon-from-ico={PROJECT_ROOT / 'assets' / 'osrs_toolkit.ico'}",
        f"--company-name={PUBLISHER}",
        "--product-name=OSRS Toolkit",
        f"--file-version={version}",
        f"--product-version={version}",
        "--file-description=OSRS Toolkit — Old School RuneScape market companion",
        f"--copyright={COPYRIGHT}",
        "--python-flag=-O",
        "--python-flag=-m",
        f"--include-data-files={PROJECT_ROOT / 'CHANGELOG.md'}=CHANGELOG.md",
        f"--include-data-dir={PROJECT_ROOT / 'assets'}=assets",
        f"--output-dir={BUILD_DIR}",
        "--output-filename=OSRS Toolkit.exe",
        "--assume-yes-for-downloads",
        str(ENTRY_POINT),
    ]


def _compiled_dist() -> Path:
    candidates = sorted(BUILD_DIR.glob("*.dist"))
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected exactly one compiled build in {BUILD_DIR}, found {len(candidates)}."
        )
    return candidates[0]


def _clear_dist() -> None:
    for _ in range(4):
        if not DIST_DIR.exists():
            return
        shutil.rmtree(DIST_DIR, ignore_errors=True)
        if not DIST_DIR.exists():
            return
        time.sleep(2)
    try:
        shutil.rmtree(DIST_DIR)
    except OSError as error:
        raise RuntimeError(
            f"{DIST_DIR} still holds files after four attempts ({error}). A running copy of "
            f"the app, or a virus scanner working through the last build, is holding one. "
            f"Stopping here rather than moving the new build in, because shutil.move onto a "
            f"directory that still exists nests the build inside it, and the failure then "
            f"surfaces as a missing executable with nothing pointing at the real cause."
        ) from error


def main() -> int:
    version = _version()
    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    subprocess.run(nuitka_arguments(version), check=True, cwd=PROJECT_ROOT)

    compiled = _compiled_dist()
    _clear_dist()
    DIST_DIR.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(compiled), str(DIST_DIR))

    executable = DIST_DIR / "OSRS Toolkit.exe"
    if not executable.is_file():
        raise RuntimeError(f"The compiled build has no executable at {executable}.")
    print(f"Compiled OSRS Toolkit {version} into {DIST_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
