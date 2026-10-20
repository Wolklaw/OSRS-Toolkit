# How this runs on the laptop

A record of the machine as it is actually set up (`wolkhost`, `192.168.0.172` on the LAN),
not a clean-room recipe. The sync service's own
[docs/deploying.md](https://github.com/Wolklaw/osrs-toolkit-sync-server/blob/main/docs/deploying.md)
covers the Cloudflare Tunnel and the account-level settings; this covers the web front end
sitting beside it and, mostly, how to update it without breaking anything.

## The shape of it

Everything the site is made of runs as the `wolklaw` user — **systemd user units**, not system
units, so those commands are all `systemctl --user` and there is no `sudo` anywhere in normal
operation. The one exception is the Cockpit console, which is a system unit and needs sudo for
everything; it is nothing the site depends on, and it has its own section below.

| | |
|---|---|
| Web app | `~/osrs-web`, unit `toolkit-web.service`, listening on `127.0.0.1:8100` |
| Domain layer | No checkout of its own — pip installs it from the commit pinned in `pyproject.toml` |
| Sync service | `~/osrs-sync`, unit `osrs-sync.service`, listening on `127.0.0.1:8000` |
| Tunnel | `cloudflared.service`, config at `~/.cloudflared/config.yml` |
| Console | `cockpit.socket`, listening on `127.0.0.1:9090` — a **system** unit, see below |
| Web app data | `~/.local/share/toolkit-web` — `accounts.db`, `journals/`, `market-cache/` |

Linger is enabled for the user (`loginctl show-user wolklaw -p Linger`), which is what makes
user units start at boot with nobody logged in. Without it none of this would come back after a
reboot until someone signed in at the console.

The tunnel already routes all three hostnames, most specific first, catch-all last:

```yaml
ingress:
  - hostname: sync.runescope.app
    service: http://127.0.0.1:8000
  - hostname: cockpit.runescope.app
    service: http://127.0.0.1:9090
  - hostname: runescope.app
    service: http://127.0.0.1:8100
  - service: http_status:404
```

## Updating it

**A reboot does not deploy anything.** Linger means the services come back on their own, but
they come back running whatever code is already on disk. Nothing pulls from GitHub on boot.

```bash
ssh 192.168.0.172
cd ~/osrs-web && git pull --ff-only
systemctl --user restart toolkit-web
```

The hostname `wolkhost` is what the machine calls itself, not something every client resolves —
the Windows machine only has `192.168.0.172` in its `~/.ssh/config`, so that is what works from
there.

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
~/osrs-web/.venv/bin/pip uninstall -y osrs-toolkit
~/osrs-web/.venv/bin/pip install -e .
systemctl --user restart toolkit-web
```

**The uninstall is the step that matters, and `--upgrade` is not a substitute for it.** This
note used to say it was, and it is not: `osrs-toolkit` is a direct-URL requirement, so pip
checks whether *something* named `osrs-toolkit` is installed, sees that something is, and
leaves it alone — pinned commit or not. On 2026-08-23 that had left commit `d43bc56` installed
against a pin of `e0b8f72` for two commits' worth of deploys. Uninstalling first is what forces
the resolve.

Confirm the pin actually took, rather than trusting pip's summary line, which happily reports
success after changing nothing:

```bash
cat ~/osrs-web/.venv/lib/python3.14/site-packages/osrs_toolkit-*.dist-info/direct_url.json
```

The `commit_id` in there must match the pin in `pyproject.toml`.

**A stale domain layer does not fail at import.** This note used to claim it did, and that the
service would enter a restart loop and serve nothing — which would at least be obvious. What
actually happens is that every page renders fine and one endpoint 500s on a method that is not
there yet. The stale layer above cost `/api/journal/version` — the desktop app's once-a-minute
mirror poll — for as long as nobody read the log. The import check is still worth running, but
it proves less than it looks like it does:

```bash
~/osrs-web/.venv/bin/python -c "from toolkit_web.app import create_app; print('ok')"
```

### Confirming it worked

```bash
systemctl --user is-active toolkit-web
journalctl --user -u toolkit-web --since "2 min ago" --no-pager | grep -iE "error|traceback"
curl -s -o /dev/null -w '%{http_code}\n' localhost:8100/
```

The `journalctl` line is not optional garnish. A 200 on `/` says nothing about the routes the
desktop app uses, and those are the ones a half-applied domain-layer bump breaks — check the
main PID changed too, or you will read the old process's errors and think they are new ones.

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

## The Cockpit console

`cockpit.runescope.app` is the machine's web console — services, logs, storage, a terminal —
added 2026-08-23. It is behind Cloudflare Access, so an unauthenticated request never reaches
it: the edge answers with a redirect to a one-time-PIN login, and only then does the tunnel
open a connection to 9090.

It is also the one **system** service on this box. Everything else is a `systemctl --user` unit
with no sudo anywhere, so every command in this section is the exception to that rule.

| | |
|---|---|
| Packages | `cockpit`, `cockpit-storaged` |
| Unit | `cockpit.socket` (system), listening on `127.0.0.1:9090` |
| Loopback drop-in | `/etc/systemd/system/cockpit.socket.d/listen.conf` |
| Origins config | `/etc/cockpit/cockpit.conf` |
| Access application | Zero Trust → Access → Applications → `cockpit`, policy `Me` |

Two pieces of configuration are doing real work. The socket binds to `0.0.0.0:9090` out of the
box, which would put a root-capable login form on the LAN; the drop-in rebinds it to loopback,
and the empty `ListenStream=` is required — without it the new address is *added* to the
default rather than replacing it:

```ini
[Socket]
ListenStream=
ListenStream=127.0.0.1:9090
```

And `/etc/cockpit/cockpit.conf` tells cockpit what its public name is. Without the `Origins`
line the page loads and then the websocket handshake fails, which surfaces as a vague internal
error rather than anything pointing at this file. `AllowUnencrypted` is safe only because of
the line above it: TLS terminates at Cloudflare, and nothing but the tunnel can reach 9090.

```ini
[WebService]
Origins = https://cockpit.runescope.app wss://cockpit.runescope.app
ProtocolHeader = X-Forwarded-Proto
AllowUnencrypted = true
```

### Editing either of those means restarting the socket

`daemon-reload` on its own is not enough, and this is not obvious, because the unit goes on
reporting `active (listening)` on the right address while being bound to nothing at all. The
site 502s and `systemctl status` looks fine. The tell is in the journal:

```
cockpit.socket: Unit configuration changed while unit was running, and no socket file
descriptors are open. Unit not functional until restarted.
```

That happened on first install — `apt` starts the socket itself, before the drop-in exists. So:

```bash
sudo systemctl restart cockpit.socket
ss -lnt | grep 9090
```

The second line is the check that means anything. `127.0.0.1:9090` must actually appear.

### NetworkManager is disabled, and Cockpit is why

Netplan on this machine renders to `systemd-networkd`, which owns all three interfaces and
reports `Online state: online`. NetworkManager was installed and running but managed nothing —
every device `unmanaged`, global state `disconnected`. Nothing cared until PackageKit did:
Cockpit's **Software Updates** page asks PackageKit, PackageKit asks NetworkManager whether the
system is online, and the answer was no. The page failed with `Cannot refresh cache whilst
offline` on a machine that was plainly not offline.

`sudo systemctl disable --now NetworkManager` fixed it, and cost nothing, because it was not
managing the link this host is reached over. **PackageKit caches that answer at startup**, so
it needs restarting too or it keeps repeating the old one:

```bash
sudo systemctl restart packagekit
```

If a future reinstall brings NetworkManager back, this is the page that will break first.

**It came back once already.** `NetworkManager.service` was found `enabled` and `active` again
on 2026-08-24 — presumably re-enabled by a package update — breaking the Software Updates page
by exactly the mechanism above. Re-disabled the same day (see the housekeeping table below).
Worth re-checking after any round of updates, since nothing prevents a third time:

```bash
sudo systemctl disable --now NetworkManager
sudo systemctl restart packagekit
```

### Housekeeping done on 2026-08-24

An audit found several things this machine was carrying for no reason. All fixed the same day,
once `wolklaw` had passwordless sudo (see below) — the account didn't when the audit itself
was run, which is why "confirmed" and "fixed" have the same date.

| | Fixed by | |
|---|---|---|
| `kdump-tools` reserving 320MB | purge + `update-grub` + reboot | Verified after reboot four independent ways: `kexec_crash_size` reads `0`, `kexec_crash_loaded` reads `0`, no crash region in `/proc/iomem`, and `MemTotal` in `/proc/meminfo` gained back exactly ~320MB. (A *combined* check misreported the old 320MB figure once, from output interleaving in a multi-command SSH call — re-run alone, it read `0` like everything else. Worth knowing this file can look stale inside a big combined command.) |
| `open-vm-tools` | purge | Not a VM (`systemd-detect-virt` → `none`); the package did nothing here. |
| `ModemManager` | `disable --now` | No modem devices present. Not purged — it's a real dependency of `network-manager`, which stays installed, just disabled. |
| `open-iscsi`, `multipathd` | `disable --now` | Zero iSCSI sessions, no multipath storage. Not purged — both are pulled in by the `ubuntu-server` metapackage; disabling avoids an apt dependency cascade for no real benefit. |
| journald uncapped | `SystemMaxUse=200M` in `/etc/systemd/journald.conf` | Was 96MB against a ~9.8GB default ceiling. |
| `smbd` / `nmbd` | **kept, deliberately** | Not unused — Samba is how Claude reaches this machine's filesystem directly. See `project_wolkhost_passwordless_sudo` in the assistant's own memory for why. Do not propose removing it in a future pass. |
| `cloud-init` (5 units) | left as-is | Already `disabled` in its own config (subiquity turned it off at install) and holds no memory at steady state — the units are one-shots that already exited. Not worth the churn for a saving that doesn't exist. |

### Rate limiting is a Cloudflare-side job

There is none today. On 2026-08-23 the app served a burst of ~34,600 requests to `/` in five
minutes — that one turned out to be **local** (from `127.0.0.1`, no `X-Forwarded-For`, so not
tunnel traffic), and the app absorbed it without dropping a request or leaking memory. But
nothing would throttle the same thing arriving from outside. A rate-limiting rule on
`runescope.app` belongs in the Cloudflare dashboard; the origin cannot do it usefully, since
every request arrives from the tunnel.

Client IPs are already logged correctly — uvicorn honours the `X-Forwarded-For` cloudflared
sets, verified by forging one against loopback and watching it appear in the log.

### Phased updates break "Install all updates"

Cockpit lists an update as available whenever the archive has a newer version, but Ubuntu
phases some of them to a percentage of machines, and PackageKit refuses to install one this
host is not yet eligible for:

```
Package id 'python3-software-properties;0.120.1;all;ubuntu-resolute-updates-main'
is not available as an update candidate
```

One held-back package fails the whole batch, and the page offers no way to deselect it — the
buttons are **Install security updates** and **Install all updates**, nothing per-package. So
either use the security button, which is a smaller set and usually unphased, or upgrade over
SSH, where apt handles phasing properly and lists the held-back packages under *The following
packages have been kept back*:

```bash
sudo apt upgrade
```

`apt-cache policy <pkg>` names the reason — a phased package shows `(phased 10%)` beside its
candidate version. Waiting is the right response; forcing it with `apt install <pkg>` opts this
machine out of exactly the mechanism that catches regressions before they reach everyone.

One package must never be upgraded from Cockpit's own terminal: `cockpit` itself, which
restarts `cockpit-ws` and kills the session mid-transaction. Do that one over SSH.

## Cloudflare: do not turn on "Cache Everything"

Every signed-in page is account-specific. The default cache level only caches static
extensions, so this is safe as it stands — but a page rule or cache rule that caches HTML for
`runescope.app` could serve one account's dashboard to another. The app does not currently send
`Cache-Control: no-store` of its own, so nothing in the response would prevent it.

## What this machine has left to give

Measured 2026-08-24, with both services and the tunnel running:

| | |
|---|---|
| CPU | AMD Athlon II P340, 2 cores, ~2.2GHz — 2010-era |
| Load average | 0.06 / 0.04 / 0.00 |
| Memory | 3,334MB total, 656MB used, **2,678MB available** |
| Swap | 3.6GB, **0B used** — never touched since boot |
| The whole stack | cloudflared 41MB + sync service 58MB + web app 74MB = **173MB** |

So the constraint here is not memory. It is the CPU, and only for work that is actually
CPU-bound — everything running today is I/O-wait, which is why a fourteen-year-old dual core
sits at a load average of 0.00 serving a public site.

### Adding a Discord bot

Fits, with one thing to watch. A gateway bot holds a WebSocket open and reacts to events, so
it is idle almost all the time: a command-and-response bot costs roughly 60–90MB resident and
near-zero CPU. Against 2,678MB free that is not a close call, and disabling `kdump` above
would hand back another 320MB on its own.

The caveat is that this CPU is slow in absolute terms. A bot that answers `/price whip` from
the market cache is fine. A bot that renders images, does anything ML-shaped, or runs work
over every message in a busy guild is the kind of thing that will find the limit — not by
running out of memory, but by being noticeably slow at it.

Two things worth knowing before building one:

- **Alerts do not need a bot.** `discord_webhook.py` already posts outbound messages, and
  `/internal/check-alerts` already drives them. A bot is only required for *inbound* commands.
- **A bot token is a different credential** from the OAuth client id and secret already in the
  unit file. Same Discord application, different tab, and it needs the bot to be invited to
  the guild.

Run it as its own `systemctl --user` unit beside the other two, matching everything else here.

## Backups

`~/.local/share/toolkit-web/journals/` — one SQLite database per account, and the only thing
here that cannot be recreated. `accounts.db` beside it costs a re-login and a re-pasted pairing
token. `market-cache/` is disposable.

Automated nightly since 2026-08-25, the day the RuneLite plugin was listed on the Plugin Hub
and the journals here stopped being only mine.

```bash
python -m toolkit_web.backup
```

Writes `~/backups/runescope-<utc-stamp>.tar.gz` holding `accounts.db`, every account's
`journals/<id>/toolkit.db`, and the sync service's `sync.db` from next door. Keeps the newest
14 and deletes the rest; files in that directory that are not ours are left alone.

**It copies through SQLite's backup API rather than copying the files.** Everything here runs
in WAL mode with the services still writing, so the `.db` on disk is only part of the truth
until a checkpoint — a `cp` or a `tar` of the raw file can restore missing whatever arrived
most recently, and it looks fine until somebody goes looking for it.

`~/.config/systemd/user/toolkit-backup.service`:

```ini
[Unit]
Description=Back up RuneScope journals and the sync queue

[Service]
Type=oneshot
WorkingDirectory=%h/osrs-web
Environment=TOOLKIT_DATA_DIR=%h/.local/share/toolkit-web
Environment=TOOLKIT_BACKUP_DIR=%h/backups
ExecStart=%h/osrs-web/.venv/bin/python -m toolkit_web.backup
```

`~/.config/systemd/user/toolkit-backup.timer`:

```ini
[Unit]
Description=Nightly RuneScope backup

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now toolkit-backup.timer
systemctl --user start toolkit-backup.service   # take one now
journalctl --user -u toolkit-backup -n 20
```

`Persistent=true` matters on a laptop: a missed run fires once at the next boot instead of
being skipped silently.

**This is still one disk.** It protects against a bad deploy, a wrong `DELETE`, or somebody
asking for their data back. It does not protect against that laptop dying. Copying the newest
archive somewhere else is a separate job and is not done yet.

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

- **`wolklaw` has passwordless sudo**, set up 2026-08-24 (`/etc/sudoers.d/wolklaw-nopasswd`,
  `NOPASSWD:ALL`), specifically so Claude can do maintenance like the housekeeping above without
  a password it doesn't have. This is a deliberate, wide grant, not an oversight — but it means
  destructive commands (`apt purge`, `rm`, anything on a live service) need to be confirmed
  before they run regardless, the same as they would anywhere else. See
  `feedback_confirm_before_infra_deletion` in the assistant's own memory.
