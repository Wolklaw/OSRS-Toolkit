# The plugin's own roadmap

Where **OSRS Toolkit Sync** — the RuneLite companion plugin — goes next, and the envelope it
has to stay inside while getting there.

This document lives in *this* repository on purpose. The plugin's own repo
(`Wolklaw/osrs-toolkit-runelite`) is what the Plugin Hub builds from, commit by commit, and
planning notes in it are one more thing a reviewer has to read past. Keep it here.

[`CLAUDE.md`](../CLAUDE.md) holds the roadmap for the website and the desktop app. **None of
its phases need anything from this document** — that separation is deliberate, so plugin work
and toolkit work never block each other.

---

## Where the plugin stands today

It is **shipping on the Plugin Hub.** [plugin-hub#15320](https://github.com/runelite/plugin-hub/pull/15320)
merged on 2026-08-25, and the manifest on `runelite/plugin-hub` master currently reads:

```
repository=https://github.com/Wolklaw/osrs-toolkit-runelite.git
commit=b60e00c1420615045e79d2040a132f86522d0fb0
warning=This plugin submits your IP address, and may submit various account data, to a
        3rd-party server not controlled or verified by Runelite developers.
```

That commit is the plugin repo's current tip, version **1.1.0** — so the pin is in step with
the source and nothing is pending. The `warning=` line is riktenx's, applied by him during a
queue-wide sweep; it is the standard install-time dialog for any plugin that contacts an
external server, not a critique of this one.

That is 1.1.0. **1.2.0 is built and awaiting an in-game test**, so the pin above is still 1.1.0.
It adds the settings below marked 1.2.0, and the Grand Exchange panel under
[Grand Exchange suggestions](#grand-exchange-suggestions-in-the-client).

BSD-2 licensed, Java 11 target, 66 tests, no CI.

### What it records

| Setting | Default | What it sends |
|---|---|---|
| `syncEnabled` | **off** | The master switch. Nothing is recorded or queued until it is on *and* a pairing token is set. |
| `trackGrandExchange` | on | Offer opened, partial and complete fills, offer cancelled, the live eight slots, and which item the offer box is on. |
| `trackPlayerTrades` | **off** | The counterparty's display name and every item and coin both ways. |
| `trackPvmLoadout` | **off** | Equipment, inventory, bank (top 1,200 stacks by value), skill levels and experience — on bank open, fingerprinted so an unchanged bank sends nothing. |
| `trackLoot` | **off** | What an NPC dropped, from `NpcLootReceived`. |
| `trackDeaths` | **off** | Equipment and inventory at death, and the skull flag, from `ActorDeath`. |
| `trackProgress` (1.2.0) | **off** | Quest states and points, diary tiers, combat achievement points and tier status, Slayer task. |
| `trackSkills` (1.2.0) | **off** | Skill levels and experience as they change, at most once a minute. |
| `showGeSuggestions` (1.2.0) | **off**, third-party warning | Reads tracked items and suggested prices from runescope.app while the Grand Exchange is open. |

1.2.0 also widens two existing settings without renaming them: `trackLoot` now includes chest,
raid, clue and pickpocket loot, and `trackPvmLoadout` now includes the looting bag, seed vault,
seed box and tackle box.
The design worth not breaking: events land in an on-disk outbox under `Filepath`
(`.runelite/plugin-data/osrs-toolkit-sync/`, migrated from the pre-`Filepath` folder via
`legacyDataDirectory`), a flush task drains it every 5 s, nothing is deleted until the service
confirms it, and liveness is a 60 s heartbeat rather than a re-stamped file. Switching the
master switch off withdraws the live state it had published, so nothing of yours is left
standing on the website afterwards.

---

## The envelope

Two kinds of constraint. Neither is negotiable, and only one of them is written down.

### The written rules

[`runelite/example-plugin/AGENTS.md`](https://github.com/runelite/example-plugin/blob/master/AGENTS.md)
is the enforced list — it is what reviewers and the automated review bot work from. The
plugin-hub README only covers submission mechanics. **Read AGENTS.md, and never derive a
requirement for this plugin from a review comment left on somebody else's PR.** That mistake
has been made here twice, both times about the `warning=` string.

The lines this plugin lives closest to:

- All file IO goes through `Filepath`, obtained from `getPluginDirectory()`, which requires
  `internalName` in `@PluginDescriptor`. Touching `Filepath.Unchecked` costs the plugin its
  automated review.
- OkHttp for every request, injected with `@Inject OkHttpClient`. Never on the client thread —
  `enqueue()`, and `clientThread.invoke()` to get back in.
- Gson injected, never constructed. `.newBuilder()` to vary it.
- Never `Thread.sleep()`. Never block in `startUp()`/`shutDown()` — and note that
  `PluginManager` asserts the Swing EDT in both, so those two methods run on the thread that
  draws the client.
- Scheduled futures cancelled explicitly on shutdown, *as well as* shutting the executor down.
- A `@ConfigItem` toggling a third-party-server feature must be off by default and carry
  exactly: *"This feature submits your IP address to a 3rd-party server not controlled or
  verified by RuneLite developers"*. `OsrsToolkitSyncConfig.syncEnabled` has that string
  character for character. **Do not improve the wording.**
- `net.runelite.api.gameval` constants, never magic numbers, and widget components passed
  whole rather than interface and child combined by hand.
- No reflection, JNI, JNA, Unsafe, subprocesses, classloading, runtime codegen, or Java
  serialization.
- Config keys and the group name never get renamed without a migration.
- A PR description to `runelite/plugin-hub` must end with the footer `Generated-by: Claude Code`.

### The unwritten ones

Both were learned from this plugin's own review and appear in none of RuneLite's published
sources:

1. **No dependency on a native application running on the user's machine.** This is what closed
   [#14949](https://github.com/runelite/plugin-hub/pull/14949): *"we dont allow plugins that
   rely on separate native applications that run on the users system. if you can instead
   interface with a website or something that would be ok."* The objection is **reviewability** —
   a reviewer can inspect an endpoint and cannot inspect an arbitrary local binary. That is why
   the file bridge became `sync.runescope.app`, and it is why the plugin must never grow a
   local-app path again, not even as a fallback for people who liked the old one.
2. **Don't talk to the maintainers unprompted.** riktenx, verbatim, after two explanatory PR
   comments: *"we don't really care, save some output tokens and have your llm not post these
   updates."* So: if the manifest genuinely needs a new pin, bump it **silently** — no comment
   explaining it, no comment confirming it, no thanks for a review suggestion. Default to not
   bumping at all over bump-and-explain, and batch a real need into one update.

---

## Standing obligations

Not features. Things that have to keep being true, and that have already broken once each.

### Build against the current client

`build.gradle` resolves `net.runelite:client` at `latest.release`, and the Hub builds the
pinned commit against whatever the client is **at build time**. So a client API change can
break a commit that built fine when it was pinned, and the first sign of it is a user saying
the plugin vanished. The repo history says this plainly: *"Compile against the current client
again, which is what took this down."* It has happened twice.

**There is no CI in the plugin repo.** A scheduled GitHub Actions workflow that runs
`./gradlew build` against `latest.release` — weekly, and on push — is the single highest-value
thing that could be added to that repository, and it is worth more than any feature below it.

### Keep the version in step in three places, and the user agent in four

`build.gradle`'s `version`, `runelite-plugin.properties`'s `version`, and the install
instructions all have to agree. `SyncClient.USER_AGENT` currently says `OSRS-Toolkit-Sync/1.0`
while the plugin is 1.1.0 — harmless today, but the CDN in front of the service filters partly
on that header, so it is not a string to let rot.

### Nothing in the plugin repo "for the reviewers"

No comment-density pass aimed at reading less AI-authored, no cosmetic manifest churn. Ordinary
bug fixes for ordinary reasons are fine.

---

## Roadmap

### Tier 1 — keep it alive

1. **CI, as above.** Scheduled build against `latest.release`.
2. **`shutdownNow()` instead of `shutdown()`.** AGENTS.md reads *"Never block on `shutDown()` or
   `startUp()` — don't call `executor.awaitTermination()` in shutdown, just use
   `shutdownNow()`."* The plugin uses `shutdown()` deliberately, with a comment saying an
   interrupt would leave a half-written scratch file behind. That reason no longer holds:
   `LocalSyncStore` writes to `<name>.<uuid>.tmp` and `ATOMIC_MOVE`s it into place, so the
   destination is never half-written, and there is already a sweep that collects orphaned
   `.tmp` files. The deviation passed human review once; the review bot works from the rule
   text. Switch it, delete the comment, and stop carrying the explanation.
3. **The user-agent version.** Derive it, or bump it with the rest.

### Tier 2 — complete the data the toolkit already wants

Each of these closes a gap the website's own roadmap has to work around. None is a new *kind*
of data — they are the same opt-in categories, measured where they are actually missed.

4. **Send skills and experience without needing a bank.** *Built in 1.2.0, awaiting an in-game test.* Today XP only arrives inside a
   loadout snapshot, which fires on bank open, so a four-hour skilling session that never banks
   produces no XP readings at all — and the website's session inference has to lean on changed
   snapshots to date it. A skills-and-XP-only event, throttled and fingerprinted the same way,
   on a `StatChanged` edge and at logout, is a few hundred bytes against the snapshot's six
   figures. **This is the highest-value functional change on this list**: it makes XP per hour
   real, and it makes session boundaries honest for everyone who isn't a flipper.
5. **Loot that isn't an NPC kill.** *Built in 1.2.0 on `LootReceived`, awaiting an in-game test.* `NpcLootReceived` misses chests, raids, clue caskets,
   implings and pickpocketing — a large share of what a PvM trip actually pays.
   `net.runelite.client.plugins.loottracker.LootReceived` carries `name`, `combatLevel`,
   `type` (`LootRecordType`: NPC, PLAYER, EVENT, PICKPOCKET), the items and an amount, and is
   what RuneLite's own loot tracker consumes. It is a core *plugin's* event class rather than a
   `net.runelite.client.events` one, so confirm depending on it is acceptable before building
   on it; if it is, send the `type` through so the journal can tell a chest from a kill.
6. **The containers net worth is currently blind to.** *Built in 1.2.0 for the looting bag, seed vault, seed box and tackle box; the collection box has no usable constant in the client and is left out.* The snapshot reads `WORN`, `INV` and
   `BANK` only. Real wealth also sits in `LOOTING_BAG`, `SEED_VAULT`, `SEED_BOX`, `TACKLE_BOX`,
   the GE collection box (`GE_COLLECT_*`) and raids shared storage — all real
   `gameval.InventoryID` constants. The catch is that `getItemContainer` returns null until the
   player has opened that interface in the session, so these can only be read opportunistically
   and the payload has to mark which containers it actually saw. Without that marker the website
   cannot tell "empty looting bag" from "never looked", and net worth would lurch every time
   someone opened one.
7. **Explicit session boundaries.** `GameStateChanged` is already subscribed. A login/logout
   event would let the website stop inferring sessions from gaps between unrelated activity.
   Cheap, and it makes every per-session number exact rather than approximate.

### Tier 3 — make it answerable when it goes wrong

8. **Say more in the sidebar panel.** It reports the connection, which is the hard part and is
   done. What support questions actually need next: how many events are queued, when the last
   flush succeeded, and which character the service last saw. All three are already known on
   the IO thread.
9. **Say something when the outbox is stuck.** An `UNAUTHORIZED` outcome deliberately keeps the
   queue — correct, since the fault is the token — but a player whose token was revoked gets a
   growing queue and a panel line they may not be looking at. One chat message, once, on the
   transition rather than per flush.

### Tier 4 — only with a maintainer's blessing first

The lesson of #14949 is that the expensive mistake is building and then submitting. Anything
here gets cleared on the RuneLite Discord **before** code is written:

10. **Wilderness/safe-death context on a death event.** Your own death, your own data, and it
    would let the journal stop saying "carried" where it means "lost". But it is adjacent to
    PvP features the rules restrict, so ask.
11. **Anything that reads a price from the service rather than sending to it.** The plugin is
    write-only today, which is a large part of why it is uncontroversial. A plugin that pulls
    toolkit suggestions into the client is a different review, and quite possibly a different
    answer.

---

## Grand Exchange suggestions in the client

Built in 1.2.0, and the one place the plugin reads from the toolkit. It adds text widgets to
the game's own chatbox prompts and reorders search results with `GrandExchangeSearched`, which
RuneLite's own Grand Exchange plugin also uses. A first attempt as a floating overlay was dropped:
its clicks did not survive stretched mode.

Filling the price and quantity prompts on a click copies what Flipping Utilities does, which
writes the value into the prompt's input and leaves Enter to the player. That is the one part
that rests on precedent rather than on a written rule: AGENTS.md says no autotyping into the
chatbox, and a strict reading could cover it.

What it deliberately does not do: select an item, because that is the game's own action on a
search result and nothing found does it for the player, or confirm an offer.

Ask on the RuneLite Discord before the Hub PR whether writing a value into the prompt's input on a
click is acceptable, and, if it is, whether writing an item name into the search box would be.

## Hard limits

Red lines, each tied to a rule. These are not "later" items; they are permanently out.

- **No local-app path, ever again** — not a fallback, not a dev convenience. See #14949.
- **No data about other players beyond what one opt-in trade already sends.** AGENTS.md:
  *"No crowdsourcing data about other players (locations, gear, names, etc.)"* and *"Don't
  expose player information over HTTP."* The counterparty display name in `player_trade` is the
  single place the plugin sends anything about anyone else; it is opt-in, off by default, and
  passed review. **Do not extend it** — no scouting, no aggregating counterparties across
  users, no "who else is trading this". If the website's pooled-liquidity idea ever ships, it
  pools *your own* fills, server-side, and the plugin's payloads do not change.
- **Nothing that assists combat.** No next-attack prediction, prayer-switch hints, attack
  counters, hazard warnings, flinch timing, NPC focus, or boss simulation. The plugin records
  what happened; it never tells you what to do next. This also means a supply-burn warning
  belongs on the website, not in the client.
- **No input injection, no menu entries that send actions to the server, no chatbox
  autotyping.**
- **No credential storage.** The pairing token is the one secret, it is RuneLite config with
  `secret = true`, and it grants access to nothing but its own pairing's data.
- **No reflection or anything else on the forbidden-language list**, however tidy it would make
  a container sweep.

---

## Shipping a plugin release

1. Change the plugin repo. Keep reformatting out of feature commits.
2. `./gradlew build` and the test suite. Then test it **in game** — AGENTS.md is explicit that
   a clean JVM start is not sufficient, and this plugin's own history agrees: the two worst
   bugs it has shipped were a silent `405` and a pairing token that produced no feedback at all.
3. Bump `version` in `build.gradle` and `runelite-plugin.properties` together.
4. Push.
5. Only if the change is one users need: bump `commit=` in `plugins/osrs-toolkit-sync` on the
   `Wolklaw/plugin-hub` fork branch, open or update the PR, and **say nothing**. The PR
   description ends `Generated-by: Claude Code`.
6. The `RuneLite Plugin Hub Checks` job showing red with the title "Requires maintainer review"
   is not a failure. Don't chase it.
