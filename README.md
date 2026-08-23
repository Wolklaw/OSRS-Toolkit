# OSRS Toolkit — web

The web front end for OSRS Toolkit. Sign in with Discord, paste the pairing token the sync
service gave you, and your Journal reads and writes through here — from any device, nothing to
install.

The desktop app is not an alternative to this; it is a companion. Mint a desktop access token
on `/profile` and paste it into the desktop app's Settings, and its journal mirrors this one:
a trade recorded in either place reaches the other within a minute. Not connected, it stays
exactly what it always was — a local app with a local journal.

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
| `/profile` | Account, pairing token, desktop access tokens, characters, data export, theme. |
| `/download` | The Windows desktop app: current version and both builds, read from the GitHub release. **Renders signed out** — whether to use the site or the app is a question asked before signing up. |
| `/about` `/help` | What this is and what it holds; setup, FAQ, and how to get in touch. **Both render signed out** — they answer the questions asked before signing in. |

Every page shares one shell (`templates/base.html`): sidebar navigation, a price-freshness
pill that also forces a refresh, the character switcher, and a light/dark/system theme toggle
kept in the browser rather than on the server.

## Running it

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
python -m toolkit_web
```

That is the whole setup. The domain layer comes down with it — `pyproject.toml` pins
`osrs-toolkit` to a commit in its own repository, so there is nothing to clone by hand and no
way to end up with a checkout that imports a package that was never installed.

If you are changing the domain layer and this app together, install the sibling editable
afterwards and it takes precedence for the rest of that session:

```bash
.venv/bin/pip install -e "../OSRS Toolkit"
```

When that work lands, bump the pinned commit in `pyproject.toml` — that is the one step that
makes the change real for everyone else.

Listens on `127.0.0.1:8100` by default and speaks plain HTTP — put a tunnel or reverse proxy in
front of it for anything reachable from outside the machine, the same as the sync service.

For a real deployment beside the sync service, see **[docs/deploying.md](docs/deploying.md)**:
how the laptop is actually set up, how to update it, and the settings
that quietly break it if missed — `TOOLKIT_SESSION_SECRET`, and reinstalling after the pinned
domain layer moves.

### Settings

| Variable | Default | |
|---|---|---|
| `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` | unset | From a Discord application — see below |
| `DISCORD_REDIRECT_URI` | `https://runescope.app/auth/discord/callback` | Must match the Discord application exactly |
| `TOOLKIT_SESSION_SECRET` | random on every start | Set this in production, or every restart signs everyone out |
| `TOOLKIT_DATA_DIR` | `data` | Where the accounts database and every user's journal live |
| `TOOLKIT_SYNC_BASE_URL` | `https://sync.runescope.app` | The plugin's sync service |
| `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET_KEY` | unset | Cloudflare Turnstile — see below |
| `TOOLKIT_DEV_LOGIN` | `false` | See below |

### The captcha on account creation

Creating an account without Discord costs nothing — no login somewhere else, no mailbox, no
confirmation step — so that one route is gated on a
[Cloudflare Turnstile](https://developers.cloudflare.com/turnstile/) challenge. Discord sign-in
is already somebody else's identity check, and signing back in with a recovery code needs a
code that was issued to a real person, so neither is gated.

Get the keys from **dash.cloudflare.com → Turnstile → Add widget**, hostname `runescope.app`.
The widget is drawn only where `TURNSTILE_SITE_KEY` is set, and the token is demanded only
where `TURNSTILE_SECRET_KEY` is set — the two are read independently, so an environment with
neither (local, tests) skips the challenge on both sides rather than drawing a box that can
never be satisfied. Set both together in production.

Cloudflare publishes [test keys](https://developers.cloudflare.com/turnstile/troubleshooting/testing/)
for exercising this by hand: site key `1x00000000000000000000AA` and secret
`1x0000000000000000000000000000000AA` pass everything, and the `2x...` pair fails everything.

If Cloudflare cannot be reached at all, creation answers 503 rather than either minting an
account on an answer that never arrived or telling somebody they failed a check they passed.

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

## Outbound calls

Three, all of them read-only and none of them on the hot path of a signed-in page:
the OSRS Wiki for prices, the sync service for pending plugin events, and — for `/download`
only — `api.github.com` for the latest desktop release, cached for an hour. Turnstile adds a
fourth, on account creation only. A failure of any of them costs a page its freshness or one
button, never the page itself.

## How a page stays current

There is no background sync worker. Every read of the Journal, positions, or the loadout first
asks the sync service for anything new and imports it, then reads the user's own database — see
`toolkit_web/importing.py` for why that trade-off was made over keeping a scheduler alive.

## API

Interactive docs at `/docs` once running. Every `/api/*` route needs a signed-in session or a
bearer desktop access token; `/api/pairing-token` is how an account attaches to a RuneLite
plugin's sync token, and `/api/desktop-tokens` is how it mints and revokes the desktop app's
own credential — a different one, since a leaked plugin token should not also read a whole
journal. `/api/journal/version`, `/pull` and `/push` are what the desktop app mirrors through.

## Development

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .
```
