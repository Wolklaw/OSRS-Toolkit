# CLAUDE.md

Orientation for anyone — person or assistant — picking this repository up cold. What it is
today, how to run it, the conventions it holds itself to, and where it is going next.

The roadmap at the bottom is the part worth reading twice: **it assumes no change to the
RuneLite plugin.** Everything in it is built from data the plugin already sends. The plugin has
a roadmap of its own, and the compliance envelope it has to stay inside, in
[`docs/plugin-roadmap.md`](docs/plugin-roadmap.md); neither depends on the other.

---

## What this repository is

OSRS Toolkit is a market-analysis and trade-tracking companion for Old School RuneScape. It
plans Grand Exchange flips against your actual cash and free slots, keeps a journal of every
position from offer to payout, and afterwards tells you which of those plans were worth
following. It also covers High Alchemy, skilling profit, boss-readiness checklists, loot and
death logs, and net worth over time.

**Nothing in it plays the game.** It reads prices, it reads what the plugin reports, and it
writes a journal. That boundary is a product promise, not an implementation detail.

Since 2026-10-20 this one repository holds three things that used to be two:

| Path | What it is |
|---|---|
| `src/osrs_toolkit/` | The **domain layer** — journal, ranking, performance, calculators, market, PvM, sync parsing. Qt-free, importable on a headless server. Also the PySide6 desktop window (`app.py`, `item_details.py`). |
| `web/` | **runescope.app** — FastAPI + Jinja front end (`toolkit_web`), the product's primary surface. |
| `runelite-plugin/` | A gitignored clone of the plugin repo, for convenience. Not part of this repo's tree. |

Both halves install editable from this one checkout, so one commit changes both and there is
no version pin between them. That merge replaced a commit-pinned git dependency which had been
bumped eleven times in eight weeks, and whose staleness silently ran the website on older
domain code than the desktop had.

### The repo family

Four repositories plus a fork, all load-bearing. Do not propose consolidating them.

- **OSRS-Toolkit** (public) — this one. Domain layer, desktop app, web app, and
  [`docs/sync-api.md`](docs/sync-api.md), the endpoint contract the plugin builds against.
- **osrs-toolkit-sync-server** (public) — the plugin's HTTP endpoint. Public *on purpose*: it
  is the verification story that makes the plugin acceptable to the RuneLite Plugin Hub.
- **osrs-toolkit-runelite** (public) — the plugin, live on the Plugin Hub since 2026-08-25.
  Its roadmap and the rules that bind it are [`docs/plugin-roadmap.md`](docs/plugin-roadmap.md),
  kept here rather than there so a reviewer never has to read past it.
- **osrs-toolkit-discord-bot** (private) — `/status`, `/price`, `/hiscores`, `/wiki`, plus
  moderation. Account-unaware by design.
- A **plugin-hub fork**, whose branch carries the submission manifest. Keep it.

The plugin repo cannot be merged in: the Hub manifest is `repository=` + `commit=` and builds
from the repo *root*, so a plugin in a subdirectory cannot be submitted at all.

### How data moves

```
RuneLite plugin  --HTTPS-->  sync.runescope.app  --loopback-->  runescope.app  --token-->  desktop app
   (events)                  (a queue, 30d max)                 (the journal)             (mirror)
```

The desktop app never reads the sync service or the `.runelite` folder directly — always
`WebAppSource` against runescope.app, authenticated by a desktop token minted on `/profile`.
`LocalFileSource` is no longer reachable from the running app; that was decided deliberately,
twice. Don't re-propose it "for people on an older plugin".

The sync service is a **queue, not an archive**. Events are deleted on ack and hard-expire at
30 days. The journal is the archive.

---

## Running it

