# Contributing

OSRS Toolkit is a Windows desktop app built with Python 3.12 and PySide6. Bug reports and
focused pull requests are both welcome.

## Setting up

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

The editable install puts the launcher on your path, so run the app with:

```bash
osrs-toolkit
```

## Before opening a pull request

```bash
pytest
ruff check .
```

Both must pass. Tests live in `tests/` and run without network access or a real GE account,
which is deliberate: market responses are stubbed so a failing test always means a real
change in behaviour rather than a quiet market. Keep it that way. Anything that fetches
from the OSRS Wiki API belongs behind a stub.

The line length is 100 characters, set in `pyproject.toml`.

## What fits this project

The toolkit reads public data and records what you tell it. It does not, and will not,
automate gameplay, read game memory, place or edit Grand Exchange offers, send input to the
client, or ask for Jagex credentials. The RuneLite companion plugin only reads events
RuneLite already exposes and writes them to a local file. Contributions that cross those
lines will be declined, since they would put users at risk of a ban and break the terms the
project relies on.

New skilling methods and boss checklists are welcome. Include a link to the OSRS Wiki page
you took the rates or requirements from, since every entry is expected to be verifiable.
Skilling methods are defined in `src/osrs_toolkit/calculators.py` and boss checklists in
`src/osrs_toolkit/pvm.py`.

## Screenshots in the README

Documentation screenshots are generated, not captured by hand:

```bash
python tools/capture_docs_screenshots.py
```

The script builds a temporary profile, seeds a demo journal, renders the real widgets, and
writes all thirteen images to `docs/images/`. Run it after any change that alters the
sidebar, a table layout, or the version string, so the README does not advertise a build
that no longer exists.

## Releases

`build-release.ps1` produces the setup executable and the portable archive.

The application is compiled with Nuitka rather than packaged with PyInstaller, which means the
release build needs a C compiler and takes minutes rather than seconds. Nuitka fetches the Zig
toolchain into its own cache on the first run if it finds nothing else installed; an existing
MSVC or MinGW installation is used in preference. `packaging/build_app.py` holds the argument
list and explains the reasoning, which is mostly about false-positive virus warnings. Building
the setup wizard additionally needs Inno Setup 6; without it the script still produces the
portable archive and says so.

A version bump has to be made in two places, which is easy to half-finish: `version` in
`pyproject.toml` and `__version__` in `src/osrs_toolkit/__init__.py`. The second one is the
one that matters at runtime. It is what the sidebar, the About tab, and the update check
display, and `build-release.ps1` reads it to name the installer and the portable archive, so
a stale value there ships mislabelled files. Each release also adds a section to
`CHANGELOG.md`, which the in-app What's new window reads.

That window shows headlines, not entries in full, and it covers every release between the
version the user last opened and the one they just installed. Give a release a
`### Highlights` section of two or three short lines and it says exactly that; without one,
each entry is cut back to its opening sentence, which reads well only if that sentence
already carries the point. Entries themselves stay as long as they need to be — the window
links out to the full changelog for anyone who wants the detail.
