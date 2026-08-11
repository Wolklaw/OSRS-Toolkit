# OSRS Toolkit

A clean Windows desktop companion for Old School RuneScape market and profit planning.

## Current MVP

- GE Flipper with real-time RuneLite/OSRS Wiki transaction data
- Item detail views and a persistent watchlist
- Trade journal with pending recommendation tracking, preserved targets, actual fills, realized
  profit, win rate, and capital tracking
- Correct 2% per-item GE tax (maximum 5,000,000 GP)
- Liquidity, freshness, ROI, profit, buy-limit, and risk-aware ranking
- Offline cache when the market feed is temporarily unavailable
- Placeholders for Alch Finder, Skilling Profit Finder, and Kingdom Optimizer

The app presents estimates, not guaranteed trades. The Grand Exchange is dynamic, and observed
prices can be stale or manipulated. Every recommendation therefore shows freshness and a confidence
score instead of claiming certainty.

## Run in VS Code

1. Install Python 3.12 or newer and select it in VS Code.
2. Open this folder in VS Code.
3. In a PowerShell terminal, make sure you are in the project root and run:

```powershell
Set-Location "E:\Personal Projects\OSRS Toolkit"
python -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install -e ".[dev]"
& ".\.venv\Scripts\python.exe" -m osrs_toolkit
```

This deliberately uses the virtual environment's Python directly, so PowerShell script execution
does not need to be enabled. Run tests with:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest
```

## Data

Prices come from `prices.runescape.wiki`, the OSRS Wiki/RuneLite real-time Grand Exchange feed.
Requests use bulk endpoints and a descriptive User-Agent. Cached responses live in the user's local
application-data folder; no account credentials are requested or stored.

## Windows releases

End users do not need Python, VS Code, PowerShell, or any dependencies.

- `OSRS-Toolkit-Portable-1.1.0.zip`: unzip it anywhere and double-click `OSRS Toolkit.exe`.
- `OSRS-Toolkit-Setup-1.1.0.exe`: a normal Windows setup wizard with a Start Menu shortcut,
  optional Desktop shortcut, and an uninstaller.

For maintainers, `build-release.ps1` runs tests, builds the self-contained application, creates the
portable ZIP, and compiles the setup wizard when Inno Setup 6 is available.

## License

OSRS Toolkit is licensed under the [GNU General Public License v3.0](LICENSE). You may use,
study, modify, and redistribute it under the terms of that license.

This project is not affiliated with or endorsed by Jagex. Old School RuneScape and RuneScape are
trademarks of Jagex Limited.