Both halves from the repository root, not from `web/`:

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]" -e "web[dev]"
```

The web app:

```powershell
.venv\Scripts\python -m toolkit_web
```

Listens on `127.0.0.1:8100`, plain HTTP. Needs `TOOLKIT_SESSION_SECRET` set in production or
every restart signs everyone out; see [`web/README.md`](web/README.md) for the rest of the
environment and [`web/docs/deploying.md`](web/docs/deploying.md) for how the host is actually
set up.

The desktop window:

```powershell
.venv\Scripts\python -m osrs_toolkit
```

### Tests and lint

Two suites, two roots. The root suite needs PySide6; the web suite must never.

```powershell
.venv\Scripts\python -m pytest tests
```

```powershell
.venv\Scripts\python -m pytest web\tests
```

```powershell
.venv\Scripts\python -m ruff check .
```

### Building a Windows release

```powershell
.\build-release.ps1
```

Runs the suite, lints, regenerates the icon and installer artwork, builds with Nuitka, and
writes the portable zip into `release/` — plus the setup wizard if Inno Setup 6 is installed.
**Invoke it with no stream redirection at all** — PowerShell 5.1 wraps a native command's
stderr in error records and falsely reports failure.

---

## Conventions this repo actually holds to

- **Commits carry Wolklaw's name only.** No `Co-Authored-By` trailer, in any repository.
- **No comments and no docstrings.** The code carries no prose. Name things so the reason
  is in the name, the type and the test, and put the rest where it cannot go stale: the
  commit message, [`CHANGELOG.md`](CHANGELOG.md), or a file under `docs/`. Two exceptions,
  because they instruct tooling rather than explain anything to a person — keep `# noqa`
  and `# type:` exactly where they are. This replaced a why-not-what rule on 2026-10-24;
  4,500 lines of comment and docstring came out of the Python and another 140 out of the
  templates in that pass, so do not reintroduce them a few at a time.
- **Backward-compatible SQLite migrations, always.** Real users' trading data is in these
  files. `_add_missing_columns` and the backfill `UPDATE`s in `journal.py` are the pattern:
  add columns, backfill, never drop.
- **A hard data-flow requirement is fixed in code, not explained away as configuration.**
- **PySide6 never enters the web environment.** It is the root project's `gui` extra and
  nothing under `web/` asks for it. A domain module that imports Qt has broken the website.
- **Deploy order is: push to GitHub, then touch the host.** The host deploys by pulling. The
  reverse order took runescope.app down once. The tell for a failed install is the *absence*
  of a `Collecting` line in pip's output, not the exit code.
- **A version bump is not done until the user can install it.**
- Ruff is pinned, not floating, in both `pyproject.toml` files. Raise it deliberately.
- Line length 100, target `py312`.

---

## The data we have

This is the inventory the roadmap is built from. Every item here is **already arriving** — the
plugin sends it, `runelite_sync.py` parses it, and in most cases `journal.py` stores it.

Event types, fixed at seven (`parse_sync_event`):

| Event | What it carries |
|---|---|
| `ge_fill` | Item, side, filled quantity, gross coins moved (tax comes off downstream, once), and in `metadata`: `offer_id`, `offer_slot`, `offer_price`, `offer_state`, `total_quantity` |
| `ge_offer_opened` | Item, side, offer price, total quantity, whether it was restored on login |
| `ge_offer_cancelled` | The same fields, matched to its opening by size and price |
| `player_trade` | Counterparty display name, every item and coin given and received, unit values |
| `loadout_snapshot` | Equipment, inventory, and up to 1,200 bank stacks with names and values, plus **skill levels and experience**. Fired on bank open, skipped when nothing changed. |
| `npc_loot` | NPC name and every stack it dropped, with values |
| `player_death` | Equipment and inventory at the moment of death, and whether you were skulled |

Plus live current-state, replaced rather than queued: the eight GE slots (item, offer price,
total and filled quantity, coins spent, state) and which item and side the offer box is open
on.

Stored, per account, in each user's own SQLite journal:

