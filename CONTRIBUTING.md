# Contributing

OSRS Toolkit is a Windows desktop app built with Python 3.12 and PySide6, sharing its domain
layer with [runescope.app](https://runescope.app) — the primary product now, so new companion
features (anything beyond the existing pages here) should land there first. Bug reports and
focused pull requests on this desktop app are both welcome.

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
ruff format --check .
```

All three must pass. Tests live in `tests/` and run without network access or a real GE account —
market responses are stubbed, so a failing test always means a real behavior change, not a
quiet market. Keep it that way; anything that fetches from the OSRS Wiki API belongs behind a
stub.

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
sidebar, a table layout, or the version string.

## Releases

`build-release.ps1` produces the setup executable and the portable archive.

The app is compiled with Nuitka rather than packaged with PyInstaller (mostly to avoid
false-positive virus warnings — see `packaging/build_app.py`), so the release build needs a C
compiler and takes minutes rather than seconds. Nuitka fetches the Zig toolchain into its own
cache on first run if nothing else is installed; an existing MSVC or MinGW installation is
preferred if present. Building the setup wizard also needs Inno Setup 6; without it the script
still produces the portable archive and says so.

Bump the version in two places: `version` in `pyproject.toml` and `__version__` in
`src/osrs_toolkit/__init__.py`. The second is what matters at runtime — the sidebar, About tab,
and update check all read it, and `build-release.ps1` uses it to name the installer and
portable archive, so a stale value ships mislabelled files. Each release also needs a section
in `CHANGELOG.md`, which the in-app What's new window reads.

That window shows headlines, not full entries, covering every release between the version the
user last opened and the one they just installed. Give a release a `### Highlights` section of
two or three short lines to control what shows; without one, each entry is cut to its opening
sentence. The window links out to the full changelog for anyone who wants more detail.
