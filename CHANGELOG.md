# Changelog

All notable changes to OSRS Toolkit are documented here.

## [Unreleased]

### Changed

- Rebuilt the project landing page around the complete current feature set and fresh application
  screenshots.
- Clarified the difference between the latest market-data fetch time and the age of the completed
  trades used by Watchlist, Alch Finder, Skilling Profit, and item details.

### Fixed

- Corrected malformed bullet separators in the About and variable-sale-fill views.
- Normalized malformed legacy Balanced strategy labels when opening existing journals.

## [1.4.6] - 2026-08-11

### Changed

- Reworked GE portfolio selection to optimize the complete offer mix instead of blindly taking
  the first ranked candidates.
- Recommendations now balance total profit, confidence, usable capital, and available GE slots
  while retaining every existing liquidity, buy-limit, freshness, and risk check.

## [1.4.5] - 2026-08-11

### Fixed

- Corrected the Herblore and Hunter assumption links to their verified OSRS Wiki training pages.
- Added exact URL coverage for the complete ten-skill guide mapping.

## [1.4.4] - 2026-08-11

### Changed

- Expanded per-offer liquidity allowances when fewer GE slots are selected, so one-slot
  recommendations can use the available cash when market volume, buy limits, and risk checks allow.
- Removed the redundant Magic 55 column from Alch Finder.
- Turned every Skilling Profit assumption into a link to the relevant OSRS Wiki training guide.

## [1.4.3] - 2026-08-11

### Changed

- Restored visible up/down chevrons to numeric controls without shrinking their click targets.
- Made flip candidate sizing account for the selected number of available GE slots.
- Changing GE slots now reranks candidates immediately instead of only reallocating already-capped
  recommendations.
- Added multiple sale fills per tracked position, including quantities, varying sell prices,
  weighted-average display, remaining quantity, and tax-correct realized profit per fill.
- Added a Partially sold state and required completed positions to account for the full quantity.
- Double-clicking a tracked Trade Journal row now opens its update dialog.
- Overnight positions now preserve their original targets and automatically review separate current
  buy/sell suggestions once per new day when a fresh market snapshot is available.
- Refreshed overnight suggestions are marked in the journal and include their review date in the
  update dialog.

## [1.4.2] - 2026-08-11

### Changed

- Made every results table responsive: columns fill wide windows proportionally while retaining
  readable preferred widths and horizontal scrolling in compact windows.
- Added extra header breathing room so Trade Journal labels do not clip at smaller window sizes.
- Added version-independent journal migration, automatic startup backups, and recovery when the
  primary local database is missing.
- Updated About with Jagex Fan Content Policy attribution, unofficial-project disclosure,
  game-interaction boundaries, data handling, and links to the current official rules and terms.

## [1.4.1] - 2026-08-11

### Changed

- Separated static field labels from editable numeric values.
- Corrected spin-button hit areas and hover feedback.
- Replaced final-column stretching with content-aware table widths.
- Added practical minimum widths for item, method, trade, and assumption columns.
- Made calculated tables explicitly read-only and colored realized journal profit by sign.

## [1.4.0] - 2026-08-11

### Added

- More than 80 processing and gathering activities across ten production and gathering skills.
- Skilling method search, skill filters, profitable-only filtering, and character-level filtering.
- Detailed skilling input cost, after-tax output value, action rate, price age, and method assumptions.
- Alch budget and safety controls with suggested quantities, capital requirements, and GP/hour estimates.

### Changed

- GE Flipper targets now cross-check latest, five-minute, and one-hour prices and reject spreads
  supported by negligible trade volume on either side.
- Alch candidates now use the highest of latest, five-minute, and one-hour buyer-paid prices.
- Safer Alch mode rejects stale and thinly traded items and caps quantities by liquidity and buy limits.
- Skilling recipes use stable item IDs so Wiki display-name changes cannot silently remove methods.
- Skilling inputs and outputs now use conservative recent prices, including Grand Exchange tax.

## [1.3.0] - 2026-08-11

### Added

- Local RuneLite connection status and automatic public character lookup.
- Durable Grand Exchange fill imports from the companion RuneLite plugin.
- Optional player-to-player trade tracking, disabled by default in RuneLite.
- A RuneLite activity view with exact items and coins given and received.
- Configurable Grand Exchange slot count and one-click tracking for every recommended offer.

### Changed

- Recommendations now allocate one cash stack across several independent GE offers.
- Quantities respect cash, Grand Exchange limits, recent volume, and strategy-specific risk caps.
- Recommendation summaries show allocated cash, held-back cash, and combined estimated profit.
- Release builds now take their filenames from the application version automatically.

## [1.2.0] - 2026-08-11

### Added

- In-app update checks from Settings > About.
- Verified installer downloads from official GitHub Releases.
- Clear update guidance for installed and portable editions.

## [1.1.0] - 2026-08-11

### Added

- Item details with transparent price and margin breakdowns.
- Persistent item watchlist.
- Trade journal with pending recommendations, original targets, actual fills, and realized profit.
- Quick, balanced, and overnight GE flipping strategies.
- Windows installer and portable release formats.

### Changed

- Buy and sell targets now use recent five-minute averages to avoid relying on anomalous trades.
- Cash-stack limits immediately affect recommendation quantities and rankings.
- Improved table sorting, navigation, branding, application icon, settings, and release copy.

## [1.0.0] - 2026-08-11

- First complete public release of the desktop toolkit.
