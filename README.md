<div align="center">

# OSRS Toolkit

### Plan better trades. Record what actually happened.

A free Windows desktop companion for Grand Exchange research, slot-aware flip planning,
trade journaling, performance review, optional RuneLite imports, High Alchemy, and skilling
margins.

[![Latest release](https://img.shields.io/github/v/release/Wolklaw/OSRS-Toolkit?style=flat-square&label=latest&color=d5ad52)](https://github.com/Wolklaw/OSRS-Toolkit/releases/latest)
![Windows](https://img.shields.io/badge/platform-Windows-3572A5?style=flat-square)
[![GPLv3](https://img.shields.io/github/license/Wolklaw/OSRS-Toolkit?style=flat-square&color=70d6a1)](LICENSE)

### [Download the latest Windows release](https://github.com/Wolklaw/OSRS-Toolkit/releases/latest)

**No login. No game automation. Your journal stays on your PC.**

</div>

[![OSRS Toolkit GE Flipper planning an eight-slot portfolio](docs/images/ge-flipper.png)](docs/images/ge-flipper.png)

## One toolkit, connected workflows

| Page | What it does |
|---|---|
| **[GE Flipper](#ge-flipper)** | Builds a whole offer portfolio around your cash, available GE slots, liquidity, buy limits, and preferred time horizon. |
| **[Watchlist](#watchlist)** | Saves any item by name and keeps passive targets, after-tax margin, ROI, volume, and trade age in one table. |
| **[Trade Journal](#trade-journal)** | Preserves original targets, records partial sales at every real price, and calculates tax-correct realized P/L. |
| **[Performance](#performance)** | Grades the plans in your journal against your real fills: results by strategy, plan-versus-actual drift, and which items actually pay. |
| **[RuneLite activity](#runelite-activity)** | Imports locally queued GE fills, and optionally completed player trades, from the companion plugin. |
| **[Alch Finder](#alch-finder)** | Compares conservative acquisition costs against High Alchemy value, nature-rune cost, liquidity, and budget. |
| **[Skilling Profit](#skilling-profit)** | Compares 83 skilling methods across 10 skills using current market data. |
| **[PvM Readiness](#pvm-readiness)** | Checks synced gear, bank, and stats against hand-picked boss checklists to show what you can do now and what is missing. |

Every market-dependent page uses the same shared data source. The app fetches market data in the
background on launch and every five minutes; **Refresh market** forces an immediate fetch without
freezing the interface, then updates every open calculation together.

## Download

The [latest release](https://github.com/Wolklaw/OSRS-Toolkit/releases/latest) contains two builds:

| Build | Best for | What you get |
|---|---|---|
| **Setup `.exe`** (recommended) | Most players | Guided install, Start Menu entry, optional desktop shortcut, uninstaller, and in-app updates. |
| **Portable `.zip`** | A folder or USB drive | Extract anywhere and open `OSRS Toolkit.exe`; nothing is installed. |

Python, PowerShell, and developer tools are not required. Releases are not currently code-signed,
so Windows may display an **Unknown publisher** warning. Download only from this repository's
official Releases page.

Reference sections:
[Market data and trade age](#market-data-fetch-time-vs-trade-age) ·
[Daily-use details](#daily-use-details) ·
[Updates and privacy](#updates-privacy-and-game-boundaries) ·
[Quick start](#quick-start) ·
[Attribution and license](#attribution-risk-and-license)

## GE Flipper

Enter a cash stack, choose **1–8 available GE slots**, and select **Quick**, **Balanced**, or
**Overnight**. OSRS Toolkit evaluates the offer mix as a whole instead of simply taking the first
rows in a ranking.

- Sizes positions around available cash, slot count, four-hour buy limits, recent volume, and
  strategy-specific risk caps.
- Cross-checks the latest completed trades with five-minute and one-hour averages.
- Rejects stale prices, one-sided liquidity, excessive drift, weak ROI, and margins too small for
  the selected strategy's adverse-price buffer.
- Shows suggested buy/sell offers, safe maximum, after-tax profit, ROI, one-hour volume, buy limit,
  maximum potential, and a market-quality confidence score.
- Reports exactly how much GP is allocated or held back. Held-back GP means no additional size
  passed the current liquidity, limit, and risk rules.
- Opens a complete price breakdown on double-click, and tracks either a single offer or the full
  recommended portfolio directly in the journal.
- Not keen on the top pick? **Recommend something else** swaps it for the next-best combination
  that doesn't reuse any item already suggested this session.

[![Detailed market breakdown for an item](docs/images/item-details.png)](docs/images/item-details.png)

The displayed prices are completed transactions, not outstanding GE offers. Confidence describes
the quality of the underlying data; it is not a promise that an offer will fill or make money. A
**Price history** tab shows roughly the last 75 days of six-hour average instant-buy and
instant-sell prices, loaded in the background the first time you open it.

## Watchlist

[![Watchlist with current buy, sell, margin, ROI, volume, and oldest trade](docs/images/watchlist.png)](docs/images/watchlist.png)

Open an item from GE Flipper or Alch Finder to add it to the persistent watchlist. Double-click a
saved row for its full latest, five-minute, and one-hour breakdown, or remove it from the same item
view.

## Trade Journal

[![Trade Journal showing partial, completed, losing, cancelled, and pending trades](docs/images/trade-journal.png)](docs/images/trade-journal.png)

Recommendations enter the journal as **Pending buy** with their original quantity, strategy, and
targets preserved. A position can move through **Bought**, **Listed for sale**, **Partially sold**,
**Completed**, or **Cancelled**.

- Watch all eight Grand Exchange slots fill in real time above the journal, laid out the way the
  game lays them out, colour-coded for buying, selling, and finished-waiting-to-be-collected.
- When a buy finishes and the item is yours to sell, its Grand Exchange slot and its journal
  row flash yellow together, so the row you now have to act on finds you rather than the other
  way round. The same flash marks a sale closing out, and the rows a newly tracked plan just
  created. A flash that happens while you are in-game waits: the sidebar carries a dot until
  you come back and look, then plays.
- Click any Grand Exchange slot to jump straight to that item's journal row, widening the
  status and period filters if they were hiding it.
- Record any number of buy fills and sale fills at different quantities and prices. Both sides of
  a trade support the same weighted-average pricing.
- Cancelled a buy order partway through? Shrink **Quantity acquired** to what actually filled and
  mark the position **Bought** instead of Cancelled, so the leftover stock still flows through
  Listed for sale, Partially sold, and Completed.
- See the weighted-average buy and sale price, remaining quantity, and tax-correct realized result.
- Scan color-coded lifecycle states, filter the table to active trades or any individual status,
  and sort by Status in trade-lifecycle order instead of alphabetically.
- Filter the summary cards and completed/cancelled rows to a time window: today, this week, this
  month, this year, a rolling range, or all time. Trades still in progress always stay visible.
- Distinguish clearly labeled projections from signed, color-coded realized profit and loss.
- Keep original suggestions beside actual execution data, even after the market changes.
- Double-click any tracked row to update it; completed trades can also be entered manually.
- Review realized profit, win rate, capital traded, and how many positions **need attention** at
  a glance.
- Overnight positions keep their original targets while current suggestions are reviewed once on
  each new day when sufficiently fresh market data is available. Quick and Balanced targets never
  move on their own, so a Listed for sale or Partially sold position is flagged once its asking
  price drifts at least 2% above what the current market now suggests. An ask that stale is
  unlikely to fill.
- **Export CSV** saves every tracked position and manually entered trade to a file, regardless of
  the status and period filters currently selected.

[![Variable sale-price editor with multiple fills and remaining stock](docs/images/trade-sale-fills.png)](docs/images/trade-sale-fills.png)

Journal statistics use trades with realized sale proceeds, including partially sold positions.
Cancelled positions with no realized sale are excluded from win rate.

The journal uses a version-independent local database. Startup backups retain the latest ten
copies, recovery logic can migrate data from older storage locations after an update, and
**Settings → Data** lets you move the database file to a location of your choice at any time.

## Performance

[![Performance page comparing realized results by strategy](docs/images/performance-strategy.png)](docs/images/performance-strategy.png)

The toolkit suggests a strategy and target prices, and the journal records the fills you
actually got. **Performance** compares the two, so a journal full of history can answer
questions it previously could not.

- **By strategy.** Realized profit, win rate, return on capital, and median hold time for
  each strategy you traded under. The comparison is between your own results, not between
  the strategies' descriptions.
- **By item.** Which items you actually make money on. Items traded only once are hidden
  until you ask for them, because a single flip says little about an item.
- **Plan vs. actual.** The original buy and sell targets against what you really filled at,
  weighted by quantity, plus what those targets promised for the quantity that actually sold
  against what it really made after tax.

[![Plan versus actual drift between target and filled prices](docs/images/performance-plan.png)](docs/images/performance-plan.png)

Every figure is realized from recorded fills; a projection never counts as a result. Return
on capital is weighted by the money at work, so one large flip is not averaged away by a
small lucky one, and a partly sold position counts only the part that sold. Paying under a
buy target is colored as the win it is rather than as a shortfall.

The period filter and the underlying history match the Trade Journal exactly, so the two
pages cannot disagree about the same trades. Manually entered completed trades are included
under **Manual entry**; they carry no plan, so they are left out of Plan vs. actual. A
position opened straight from a RuneLite offer takes that offer's own price as its target and
therefore shows no buy drift by definition.

## RuneLite activity

Install and enable the separate
[OSRS Toolkit Sync companion plugin](https://github.com/Wolklaw/osrs-toolkit-runelite), then choose
**Connect RuneLite**. Partial and completed GE fills are queued locally while RuneLite is running,
including while the desktop app is closed, and imported automatically when the toolkit opens.

[![RuneLite GE fills and player trade activity in the journal](docs/images/runelite-activity.png)](docs/images/runelite-activity.png)

- Durable local queue with idempotent event IDs, so retries do not duplicate an import.
- Character, buy/sell side, item, quantity, coins, offer slot, limit price, and offer state.
- Every fill belonging to the same Grand Exchange offer merges into one row that updates in place,
  instead of a new row per partial fill.
- Filters for all activity, Grand Exchange fills, or player trades, with details and deletion.
- Optional player-to-player tracking is **off by default** and records completed trades only.
- Trade Journal positions start tracking the moment an offer is placed, not just once it fills: a
  buy offer opens a Pending buy position sized to the full order right away, and a sell offer
  against something already Bought advances it straight to Listed for sale.
- A synced buy fill with nothing tracked yet starts a new Journal position sized to the offer's
  real total quantity, so later fills of the same order keep landing on it instead of only
  appearing once the whole order is filled.
- Optional PvM gear sync is **off by default**: when enabled and you open your bank in-game, the
  plugin records your equipped gear, inventory, bank contents, and skill levels for the PvM
  Readiness page. Nothing is uploaded.
- Active/offline heartbeat and automatic public-hiscore lookup for the connected character.

[![Local RuneLite connection and privacy controls](docs/images/runelite-connection.png)](docs/images/runelite-connection.png)

The bridge uses local files. It does not automate clicks, alter offers, communicate with game
worlds, request Jagex credentials, or upload trade history to an OSRS Toolkit server. It cannot
reconstruct trades made before installation, while disabled, or through mobile and other clients.

## Alch Finder

[![Alch Finder with conservative safe-buy prices and current trade ages](docs/images/alch-finder.png)](docs/images/alch-finder.png)

Alch Finder uses the highest recent buyer-paid observation across the latest, five-minute, and
one-hour data, then includes the current nature-rune cost. **Safer**, **Balanced**, and **Show all**
policies constrain results by trade age and liquidity. Suggested quantity is capped by budget,
hourly volume share, GE buy limit, and the 1,200-cast hourly maximum.

High Level Alchemy requires Magic level 55. Every listed candidate shares that requirement, so the
table does not waste space on a redundant Magic 55 column.

## Skilling Profit

[![Skilling Profit with 83 methods, oldest trades used, levels, and Wiki guides](docs/images/skilling-profit.png)](docs/images/skilling-profit.png)

Compare **83 processing and gathering methods across 10 skills**: Cooking, Crafting, Fishing,
Fletching, Herblore, Hunter, Magic, Mining, Smithing, and Woodcutting.

- Search by method, filter by skill, show profitable methods only, or connect a character to hide
  methods above its public hiscore levels.
- Inputs use conservative buyer-paid prices; outputs use conservative seller-received prices after
  Grand Exchange tax.
- Compare input cost, output value, profit per action, practical actions per hour, estimated GP per
  hour, requirement level, and the oldest completed trade used in the calculation.
- Every assumption links to a verified, relevant OSRS Wiki training guide.
- Notes call out burn assumptions, fixed fees, staff/tool requirements, Wilderness risk, and
  route-, gear-, competition-, or attention-dependent rates.

Rates are practical baselines, not guarantees. Test supply purchases and output sales in small
amounts before committing a large stack.

## PvM Readiness

[![PvM Readiness checklist comparing synced gear and stats against boss requirements](docs/images/pvm-readiness.png)](docs/images/pvm-readiness.png)

Enable the optional PvM gear sync in the RuneLite plugin settings, then open your bank in-game.
The next time RuneLite activity imports, the toolkit compares your equipped gear, inventory, bank
contents, and skill levels against a hand-picked checklist covering **20 bosses**: Vorkath,
Zulrah, General Graardor, Kree'arra, Cerberus, King Black Dragon, Giant Mole, Barrows, Kalphite
Queen, Dagannoth Kings, Corporeal Beast, Thermonuclear Smoke Devil, Kraken, Alchemical Hydra,
Grotesque Guardians, Sarachnis, Vet'ion, Callisto, Venenatis, and Chaos Elemental.

- Shows Ready/Not ready per activity, exactly which skills and gear are missing, and a GP/hr
  figure.
- GP/hr nets a live, market-priced supply cost (prayer potions and food) off a community
  loot-value baseline, so it moves with real prices instead of sitting fixed. Hover a row for
  the breakdown. Requirement levels and the loot-value baseline itself remain **community
  estimates curated by hand**, not a live drop-table simulation; treat them as a starting point,
  not gospel.
- Gear is matched by name across equipment, inventory, and bank. Anything a single bank trip
  away counts as owned.
- Double-click a row to open its OSRS Wiki page and verify requirements yourself.

## Market data: fetch time vs. trade age

**Market data fetched • 14:03:03** means the app successfully downloaded the newest response from
the OSRS Wiki price API at that time. It does not mean every item traded at 14:03:03.

Illiquid items may not have a new completed trade for 30 minutes, two hours, or longer. The
**Oldest trade**, **Buy trade age**, and **Oldest trade used** columns expose the age of the actual
observations behind each result. For a skilling method, the displayed age is the oldest required
input or output trade. A freshly fetched response can therefore contain older item prices, and
that is the data being reported accurately rather than a fault.

Market data is loaded once and shared across every page. A manual fetch disables all refresh
buttons until it finishes, keeps the UI responsive, and rerenders the current page automatically.
If the API is temporarily unavailable, the app can fall back to its last local cache and labels it
**Cached market data loaded**.

## Daily-use details

- **Sign-aware money:** positive results are green, true losses are red, and zero is neutral.
  Ordinary input costs are not mislabeled as losses.
- **Responsive tables:** columns fill wide windows and preserve readable minimums with smooth
  horizontal scrolling in compact windows; cells remain sortable, right-aligned, and tooltiped.
- **Purpose-built controls:** consistent buttons, fields, dropdowns, checkboxes, focus states,
  disabled states, hit areas, and dark-theme scrollbars.
- **Search that behaves normally:** clear button when populated, Escape to clear, and standard
  selection/copy/paste shortcuts.
- **Keyboard navigation:** `Ctrl+1` to `Ctrl+7` open the seven pages in sidebar order and `F5`
  refreshes the market, for checking one figure and going straight back to the game.
- **Four saved themes:** Dark, Midnight, Light, and Old School — carved stone panels,
  square corners, and the game's own orange, for a companion that sits beside the client
  without looking borrowed from somewhere else.
- **Public character lookup:** enter only a RuneScape display name to show total level and filter
  skilling methods. The toolkit never logs into the account.

## Updates, privacy, and game boundaries

[![Settings About page with version, update status, privacy, and fan-content disclosure](docs/images/settings-about.png)](docs/images/settings-about.png)

OSRS Toolkit checks for a newer official release in the background when it starts. Nothing
appears while you are up to date; when a newer version exists, a window offers to install it,
remind you later, or skip that version. The same check runs on demand from **Settings → About**.
Either way the installer is downloaded from the official GitHub release, verified against the
GitHub-provided SHA-256 digest, and only then opened. Portable copies install the standard
edition and leave the portable folder unchanged.

After an update, a **What's new** window lists that version's changes once, taken from the
bundled [changelog](CHANGELOG.md). It stays available from **Settings → About**.

Journal entries, imported trade history, settings, watchlists, and cached prices remain in local
user-data folders. Market fetches use the OSRS Wiki API, update checks use GitHub Releases, and
character lookup reads public OSRS hiscores. OSRS Toolkit never asks for or stores a Jagex
password, bank PIN, authenticator code, or game session credential. See the
[security policy](SECURITY.md) for safe reporting.

## Quick start

1. Install the app or extract the portable ZIP.
2. Open **OSRS Toolkit**; market data loads automatically.
3. Enter the GP you want to use under **Cash stack** and choose your available **GE slots**.
4. Select Quick, Balanced, or Overnight and review the proposed targets, size, and confidence.
5. Track the recommended portfolio before placing patient limit offers in game.
6. Record actual fills manually or connect the companion RuneLite plugin.

## Attribution, risk, and license

Prices come from the
[OSRS Wiki real-time price API](https://oldschool.runescape.wiki/w/RuneScape:Real-time_Prices).
Grand Exchange prices can move, unusual trades can distort observations, and displayed targets may
not fill. All results are estimates, not guaranteed profit.

OSRS Toolkit is an independent, unofficial fan project. It is not affiliated with, sponsored by,
or endorsed by Jagex. Jagex, RuneScape, and Old School RuneScape are trademarks of Jagex Limited.
All game-related intellectual property belongs to Jagex and its licensors.

**Created using intellectual property belonging to Jagex Limited under the terms of Jagex's
[Fan Content Policy](https://legal.jagex.com/docs/policies/fan-content-policy). This content is
not endorsed by or affiliated with Jagex.** See also the
[Rules of Old School RuneScape](https://legal.jagex.com/docs/rules/rules-of-old-school-runescape)
and [Jagex Terms](https://legal.jagex.com/docs/terms/terms-and-conditions/current).

OSRS Toolkit is licensed under the [GNU General Public License v3.0](LICENSE).