- `synced_trades` + `synced_trade_items` — GE fills and player trades, item-level
- `tracked_trades` + `tracked_buy_fills` + `tracked_sale_fills` — positions, plan and actual
- `npc_loot_events` + `npc_loot_items`, `player_death_events` + `player_death_items`
- `loadout_snapshots` — one row per character, overwritten; the newest-snapshot cache
- `loadout_snapshot_history` — **append-only**, one row per `(account_hash, captured_at)`,
  thinned on a ladder with age
- `net_worth_history` — a time series of one number, total value
- `skills_history` — a time series of levels and experience

And from the OSRS Wiki: latest, 5-minute and 1-hour prices, item mappings with buy limits, and
roughly 75 days of six-hour average price and volume history per item.

### What we already do with it

`/` dashboard (positions, live slots, net worth chart and composition, session summary),
`/flips`, `/watchlist` with price alerts to a Discord webhook, `/trades`, `/activity`,
`/counterparties`, `/loot-log` (90-day rollup with observed gp/hr per NPC), `/death-log`,
`/performance` (by strategy, by item, plan vs. actual), `/session`, `/loadout`, `/pvm`,
`/buy-limits`, `/alch`, `/skilling`, `/savings-goal`, `/item/{id}`.

### What we are throwing away

Worth stating plainly, because it is the reason Phase 0 exists:

1. **When an offer was placed.** `applied_offer_events` keeps `(event_id, applied_at)` and
   nothing else; the opened moment, its price and its quantity are discarded once applied. And
   `tracked_buy_fills` / `tracked_sale_fills` carry **no timestamp at all**. So the time
   between placing an offer and it filling — the single most useful number a flipper can have —
   is recoverable only roughly, from a position's `created_at`, and not at all for an offer
   that was cancelled before it created one. Both ends of it already arrive.
2. **The market context of a fill.** When a fill lands we know the 5m and 1h prices at that
   moment and keep none of them, so a recorded fill price can never afterwards be read as
   "1h low +0.4%".

---

## Roadmap

Ordered. Each phase is useful on its own and unlocks the next. **No plugin change anywhere in
this list.**

### Phase 0 — stop discarding the data (prerequisite)

Three schema changes in `src/osrs_toolkit/journal.py`, additive and backward-compatible.
Nothing user-visible ships in this phase; everything below depends on it, so the sooner it
lands the sooner history starts accumulating.

- **Append-only loadout snapshots.** *Landed 2026-10-21.* History went into its own
  `loadout_snapshot_history` table, uniquely keyed on `(account_hash, captured_at)`, rather
  than `loadout_snapshots` re-keyed: SQLite can only change a primary key by rebuilding the
  table and renaming over the original, and these files hold real trading data. So
  `loadout_snapshots` stays the newest-row cache every existing reader already uses and PvM
  Readiness is untouched, existing journals are seeded from the one row they had, and
  `list_loadout_snapshots` reads the history back within a window. Pruned on a ladder —
  everything for 30 days, the newest of each day out to a year, then the newest of each week.
  Item-level deltas against the previous snapshot are still the fallback if size becomes
  real.
- **Retain the offer lifecycle.** A real table for openings and cancellations, with price,
  quantity, side and `restored`, not just a dedupe id. Add `filled_at` to both fill tables.
- **Stamp market context onto a fill.** The 5m and 1h point at the moment of import, stored
  beside the fill, so every fill becomes a labelled observation instead of a bare price.

### Phase 1 — the Flipper learns from your own journal

This is the thing no other tool can do, because nobody else holds your fill history.

- **Slippage** — the offer price against the effective filled price, by item and by strategy.
  This one needs nothing from Phase 0: every `ge_fill` already stores `offer_price` in its
  `metadata_json` beside the coins it actually moved, so it is a query away. Start here.
- **Observed fill time**, per item and per offset from the market price. "At the 1h low, this
  fills in about four minutes for you; a gp under, about forty." Needs Phase 0's timestamps —
  a position's `created_at` is a rough stand-in and silently misses everything cancelled.
- **A cancellation report** — what you abandon, how long you waited first, under which
  strategy. "You cancel two in five Overnight buys, median six minutes in" is a finding about
  whether the strategy fits how you actually play.
