# OSRS Toolkit — web

The web front end for OSRS Toolkit. Sign in with Discord, paste the pairing token the sync
service gave you, and your Journal reads and writes through here instead of a desktop
installer.

Sits beside [osrs-toolkit-sync-server](https://github.com/Wolklaw/osrs-toolkit-sync-server)
(the plugin's endpoint) and depends on
[OSRS-Toolkit](https://github.com/Wolklaw/OSRS-Toolkit) itself for the domain layer —
`journal.py`, `ranking.py`, `performance.py` and the rest, none of which know this app exists
and none of which need PySide6 installed to run headless on a server.

## Pages

| Page | What it's for |
|---|---|
| `/` | Dashboard: open positions, live Grand Exchange slots, net worth, and where a position is updated with what it really filled at. |
| `/flips` | GE Flipper — a portfolio sized to your cash and free slots, plus the ranked field behind it. Track or watch a row from here. |
| `/watchlist` | Items you are following, priced after tax. |
| `/alch` `/skilling` | High Alchemy candidates, and skilling methods priced off the live market. |
| `/trades` | Trade Journal — flips recorded by hand. |
| `/activity` | The raw feed of what the plugin has sent. |
| `/buy-limits` | What is left of each item's four-hour limit. |
| `/performance` | Realized results by strategy and item, and plan versus actual. |
| `/pvm` `/savings-goal` | Boss checklists against your synced loadout; one savings target. |
| `/item/{id}` | One item's full market breakdown and price history. |
| `/profile` | Account, pairing token, characters, data export, theme. |
| `/about` `/help` | What this is and what it holds; setup, FAQ, and how to get in touch. **Both render signed out** — they answer the questions asked before signing in. |

Every page shares one shell (`templates/base.html`): sidebar navigation, a price-freshness
pill that also forces a refresh, the character switcher, and a light/dark/system theme toggle
kept in the browser rather than on the server.

## Running it

```bash
python -m venv .venv
.venv/bin/pip install -e "../OSRS Toolkit"   # the domain layer, without its gui extra
.venv/bin/pip install -e ".[dev]"
python -m toolkit_web
```

Listens on `127.0.0.1:8100` by default and speaks plain HTTP — put a tunnel or reverse proxy in
front of it for anything reachable from outside the machine, the same as the sync service.

For a real deployment beside the sync service, see **[docs/deploying.md](docs/deploying.md)**:
how the laptop is actually set up, how to update it, and the settings
that quietly break it if missed — `TOOLKIT_SESSION_SECRET`, and pulling *both* repositories.

### Settings

| Variable | Default | |
|---|---|---|
| `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` | unset | From a Discord application — see below |
| `DISCORD_REDIRECT_URI` | `https://runescope.app/auth/discord/callback` | Must match the Discord application exactly |
| `TOOLKIT_SESSION_SECRET` | random on every start | Set this in production, or every restart signs everyone out |
| `TOOLKIT_DATA_DIR` | `data` | Where the accounts database and every user's journal live |
| `TOOLKIT_SYNC_BASE_URL` | `https://sync.runescope.app` | The plugin's sync service |
| `TOOLKIT_DEV_LOGIN` | `false` | See below |

### Signing in without a Discord application yet

Setting `TOOLKIT_DEV_LOGIN=true` adds `POST /auth/dev-login {"discord_user_id", "username"}`,
which issues a real session for whichever id is named — no Discord involved at all. It exists
so this app can be built, tested and demonstrated before a Discord application exists to
authenticate against.

**Must never be set where the service is reachable by anyone else.** It hands a session to
anyone who can reach the endpoint, for any account they name.

### Getting a real Discord application

Only the account holder can do this part — it needs a login at
[discord.com/developers/applications](https://discord.com/developers/applications):

1. New Application, any name.
2. OAuth2 → add a redirect URL matching `DISCORD_REDIRECT_URI` exactly, protocol and path
   included.
3. OAuth2 → copy the Client ID and (Reset Secret, then) Client Secret into the environment.

No other scopes or bot permissions are needed — this only ever asks Discord for `identify`, a
user id, a username and an avatar hash.

## How a page stays current

There is no background sync worker. Every read of the Journal, positions, or the loadout first
asks the sync service for anything new and imports it, then reads the user's own database — see
`toolkit_web/importing.py` for why that trade-off was made over keeping a scheduler alive.

## API

Interactive docs at `/docs` once running. Every `/api/*` route needs a signed-in session;
`/api/pairing-token` is how an account attaches to a RuneLite plugin's sync token.

## Development

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .
```
