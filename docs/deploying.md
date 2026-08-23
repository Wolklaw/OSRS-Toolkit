# How this runs on the laptop

A record of the machine as it is actually set up (`wolkhost`, reachable over SSH on the LAN),
not a clean-room recipe. The sync service's own
[docs/deploying.md](https://github.com/Wolklaw/osrs-toolkit-sync-server/blob/main/docs/deploying.md)
covers the Cloudflare Tunnel and the account-level settings; this covers the web front end
sitting beside it and, mostly, how to update it without breaking anything.

## The shape of it

Everything runs as the `wolklaw` user — **systemd user units**, not system units, so every
command below is `systemctl --user` and there is no `sudo` anywhere in normal operation.

| | |
|---|---|
| Web app | `~/osrs-web`, unit `toolkit-web.service`, listening on `127.0.0.1:8100` |
| Domain layer | No checkout of its own — pip installs it from the commit pinned in `pyproject.toml` |
| Sync service | `~/osrs-sync`, unit `osrs-sync.service`, listening on `127.0.0.1:8000` |
| Tunnel | `cloudflared.service`, config at `~/.cloudflared/config.yml` |
| Web app data | `~/.local/share/toolkit-web` — `accounts.db`, `journals/`, `market-cache/` |

Linger is enabled for the user (`loginctl show-user wolklaw -p Linger`), which is what makes
user units start at boot with nobody logged in. Without it none of this would come back after a
reboot until someone signed in at the console.

The tunnel already routes both hostnames, most specific first, catch-all last:

```yaml
ingress:
  - hostname: sync.runescope.app
    service: http://127.0.0.1:8000
  - hostname: runescope.app
    service: http://127.0.0.1:8100
  - service: http_status:404
```

## Updating it

**A reboot does not deploy anything.** Linger means the services come back on their own, but
they come back running whatever code is already on disk. Nothing pulls from GitHub on boot.

```bash
ssh wolkhost
cd ~/osrs-web && git pull --ff-only
systemctl --user restart toolkit-web
```

This repo is an editable install, so a pull is normally the whole deploy. Re-run
`~/osrs-web/.venv/bin/pip install -e .` when `pyproject.toml` changed at all — that now includes
the pinned domain layer, not just the third-party dependencies.

### When the domain layer moved too

The web app has no domain layer of its own — it imports `journal.py`, `ranking.py`,
`sync_source.py` and the rest from `osrs-toolkit`, which pip installs from the commit pinned in
`pyproject.toml`. There is no second checkout on the laptop to keep in step, and no branch to
get wrong. A domain-layer change reaches production by bumping that pin and pulling here:

```bash
cd ~/osrs-web && git pull --ff-only
~/osrs-web/.venv/bin/pip install -e . --upgrade
systemctl --user restart toolkit-web
```

`--upgrade` matters: without it pip may keep the domain layer it already has even though the
pinned commit in `pyproject.toml` moved.

A web app pulled forward against a stale domain layer fails at import, not at runtime — the
service enters a restart loop and no page is served at all. Worth one check before restarting:

```bash
~/osrs-web/.venv/bin/python -c "from toolkit_web.app import create_app; print('ok')"
```

### Confirming it worked

```bash
systemctl --user is-active toolkit-web
journalctl --user -u toolkit-web --since "2 min ago" --no-pager | grep -iE "error|traceback"
curl -s -o /dev/null -w '%{http_code}\n' localhost:8100/
```

and from anywhere:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://runescope.app/
```

## Configuration

The web app's settings are `Environment=` lines in
`~/.config/systemd/user/toolkit-web.service`, which is mode 600 and owned by `wolklaw` — the
Discord client secret and the session secret sit there in plain text, which is acceptable at
that permission but is the reason not to paste that file anywhere. `systemctl --user
daemon-reload` after editing, then restart.

Three of them matter more than the rest:

- **`TOOLKIT_SESSION_SECRET`** — must stay fixed. Unset, the app generates a new one on every
  start, and every restart signs out every user.
- **`TOOLKIT_SYNC_INVITE_CODE`** — must match the sync service's own `SYNC_INVITE_CODE` for as
  long as pairing is invite-gated. It is what lets **Get a token** on the profile page mint a
  pairing token. Mismatched, that button returns a 502 and nothing else explains why.
- **`TOOLKIT_DEV_LOGIN`** — must never be set here. It issues a real session for whichever
  account id the caller names, with no Discord involved.
- **`TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET_KEY`** — the captcha on account creation without
  Discord. Set both, or neither: the site key draws the widget and the secret enforces it, and
  a site key with no secret is a box that does nothing while a secret with no site key is a
  check nobody can pass. From **dash.cloudflare.com → Turnstile**, widget hostname
  `runescope.app`.

### Why the sync URL is loopback

`TOOLKIT_SYNC_BASE_URL` is `http://127.0.0.1:8000`, not the `https://sync.runescope.app`
default. Both services are on this machine, so the default sent every request out through
Cloudflare and back in — the web app asks the sync service for new events before each journal
read, making it a round trip per page render, and it put a Python HTTP client in front of
Cloudflare, which is the traffic Bot Fight Mode exists to challenge. Loopback avoids both and
keeps the front end working if the tunnel drops.

The default is still right for anything running off this machine, which is why it is the
default — this override belongs to this deployment, not to the app.

## The Discord application

[discord.com/developers/applications](https://discord.com/developers/applications) → OAuth2.
The redirect URL registered there must match `DISCORD_REDIRECT_URI` exactly, protocol and path
included: `https://runescope.app/auth/discord/callback`. Only the `identify` scope is ever
requested — a user id, a username and an avatar hash. Nothing else, no bot permissions.

Sign-in also works with no Discord application at all: the landing page offers a recovery-code
account, which needs no identity provider — and is the one path a script could walk unaided,
which is why it carries the Turnstile challenge above.

### The download page needs outbound GitHub

`/download` reads the latest desktop release from `api.github.com`, cached for an hour, and
falls back to a plain link at the releases page when it cannot. Unauthenticated, so it spends
one of the 60 calls an hour this address gets — nothing else here touches that budget. A
blocked outbound GitHub costs the page its version number and its two direct download buttons,
nothing more.

## Cloudflare: do not turn on "Cache Everything"

Every signed-in page is account-specific. The default cache level only caches static
extensions, so this is safe as it stands — but a page rule or cache rule that caches HTML for
`runescope.app` could serve one account's dashboard to another. The app does not currently send
`Cache-Control: no-store` of its own, so nothing in the response would prevent it.

## Backups

`~/.local/share/toolkit-web/journals/` — one SQLite database per account, and the only thing
here that cannot be recreated. `accounts.db` beside it costs a re-login and a re-pasted pairing
token. `market-cache/` is disposable.

## Operational notes

- **Sleep is disabled, deliberately.** `sleep.target`, `suspend.target`, `hibernate.target`
  and `hybrid-sleep.target` are masked, and `/etc/systemd/logind.conf` sets
  `HandleLidSwitch=ignore` and `HandleLidSwitchExternalPower=ignore`. Before that was done the
  machine really did suspend — twice on 2026-08-22, for 7 seconds and 2m46s — and a suspend
  freezes cloudflared's outbound connection, so both hostnames go dark until it wakes. If a
  future reinstall loses this, that is the outage to suspect first.

- **Secrets sit in the unit file.** Mode 600 and owned by `wolklaw`, so this is acceptable —
  but it is the reason not to paste that file anywhere, and the reason a rotation means editing
  it rather than a config file somewhere friendlier.

- **No auto-deploy.** Every update is the manual pull above.
