# Deploying on the same laptop as the sync service

This assumes [osrs-toolkit-sync-server](https://github.com/Wolklaw/osrs-toolkit-sync-server) is
already running there behind a Cloudflare Tunnel — its own
[docs/deploying.md](https://github.com/Wolklaw/osrs-toolkit-sync-server/blob/main/docs/deploying.md)
covers the tunnel, keeping the laptop awake, and the two Cloudflare settings that will
otherwise break things. Everything here is what the web front end adds on top: a second
service on the same machine, and a second hostname on the same tunnel.

## 1. Two repositories, not one

This is the step that is easy to get wrong, and it fails at import time rather than at runtime.

The web app has no domain layer of its own. It installs
[OSRS-Toolkit](https://github.com/Wolklaw/OSRS-Toolkit) as an editable dependency and imports
`journal.py`, `ranking.py`, `performance.py`, `sync_source.py` and the rest from there. Both
repositories have to be on the laptop, and **both have to be current** — a web app pulled to
today's commit against a desktop checkout from last week dies on an `ImportError` for a module
that did not exist yet, before it serves a single request.

```bash
sudo mkdir -p /opt && cd /opt
git clone https://github.com/Wolklaw/OSRS-Toolkit.git osrs-toolkit
git clone https://github.com/Wolklaw/osrs-toolkit-web.git osrs-web

cd /opt/osrs-web
python3 -m venv .venv
.venv/bin/pip install -e /opt/osrs-toolkit   # the domain layer, without its gui extra
.venv/bin/pip install -e .
```

Installing the desktop repo *without* `[gui]` is deliberate: nothing the web app touches needs
PySide6, and a headless laptop should not be installing Qt to run a web server.

Check the import chain before going any further, because this is the failure that looks like a
mystery later:

```bash
/opt/osrs-web/.venv/bin/python -c "from toolkit_web.app import create_app; print('ok')"
```

## 2. Secrets, in a file the unit does not carry

Unit files are world-readable. The Discord client secret and the session secret are not, so
they go in `/etc/osrs-web.env`, owned by root and mode 600:

```ini
TOOLKIT_SESSION_SECRET=<64 random characters>
DISCORD_CLIENT_ID=<from the Discord application>
DISCORD_CLIENT_SECRET=<from the Discord application>
TOOLKIT_SYNC_INVITE_CODE=<the sync service's SYNC_INVITE_CODE, while pairing is gated>
```

```bash
sudo install -m 600 /dev/null /etc/osrs-web.env
sudo -e /etc/osrs-web.env
```

**`TOOLKIT_SESSION_SECRET` is the one that must not be skipped.** Left unset, the app generates
a fresh one on every start — which is fine on a laptop you are developing on, and means every
restart and every deploy signs out every user in production.

`TOOLKIT_SYNC_INVITE_CODE` has to match the sync service's own `SYNC_INVITE_CODE` for as long
as pairing is invite-gated. It is what lets **Get a token** on the profile page mint a pairing
token on a signed-in visitor's behalf. Mismatched, that button returns a 502 and nothing else
explains why.

## 3. The service

`/etc/systemd/system/osrs-web.service`:

```ini
[Unit]
Description=OSRS Toolkit Web
After=network-online.target osrs-sync.service
Wants=network-online.target

[Service]
User=osrsweb
WorkingDirectory=/opt/osrs-web
EnvironmentFile=/etc/osrs-web.env
Environment=TOOLKIT_DATA_DIR=/var/lib/osrs-web
Environment=TOOLKIT_HOST=127.0.0.1
Environment=TOOLKIT_PORT=8100
Environment=DISCORD_REDIRECT_URI=https://runescope.app/auth/discord/callback
# Straight to the sync service over loopback rather than out through Cloudflare and back in.
# See the note below — this is not only about saving a round trip.
Environment=TOOLKIT_SYNC_BASE_URL=http://127.0.0.1:8000
ExecStart=/opt/osrs-web/.venv/bin/python -m toolkit_web
Restart=always
RestartSec=5

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/osrs-web

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd --system --no-create-home osrsweb
sudo mkdir -p /var/lib/osrs-web && sudo chown osrsweb /var/lib/osrs-web
sudo systemctl enable --now osrs-web
curl -s -o /dev/null -w '%{http_code}\n' localhost:8100/
```

`TOOLKIT_DEV_LOGIN` must never appear in any of this. It issues a real session for whichever
account id the caller names, with no Discord involved — it exists so the app can be built and
demonstrated before a Discord application exists, and on a reachable host it is a way for
anyone to sign in as anyone.

### Why the sync URL is loopback here

The default is `https://sync.runescope.app`, which is right for a desktop app on someone else's
machine and wrong for a service sitting next to the sync server. Going out through the tunnel
and back in costs a round trip on every page render — the web app asks the sync service for new
events before every read — and it puts a *Python HTTP client* in front of Cloudflare, which is
exactly the traffic Bot Fight Mode is built to challenge. Loopback avoids both. It also means
the front end keeps working if the tunnel drops.

## 4. The hostname

Same tunnel, one more ingress rule. Order matters: cloudflared takes the first hostname that
matches, so the catch-all stays last.

```bash
cloudflared tunnel route dns runescope runescope.app
```

`/etc/cloudflared/config.yml`:

```yaml
tunnel: runescope
credentials-file: /root/.cloudflared/<tunnel-id>.json

ingress:
  - hostname: sync.runescope.app
    service: http://127.0.0.1:8000
  - hostname: runescope.app
    service: http://127.0.0.1:8100
  - service: http_status:404
```

```bash
sudo systemctl restart cloudflared
curl -s -o /dev/null -w '%{http_code}\n' https://runescope.app/
```

### Do not turn on "Cache Everything"

Every signed-in page here is account-specific — one visitor's dashboard, positions and journal.
Cloudflare's default cache level only caches static extensions, so out of the box this is safe.
A page rule or cache rule that caches HTML for `runescope.app` would not be: it can serve one
account's dashboard to another account, which is a data leak rather than a stale page.

Unlike the sync service, this app does not currently send `Cache-Control: no-store` of its own,
so nothing in the response would stop such a rule. Leave caching off for this hostname.

## 5. The Discord application

Only the account holder can do this part —
[discord.com/developers/applications](https://discord.com/developers/applications):

1. New Application, any name.
2. OAuth2 → add a redirect URL matching `DISCORD_REDIRECT_URI` **exactly**, protocol and path
   included: `https://runescope.app/auth/discord/callback`.
3. OAuth2 → copy the Client ID and (Reset Secret, then) Client Secret into `/etc/osrs-web.env`.

No other scopes and no bot permissions. This only ever asks Discord for `identify` — a user id,
a username and an avatar hash.

Until that exists, the site is still usable: the landing page offers a recovery-code account,
which needs no identity provider at all.

## 6. Updating it

There is no auto-deploy and nothing pulls on boot — a reboot restarts the services against
whatever code is already on disk. Both repositories, then the restart:

```bash
cd /opt/osrs-toolkit && git pull
cd /opt/osrs-web && git pull && sudo systemctl restart osrs-web
```

Editable installs mean a pull is usually enough. Re-run
`.venv/bin/pip install -e /opt/osrs-toolkit && .venv/bin/pip install -e .` only when a
`pyproject.toml` gained a dependency.

If the desktop repo's pull brings nothing and the web app then fails to start, check which
branch that checkout is on. It should be `main`.

## 7. What is worth backing up

`/var/lib/osrs-web/journals/` — one SQLite database per account, and the only thing here that
cannot be recreated. `accounts.db` beside it costs a re-login and a re-pasted pairing token if
it is lost, nothing more. `market-cache/` is disposable; it refetches.

```bash
sudo sqlite3 /var/lib/osrs-web/accounts.db 'select count(*) from account;'
```

## Checking on it

```bash
systemctl status osrs-web osrs-sync cloudflared
journalctl -u osrs-web -f
curl -s -o /dev/null -w '%{http_code}\n' https://runescope.app/
```

The header's price pill on any signed-in page reports how long ago this server last read the
wiki's prices, which is the quickest confirmation that outbound network from the laptop is
working at all.