- **Feed it back into `rank_flips` / `plan_flip_portfolio`** as a per-account liquidity prior
  alongside volume and stability. Needs a minimum-observations floor and a clean fall back to
  today's heuristic — the same honesty `/performance` already applies by hiding items flipped
  once.

### Phase 2 — the trip ledger

What a PvM or skilling trip *actually* paid, after what it cost. Wiki gp/hr figures are
theoretical; this is yours.

- **Supplies consumed.** `session_summary()` already infers the session window from the gaps
  between loot, deaths, trades and changed snapshots. With snapshot history, diff the snapshots
  bracketing that window: brews, restores, runes, food, ammo, priced at their own value.
- **Trip P&L** = loot received − supplies burnt − what a death actually cost.
- **Death cost by inference.** `player_death` says what you were carrying. The next snapshot
  says what you still have. The difference is what genuinely did not come back — strictly
  better than simulating OSRS's protect/skull/wilderness rules, which the plugin deliberately
  refuses to guess at.
- **Per NPC**: net gp/hr rather than gross, cost per kill, kills per trip.
- **Drop tracking.** Kills and their item lists are already stored per event: observed rates,
  dry streaks, "n kills since the last one".

### Phase 3 — net worth attribution

`net_worth_history` records that your wealth changed and never why.

- **Decompose a change** into realized flip profit, loot in, deaths out, supplies burnt, and
  price movement on holdings you never traded. That last term is the one players never see and
  it is frequently the largest.
- **Idle capital** — stacks that have not moved in N days, what they are worth, and what they
  would make at today's margins.
- **Days of supply** — burn rate from the trip ledger against current stock. "Four days of
  nature runes left" is the restock warning nobody has.

### Phase 4 — alerts worth having

The cron endpoint (`/internal/check-alerts`), the Discord webhook and the 12-hour cooldown
already exist, and only price alerts use them. Everything above arms them with better
conditions:

- An offer sitting well past its own observed fill time.
- An ask the market has drifted away from — the dashboard flags this on screen; nobody is
  told while they are away.
- A consumable below N days of supply.
- An overnight set that has fully filled.
- A death logged while the app was closed.

A decision to make rather than assume: the Discord bot is account-unaware on purpose. Linking a
Discord id to an account would let `/session`, `/worth` and `/trip` answer in chat, and that is
a scope change worth agreeing to explicitly.

### Phase 5 — pooled market intelligence (opt-in, aggregate only)

Once more than a handful of accounts are syncing, pooled fill times and slippage measure
something the Wiki API does not publish: not how much traded, but **how likely an offer at a
given price is to fill, and how fast.** That is a genuine moat, and it needs no plugin change.

Non-negotiable if it is built: opt-in, aggregated across accounts before anything is served,
never per character, never a counterparty name, and written into `/about` *before* it ships.
This project's privacy story already inverted once when it moved to the web. Don't let it
happen quietly a second time.

### Phase 6 — keeping both front ends honest

Everything above belongs in `src/osrs_toolkit/`, Qt-free, so the website and the desktop window
both get it. The website leads and the desktop follows — the README already tells people that,
so the desktop must not be allowed to drift into a different answer for the same trade.

---

## Not on the roadmap

Here so it does not get re-proposed:

- **No new plugin event types**, and no plugin change, for anything above. The event set is
  fixed at the seven in the table. The plugin's own roadmap
  ([`docs/plugin-roadmap.md`](docs/plugin-roadmap.md)) does propose additions — none of the
  phases above may be made to wait on them.
- Nothing that automates gameplay, clicks interfaces, alters offers, or reads the client beyond
  what the plugin already sends.
- Restoring `LocalFileSource` as a fallback.
- Merging the plugin repo into this one, or consolidating the other repos.
- Making the sync service an archive. It is a queue; the journal is the archive.
- Code signing the Windows build. Deferred on purpose — SmartScreen is a separate problem from
  the antivirus false positives, and the web app sidesteps both.
