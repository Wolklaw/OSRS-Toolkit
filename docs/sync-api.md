# OSRS Toolkit Sync API

The contract between the RuneLite plugin and the sync service, and between the sync service and
the desktop app. The service lives at
[Wolklaw/osrs-toolkit-sync-server](https://github.com/Wolklaw/osrs-toolkit-sync-server).

This replaces the local file bridge (`.runelite/osrs-toolkit/`), which the RuneLite Plugin Hub
rejected in [plugin-hub#14949](https://github.com/runelite/plugin-hub/pull/14949): plugins may
not depend on a native application running on the user's machine. A web service is acceptable
because information sent to it can be verified, unlike an arbitrary local binary.

The event payloads themselves are **unchanged**. `ge_fill`, `ge_offer_opened`,
`ge_offer_cancelled`, `player_trade` and `loadout_snapshot` keep the exact shape they have on
disk today, so `parse_sync_event` needs no changes. Only the transport moves.

## Transport

- HTTPS, JSON, UTF-8.
- Request bodies may be gzipped (`Content-Encoding: gzip`). The plugin should do this — item
  name heavy JSON compresses roughly 5:1.
- `Authorization: Bearer <pairing-token>` on everything except `/v1/health` and `/v1/pair`.
- Bodies are capped at 1 MB, matching the desktop app's existing `MAX_EVENT_BYTES`, and refused
  from the `Content-Length` header before being read.

## Pairing

`POST /v1/pair` issues a token. The user pastes it into the plugin config and into the desktop
app once. The token identifies the pairing, not the character — several accounts sit under one
token and stay separated by the existing `account_hash`.

It is the only credential, grants access to nothing but that pairing's own data, and the
service stores only its SHA-256 digest.

Pairing is open by default so the plugin works for anyone who installs it. Setting
`SYNC_INVITE_CODE` closes it, for when the service is only for people you know.

## Liveness

The old design re-stamped a heartbeat file every 10 seconds (cheap for local writes, but 8,640
requests per user per day over a network, carrying no information).

Liveness is now **derived**: the service records `last_seen` on every authenticated request, and
the plugin calls `POST /v1/heartbeat` only when it has had nothing else to say for 60 seconds.
An account reads as connected for 90 seconds after its last contact, leaving room for one missed
heartbeat.

Desktop-side thresholds widen to match:

| Constant | Old | New |
|---|---|---|
| status freshness | 30s | 90s |
| `OFFER_SCREEN_MAX_AGE_SECONDS` | 45 | 120 |

## Timestamps

`occurred_at` is sent exactly as the plugin writes it and stored verbatim inside the payload,
but the service orders on a **normalised copy**.

Java's `Instant.toString()` prints only as many fractional digits as it needs, so the plugin
emits both `…:55.35Z` and `…:55.351844100Z`. Compared as text the second sorts *before* the
first (`'1'` is below `'Z'`), so two events written in the same second could come back reversed
— and an offer has to open before its fills land on it.

## Endpoints

### `POST /v1/pair`

`{ "invite_code": "..." }` — code only needed when the service is closed.
Returns `201 { "token": "..." }`.

### `DELETE /v1/pair`

Retires the token the request was made with, and deletes everything held under it. Returns
`{ "revoked": true, "deleted": { ... } }`.

Authenticated by the token being retired — a pairing token is the only credential this service
has, so holding one is sufficient to end it.

**Whoever mints a replacement is expected to call this on the one it replaces.** Otherwise the
old token stays valid forever, and a plugin still holding it keeps syncing into a pairing nobody
reads: correct requests, `200` responses, and a page that never shows any of it. Revoked, that
plugin gets a `401` instead.

Anything still queued under the token becomes unreachable the moment it is gone, so drain it
first if it matters.

### `POST /v1/events`

Plugin → service. Up to 100 events per call, in the shape written to disk today.

```json
{ "events": [ { "schema_version": 1, "event_id": "...", "event_type": "ge_fill",
                "occurred_at": "...", "account": { "hash": "...", "name": "..." },
                "payload": { ... } } ] }
```

Returns `{ "accepted": 3, "duplicates": 1 }`. Idempotent on `event_id` — re-posting is a no-op,
so the plugin's offline queue can retry freely.

Only the envelope is validated. Payloads and unknown event types are stored and returned
verbatim, so a plugin that learns a new trick does not need the service updated to carry it.

### `GET /v1/events?limit=500`

Desktop app → service. Undelivered events, oldest first. `limit` defaults to and caps at 500,
matching `MAX_EVENTS_PER_IMPORT`. The desktop app still sorts by
`(occurred_at, lifecycle_rank)` itself.

### `POST /v1/events/ack`

Desktop app → service. `{ "event_ids": [...] }`, returns `{ "deleted": n }`. Unknown ids are
ignored.

**Ack is per-id, not a cursor.** The desktop app leaves events it doesn't recognise in the queue
so a later build can import them — the plugin updates through the Plugin Hub while the app
updates by hand, so drift between the two is normal, not corruption. A monotonic cursor would
silently discard those.

### `POST /v1/heartbeat`

Plugin → service. `{ "account_hash": "...", "account_name": "...", "player_trade_tracking": false }`

### `PUT /v1/state/offers` and `PUT /v1/state/screen`

**`PUT`, not `POST`** — these replace the one copy the service keeps rather than adding to a
collection, and the service answers a `POST` here with `405`. Worth stating plainly: the plugin
got it wrong once, and the failure was silent — a `405` is a 4xx, the client drops 4xx payloads
instead of retrying, and the heartbeat on the same client kept reporting a healthy connection
while no live state arrived.

Plugin → service. `{ "account_hash": "...", "payload": { ... } }`, replacing `state/<hash>.json`
and `state/<hash>-screen.json`. Last write wins.

A `null` payload deletes the row. For the offer screen that is how the plugin says the box
closed — absence is the message, exactly as a deleted file was.

### `GET /v1/state?account_hash=...`

Website and desktop app → service. Slots, offer box and connection status in one call, since a
caller needs all three to draw one page, and three separate calls would return three different
moments assembled as though they agreed.

`account_hash` may be **left off**, meaning "whichever character this pairing was last seen
playing" — the question a caller has (a dashboard drawing a connection badge, most of all)
before it can name any character, and answering it here avoids a `GET /v1/accounts` first. The
hash actually used comes back as `account_hash`, and the slots and offer box are that same
character's.

An omitted hash used to match no status row at all, answering "not connected" regardless of how
many heartbeats had landed.

```json
{
  "active": true,
  "last_seen": "2026-08-21T22:14:03.120000Z",
  "account_name": "...",
  "player_trade_tracking": false,
  "offers": { "3": { ... } },
  "offers_updated_at": "...",
  "screen": { "item_id": 21802, "item_name": "...", "side": "buy" },
  "screen_updated_at": "..."
}
```

The stamps sit beside the payloads rather than inside them, since the offers payload is keyed
by slot number and an `updated_at` key inside it would be one more thing every reader has to
skip.

### `GET /v1/accounts`

Desktop app → service. Every character this pairing has been seen playing, newest first —
what a "switch character" control has to offer before it can name any one of them by hash.

```json
{
  "accounts": [
    { "account_hash": "...", "account_name": "Wolklaw", "last_seen": "2026-08-22T06:27:52Z" }
  ]
}
```

`account_name` is `null` for a character seen only through an event, before its first
heartbeat landed — a name-less entry beats one silently missing from the list.

### `GET /v1/health`

No token required. `{ "status": "ok", "time": "..." }`.

## Loadout snapshots

The one payload big enough to matter: up to `MAX_LOADOUT_ITEMS` (1,200) items with names and
values, so 100–150 KB uncompressed, fired on every bank open.

- The plugin hashes the snapshot and **skips the send when it matches the last one sent**. A
  bank rarely changes between opens, so most snapshot traffic disappears.
- The service keeps **one uncollected snapshot per account**: posting a new one deletes the
  previous uncollected one. It stays an ordinary queued event rather than a separate resource,
  so the desktop app's import path is unchanged — it just never finds a stale one waiting.

## Limits

| Limit | Value | Why |
|---|---|---|
| Request body | 1 MB | Matches `MAX_EVENT_BYTES` |
| Events per POST | 100 | Keeps a retry cheap |
| Events per GET | 500 | Matches `MAX_EVENTS_PER_IMPORT` |
| Requests per token | 120/min | ~10× normal; catches a runaway client |
| Stored events per token | 20,000 | Matches the plugin's own prune cap; oldest dropped first |

## Retention

- Events are deleted on ack, and hard-expire after 30 days regardless — the same policy the
  plugin's local prune already applies.
- Offer state, offer screen and account status are current-state rows, overwritten.
- A pairing silent for 180 days is deleted, and its rows follow.

The service is a queue, not an archive.

## Expected load

Per user, with the 60s heartbeat and snapshot deduplication:

| | Rate |
|---|---|
| Heartbeat | 60/hour |
| GE events | a few per hour |
| Offer screen changes | a few per minute, only while at the Grand Exchange |
| Desktop poll | 30/hour (2s only while the GE page is open, else 60s) |

Roughly **1,500 requests and a few hundred KB per user per day**. A laptop behind a residential
connection carries hundreds of users on that budget; the constraint that arrives first is home
upload bandwidth serving snapshots back out, not CPU.

## Storage

SQLite in WAL mode. Zero operational overhead and comfortably sufficient at this scale and
several orders above it. Tables: `token`, `event`, `offer_state`, `offer_screen`,
`account_status`.
