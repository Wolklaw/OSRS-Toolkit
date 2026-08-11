# OSRS Toolkit

A Windows desktop companion for Grand Exchange analysis, trade tracking, High Alchemy,
skilling profit, and Managing Miscellania.

## Download

### **[Download the latest release](https://github.com/Wolklaw/OSRS-Toolkit/releases/latest)**

Choose one file:

- **Setup `.exe` — recommended:** guided installation, Start Menu entry, optional desktop
  shortcut, uninstaller, and in-app updates.
- **Portable `.zip`:** extract it anywhere and open `OSRS Toolkit.exe`. Nothing is installed.

Python, PowerShell, and developer tools are not required. Windows may show an **Unknown
publisher** warning because the application is not currently code-signed.

![OSRS Toolkit GE Flipper](docs/images/ge-flipper.png)

## Quick start

1. Install the app or extract the portable ZIP.
2. Open **OSRS Toolkit**. Current market prices load automatically.
3. Enter the amount of GP you want to use in **Cash stack**.
4. Choose **Quick**, **Balanced**, or **Overnight** based on how long you want offers to sit.
5. Read the suggested buy price, sell price, quantity, estimated profit, and confidence before
   placing anything in the Grand Exchange.

The cash stack affects quantities and rankings immediately. Select **Update prices** whenever you
want a fresh market snapshot.

## Using the GE Flipper

The recommendation panel gives direct **BUY** and **THEN SELL** instructions. These are patient
limit offers based on recent trades—not guaranteed instant prices.

Each result includes:

- **Buy / Sell:** suggested offer prices.
- **Qty:** quantity sized for your cash stack, volume, and the item's GE limit.
- **Profit ea. / Potential:** estimated profit after Grand Exchange tax.
- **ROI:** estimated return relative to the buy price.
- **1h volume:** recent market activity.
- **Confidence:** a market-quality estimate based on liquidity, freshness, and stability.

Double-click any item for its full price breakdown. Use **Track top recommendation** to preserve
the current targets in the Trade Journal before market prices change.

No recommendation is a guaranteed trade. Start with small quantities and confirm that an item is
actually filling near the displayed prices before committing more GP.

## Tracking a trade

![OSRS Toolkit Trade Journal](docs/images/trade-journal.png)

When you track a recommendation, it begins as **Pending buy** with the original quantity and
targets saved. As the trade progresses:

1. Select the journal row.
2. Choose **Update selected trade**.
3. Change its status to **Bought**, **Listed for sale**, **Completed**, or **Cancelled**.
4. Enter the actual prices you received.

Only completed trades count toward realized profit, win rate, and capital traded. You can also add
a completed trade manually.

## Finding High Alchemy candidates

![OSRS Toolkit Alch Finder](docs/images/alch-finder.png)

The Alch Finder compares an item's current buy price with its High Alchemy value after the cost of
one nature rune. Check **1h volume** as well as profit: an attractive margin is not useful if you
cannot buy enough items at that price.

## Other tools

- **Watchlist:** double-click supported market rows to save items for later monitoring.
- **Skilling Profit:** compares supported processing methods by estimated profit per action and GP
  per hour. Connect a character to filter methods by public skill levels.
- **Kingdom Optimizer:** compares Managing Miscellania resources using current market prices.
- **Character lookup:** enter only a public RuneScape display name. This reads public OSRS hiscores
  and does not log into your account.

## Updating the app

![OSRS Toolkit update settings](docs/images/updates.png)

Open **Settings → About → Check for updates**. When a newer version exists, OSRS Toolkit downloads
the official GitHub installer, verifies its SHA-256 digest, opens the installer, and closes the old
version.

The portable edition cannot replace itself while running. Its update button installs the standard
edition and leaves the old portable folder unchanged.

## Market data and privacy

Prices come from the OSRS Wiki/RuneLite real-time price API. Settings, cached prices, watchlists,
and journal entries remain on your computer.

OSRS Toolkit never asks for or stores your Jagex password, bank PIN, authenticator code, or game
session credentials.

Grand Exchange prices can move, offers may not fill, and unusual trades can temporarily distort
market data. Recommendations are estimates—not guaranteed profit.

## License

OSRS Toolkit is available under the [GNU General Public License v3.0](LICENSE).

This project is not affiliated with or endorsed by Jagex. Old School RuneScape and RuneScape are
trademarks of Jagex Limited.
