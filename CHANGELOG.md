# Changelog

All notable changes to OSRS Toolkit are documented here.

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
