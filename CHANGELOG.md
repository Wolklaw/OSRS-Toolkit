# Changelog

All notable changes to OSRS Toolkit are documented here.

## [Unreleased]

### Fixed

- The taskbar showed a blank square instead of the app's icon. The icon was there all along —
  Explorer's preview pane drew it — but it contained only a single 256x256 image stored as an
  uncompressed bitmap, and the taskbar, Alt-Tab and Explorer's list views all want something
  around 16-48px. It now carries seven sizes from 16 to 256, each PNG-compressed.

- The website could never tell this app that the RuneLite plugin was connected. The shared
  sync client asked the sync service about live state without naming a character, which
  matched nothing and so answered "not connected" no matter how many heartbeats had landed.
  Live Grand Exchange slots and the offer box were empty for the same reason.

### Changed

- Reading live plugin state costs one request to the sync service instead of three. The
  service answers status, slots and the offer box together precisely so a page shows one
  moment; asking three times was both slower and the only way to get an inconsistent answer.

## [1.2.1] - 2026-08-23

### Fixed

- The app crashed on launch with a saved window state that restored as maximized or
  fullscreen: restoring that geometry fires a window-state-change event before the journal
  mirror timer it reaches for existed yet.

## [1.2.0] - 2026-08-23

### Highlights

- The desktop app can now mirror its journal against your [runescope.app](https://runescope.app)
  account. Get a desktop access token from your profile there, paste it into
  **Settings → Website**, and a trade or tracked position recorded in either place reaches the
  other within a minute — delete one somewhere and it disappears everywhere. Leave it
  disconnected and the app is exactly what it always was: a local journal on your PC, nothing
  sent anywhere.
- RuneLite data now reaches this app through the website rather than a folder the plugin writes
  on this PC. The RuneLite Plugin Hub does not allow a plugin that feeds an application on the
  same machine it runs on, so the plugin posts to a small sync service instead, the website
  collects from that, and this app reads from there. If you are still running an older version
  of the plugin that writes to `.runelite\osrs-toolkit`, this app still reads that folder too —
  nothing you already had stops working.

### Fixed

- Grand Exchange fills and offers synced from more than one RuneLite character through the same
  pairing token could cross between them — a fill on one character's account could land on
  another character's tracked position instead of its own.
- The Connect RuneLite dialog stated flatly that trade history is never sent anywhere and
  offered an "Open sync folder" button, regardless of whether the app was actually reading from
  a local folder or from the website. Signed in, that button could open a broken location; the
  privacy line now says which is actually true for how you have it set up.

## [1.1.3] - 2026-08-20

### Fixed

- PvM Readiness's "Missing gear" column named only the requirement category (e.g. "Ranged
  weapon"), not which items would satisfy it — you had to already know the checklist to
  know what to buy or bank. It now lists the specific items accepted for that slot.
- The Leviathan, Phantom Muspah, and Nex all required a ranged weapon but left the Toxic
  blowpipe off their accepted list, despite it being wiki-recommended gear for all three —
  an account holding one could be told it needed a ranged weapon it already owned.
- The in-app update check only ran once, at startup. A session left open for a while would
  never notice a release published after launch. It now rechecks automatically every hour.

## [1.1.2] - 2026-08-20

### Fixed

- PvM Readiness's Perilous Moons checklists (Blue Moon, Blood Moon, Eclipse Moon) only
  recognized 3-4 weapons each, missing several the wiki's own strategy guide recommends —
  including the moons' own reward weapons, Dual macuahuitl and Belle's folly. An account
  correctly geared for a moon could be told it was missing a weapon it was holding.
- Eclipse Moon's stab checklist referenced "Zamorakian spear," which isn't a real item name
  and could never match anything a player owned. Corrected to Zamorakian hasta.

## [1.1.1] - 2026-08-20

### Highlights

- PvM Readiness now covers 30 bosses instead of 20, adding Duke Sucellus, Vardorvis, The
  Leviathan, and The Whisperer (Desert Treasure II), Phantom Muspah, Nex, Scurrius, and the
  three Perilous Moons bosses (Blue Moon, Blood Moon, Eclipse Moon).
- PvM Readiness's gear checklists were out of date: a maxed melee weapon like Osmumten's fang,
  Scythe of vitur, Soulreaper axe, or Blade of saeldor didn't count toward "strong melee
  weapon," so a well-geared account could be told it was missing something it plainly owned.
  Every melee checklist now recognizes current best-in-slot weapons.
- Double-clicking a PvM Readiness row now opens the boss's Strategies page on the wiki
  instead of its general overview page — the page that actually explains how to fight it.

### Fixed

- GE Flipper's "Track all 1 recommended offers" button said "offers" even with exactly one
  offer to track. It now says "offer" when there's just one.
- PvM Readiness didn't recognize an imbued item as satisfying a checklist that just asked for
  the base item — an imbued slayer helmet, for example, was flagged as a missing face mask.
  An imbued item is strictly better than its base form, so it now counts.
- Cerberus's second gear checklist entry was labeled "Souls/spirits protection" but actually
  checked for antifire potions. Antifire is real at Cerberus — it's for the lava pools he
  stands you in, a different mechanic from the ghostly "souls" that drain your prayer — the
  label just described the wrong one. Relabeled to "Antifire protection (lava pools)," with a
  note on what actually reduces the souls' drain (a spectral spirit shield or Ward of
  Arceuus, neither required).

## [1.1.0] - 2026-08-20

### Highlights

- Updates install themselves. Say yes and the app closes, replaces itself, and reopens on the new
  version. There is no wizard to click through and no second window asking a question you have
  already answered — the only thing you do is decide.
- The app is compiled now rather than packaged. The old build put it behind a bootloader stub
  shared with every other program built the same way, so antivirus that had learned to distrust
  that stub distrusted this app for what other people shipped inside it. There is no shared stub
  any more, and nothing in the build is compressed in the way scanners treat as hiding something.
- The setup wizard looks like it belongs to the app it installs, with its own artwork, the licence
  it ships under, and publisher and support links that Windows shows in its own Apps & features
  list rather than a blank entry.

### Changed

- Updating an installed copy no longer opens the installer. The download is still fetched from the
  official GitHub release and still checked against its SHA-256 digest before anything is touched;
  what changed is what happens after. The update is written into the folder the app already
  occupies and the app is started again from it.
- An update stays where it was. A copy installed for all users updates as one, and a copy
  installed for the current user stays that way. Left to its own defaults the installer would put
  an update wherever it had permission to, which for a machine-wide install updated by an
  unelevated app meant a second copy in the user's own folder and the original left behind, stale.
- Portable copies still get the wizard, deliberately. For a portable folder the wizard asks a real
  question rather than repeating one: it offers an installed copy that does not exist yet, and
  leaves the portable folder alone. Nothing about a portable copy is replaced behind your back.
- Builds are compiled with Nuitka instead of packaged with PyInstaller. This is the change behind
  the false-positive virus warnings, and it comes with a cost worth stating: a release build now
  takes minutes of C compilation rather than seconds of archiving.
- The executable is no longer UPX-compressed. Compressed sections are a strong signal on their
  own to a scanner, since compressing a program is chiefly how one hides what it contains. The
  build is bigger and less interesting to look at, which is the point.

### Added

- The application and the setup program both carry full version information — publisher, product,
  description, and version — visible in the file's Properties. The old build recorded none of it,
  which is a poor look for an unsigned program: nothing to check and nothing declared.
- The setup wizard shows the GNU GPL v3 the app is released under, and its entry in Apps &
  features links to the project, its issue tracker, and its releases page.
- Setup closes a running copy rather than failing on a file in use, and refuses to run twice at
  once.

### Known limitations

- Releases are still not code-signed, so Windows SmartScreen continues to show an **Unknown
  publisher** warning on first run. Nothing in this release changes that, and nothing except a
  code-signing certificate can. The changes here reduce antivirus false positives, which is a
  different problem with a different cause.

## [1.0.2] - 2026-08-20

### Highlights

- Stand at the Grand Exchange in-game and the journal rows you are trading now pick themselves
  out on their own — the row washed blue, and inside it the Quantity and the price for the side
  you are on in the same blue. Those are the figures the game is waiting on, and until now
  finding them meant reading down a table of near-identical rows with the interface already
  open.
- It follows a whole trade rather than one screen of it. Open the "Set up offer" box and the
  highlight narrows to that one item while you type; confirm, and it stays on the row while the
  offer fills and while you collect it; walk away and it goes out. Selling works the same way
  and points at the sell price instead.

### Added

- Blue is deliberately not the yellow of the "this just finished" blink. Yellow means something
  happened while you were away; blue means you are looking at it right now. A row that wants
  both gets the blink first and the steady wash back underneath when the blink is over.
- Several rows for one item all light up rather than one being guessed at — two pending buys of
  the same teleport are an ordinary thing to have, and pointing at the wrong one would be
  pointing at the wrong quantity.
- A "Bought" row keeps the amber or red already on its price to ask. That colour says whether
  the price clears what the item cost, which this highlight has no way to say, so it is not
  painted over.
- Requires the companion RuneLite plugin, which now reports where in the Grand Exchange you are
  alongside the slots it already reported. Nothing is uploaded, it stops with Grand Exchange
  tracking, and an older plugin simply never highlights anything.

## [1.0.1] - 2026-08-19

### Fixed

- A journal row's "come see this" flash could arrive long after it stopped mattering. A
  sale finishing while the Trade Journal wasn't the page on screen queued a flash that
  stayed queued no matter how long that took — by design, so it wasn't lost — but nothing
  cancelled it if the coins got collected straight off the Grand Exchange interface before
  the journal was ever opened. Widening the Status filter later would still play it,
  lighting up a row for money that was already collected. Collecting an item now cancels
  any flash still queued for the position it finished, the same way the app already reads
  a slot disappearing from the plugin's own file as the sign a player took it. A flash
  still queued because a position just reached "Bought" is untouched by this — that one
  means go list this, and collecting the goods is what makes that possible, not what
  answers it.

## [1.0.0] - 2026-08-19

### Highlights

- Versioning reset. Everything that shipped under 1.4.6–1.8.5 is folded into this one
  release as the toolkit's first stable baseline — that history stays below for
  reference, but version numbers start counting from here going forward.

## [1.8.5] - 2026-08-19

### Highlights

- The window now reopens where you left it instead of snapping back to the centre of the
  screen every launch.
- Every table answers the keyboard as well as the mouse: select a row and press Enter to
  open it, the way double-clicking already did.
- Right-click any row — GE Flipper, Watchlist, Trade Journal, RuneLite activity, Alch
  Finder, Skilling Profit, PvM Readiness — for the actions that apply to it, including a
  quick "Copy" of the item name for pasting into the game's own search box.
- The Trade Journal's Update, Delete, View details, and Delete entry buttons now grey out
  until a row is actually selected, and the Delete key removes the selected row (with the
  same confirmation the buttons already ask for).

## [1.8.4.6] - 2026-08-19

### Highlights

- The "ready to list" price highlighted on a Bought row now prefers the live market over
  a plan frozen from whenever the flip was tracked, and never dresses up a number that
  doesn't clear what you paid as sound advice — it turns red and says so instead.

### Fixed

- A Bought row's highlighted "price to ask" always showed the flip's original sell
  target, frozen at whenever it was first tracked. For Quick and Balanced strategies —
  which never revisit that number on their own — the market could move well below it by
  the time the buy actually filled, and in the worst case (a position auto-created with
  nothing better than its own buy price to fall back on) the highlighted number was
  exactly what was paid for it: a guaranteed loss, presented in the same confident amber
  as real advice. The highlight now prefers the live passive sell target when one is
  available, falling back to the frozen plan only when there is no market data for the
  item, and turns red with a warning instead of amber whenever neither number clears
  the GE tax on what was actually paid.

## [1.8.4.5] - 2026-08-19

### Highlights

- A flip that sells out while you're watching used to vanish from the journal with no
  acknowledgement at all — the default "Active trades" filter is exactly what a
  completed sale drops out of. The sidebar and tab now keep their dot lit until you
  actually see the row, and widening the Status filter delivers the blink you missed.

### Fixed

- Completing a flip under the default "Active trades" filter marked its flash as
  delivered even though the row it was for had nothing to paint on — the filter that
  hides completed positions is the exact thing a sale finishing transitions into. The
  dot went dark, and the only sign anything happened was gone with it. A flash now only
  counts as seen once the row it belongs to is actually visible under the current
  filter; widening Status to "All statuses" or "Completed" delivers it there instead.

## [1.8.4.4] - 2026-08-19

### Highlights

- A position already listed before this update updates too: the app now reads what your
  Grand Exchange slots are really asking and adopts it, so a row you relisted before
  upgrading stops being flagged the next time the journal renders.
- Tooltips no longer need the cursor to hold still. Hovering the ⚠ warning — or any table
  cell — used to need the mouse to stop and wait; one pass over it is now enough.

### Fixed

- A position listed before this update kept being graded against its original sell
  target forever, because the price recorded on listing only covers offers placed from
  1.8.4.3 onward. The journal now also reads what each Grand Exchange slot is really
  asking on every render and adopts it onto the matching position, so a row relisted
  before the upgrade catches up instead of staying stuck.
- Table tooltips required the cursor to arrive over a cell and then stop moving, which
  made a small target like the ⚠ warning glyph fiddly to trigger — a slight drift while
  aiming for it reset Qt's hold-still timer and the tooltip never appeared. Every data
  table now shows its tooltip the moment the cursor arrives, not once it stops.

## [1.8.4.3] - 2026-08-19

### Highlights

- Relisting at a new price now clears the "Needs attention" warning. The journal grades your
  listings against the price you really asked on the Grand Exchange, not the price it once
  suggested — before this, no relist could ever satisfy it.
- Collect a buy and the price to ask for it is picked out on its row, so you can go straight
  back to the game and type it.
- Clicking through your Grand Exchange slots lights one row at a time instead of every row
  you have clicked so far.
- Click the "Needs attention" card to jump to the rows it is counting.
- Confidence on the GE Flipper is now coloured against the floor your strategy actually
  filters on, so a score that only just scraped in looks like one.

### Fixed

- The "Needs attention" warning could not be cleared by changing your price. When a sell
  offer was placed, the app read its price only to decide which position it belonged to and
  then discarded it, so the warning went on comparing the market against the sell target the
  flip was *planned* at. Relisting at exactly what the market suggested left the row flagged,
  and only Overnight positions — whose targets refresh daily on their own — ever cleared. A
  position now records the price it was really listed at, and that is what the warning reads.
- Relisting the rest of a partly sold position was dropped entirely. A cancelled sell leaves
  such a position exactly as it was, by design, so there was no status change to make and the
  new price went nowhere. It is now recorded, and the journal refreshes when it arrives.
- The warning's tooltip only appeared over the one cell carrying the ⚠. Every cell beside it
  answered a hover with its own text, so reading the explanation meant hunting for the right
  column. Any cell on a flagged row now gives it, and the P/L cell keeps its own note too.
- Clicking a second Grand Exchange slot blinked the first slot's row alongside it. The blink
  joins anything already blinking, which is right for two offers finishing seconds apart and
  wrong for one question asked twice; working down all eight slots washed the whole table.
  A click now lights only what was clicked.

### Added

- A collected buy picks out the price to ask for it. Any position sitting at "Bought" — in
  hand, nothing listed — shows its sell suggestion in bold amber until the offer is actually
  placed. The blink says which row the moment a buy lands, but it is over in two seconds and
  you are still in the game; the figure you came back for stays put.
- The "Needs attention" card is clickable. It selects, scrolls to, and blinks every row it is
  counting, widening the status and period filters if they were hiding them, and shows a hand
  cursor only while it has something to point at.
- Confidence is graded against the minimum the chosen strategy filters on rather than against
  100, since every row on screen has already cleared that minimum. A score in the bottom third
  of the room left is drawn as a warning, the top third as a strength, and its tooltip says
  what the number measures and what the colour means. The recommendation card grades it the
  same way.

### Changed

- Tooltips are themed with the rest of the app instead of arriving in the operating system's
  colours, and long ones are broken into short lines rather than laid out on a single strip
  the width of the window.
- A table cell only repeats its own text on hover when the column is too narrow to show it.
  Every cell carried its text as a tooltip so elided figures stayed readable, which was easy
  to miss while tooltips were unstyled and became noise once they were not. A tooltip that
  explains something the cell does not say is unaffected.

## [1.8.4.2] - 2026-08-19

### Highlights

- Finish buying something and the item finds you: its Grand Exchange slot and its Trade Journal
  row flash yellow together, so you never have to hunt down the row you are about to sell.
- A flash that happens while you are in the game waits for you — the sidebar carries a dot until
  you come back and look at the journal, then it plays.
- Click any Grand Exchange slot to jump straight to that item's row in the journal.
- Ctrl+1 to Ctrl+7 open the seven pages, and F5 refreshes the market.

### Added

- The moment a buy finishes, both places that know about it say so at once: the Grand Exchange
  slot holding the goods and the Trade Journal row for that position blink yellow together, three
  times, and then hand their colours back. Finding the right row was the whole difficulty — the
  buy completes in the game, and what happens next happens in a table that may hold fifty rows
  sorted by something other than "the one that just changed". The same blink marks a sale
  closing a flip out, and an offer cancelled and left uncollected, because both leave something
  sitting in a slot for you to collect.
- The blink waits until somebody is there to see it. Buying finishes while you are in RuneLite,
  not in front of this window, which is exactly when two seconds of yellow would be spent on
  nobody. So a flash is held until the window has focus, is not minimised, and is showing the
  Trade Journal — the page for the slots, and its "Plans & completed" tab for the table — and
  until then the sidebar's Trade Journal entry carries a dot, as does the tab. Arriving is what
  plays it. Nothing blinks on start-up: whatever was already finished when the app opened is
  where it was, not something that just happened.
- Grand Exchange slots are clickable. Clicking one selects, scrolls to, and flashes that item's
  row in the journal below — the same question the blink answers, asked at any other moment. If
  the status or period filter was hiding the row, both give way so the row can be shown; a slot
  holding an item with no journal row yet says so under the slots rather than doing nothing.
- Tracking a flip now points at what it made. "Track recommended offers" and tracking a single
  candidate both throw you onto the Trade Journal, at a table that may already be long; the rows
  they just created blink, the same way a filled offer does.
- Keyboard shortcuts for the sidebar: Ctrl+1 through Ctrl+7 open the pages in the order they are
  listed, and F5 refreshes the market from any page. Each sidebar entry's tooltip names its own
  shortcut. Checking one figure and going straight back to the game no longer needs the mouse.

### Changed

- Every theme carries its own pair of flash colours rather than sharing one yellow: a wash the
  journal's status and profit colours stay readable through, and a brighter edge for the slot
  card. Old School flashes in the game's interface orange, Light in a gold dark enough to read
  against white.

## [1.8.4.1] - 2026-08-18

### Highlights

- Sorting the Trade Journal by Status now follows the trade lifecycle, from Pending buy through
  to Cancelled, instead of the alphabet.
- Every row keeps its own colour: no more statuses coming out plain grey, and no more amber
  "needs attention" warnings landing on the row below the one that earned them.

### Fixed

- Sorting the Trade Journal by Status put the rows in no order anyone asked for — Cancelled
  landing above Partially sold, Completed above Listed for sale. The lifecycle rank each
  status sorts by was being attached to the cells only after the table had already sorted
  itself by the text in them, and every cell touched afterwards moved its own row again
  mid-pass, so some rows were never given a rank, a colour or a tooltip at all. Ranks are now
  set while the table is still filling, so one sort at the end sees them all.
- The same shuffle broke the colour coding it walked past: a status could come out plain grey
  and unbolded, and a "Needs attention" warning could be painted onto whichever row had slid
  into place behind it — an item turning amber with no ⚠ against its name while the stale ask
  that earned it stayed plain. Each row is now coloured from its own data rather than by
  position, so the ⚠ and the colour always belong to the same row.

### Changed

- The fan-content notice in the About tab and the README now spells out that this content is
  not endorsed by or affiliated with Jagex.

## [1.8.4] - 2026-08-18

### Highlights

- A flip you planned but never placed now says "Planned" instead of "Pending buy", so it stops
  looking like something is still filling.
- A flip you are part way through buying no longer appears twice after hopping worlds.
- A flip bought on a smaller offer than you planned for now finishes when you sell it, instead
  of reading as still filling for good.

### Changed

- "Pending buy" was saying two things at once: an offer is out there filling, and a flip is
  planned but not placed. Nothing in the table told them apart, and a cancelled offer left the
  second kind sitting there indefinitely — the plan is deliberately kept rather than deleted
  along with the offer placed for it, so the row outlived the offer that explained it. A
  pending buy with nothing bought and no Grand Exchange slot holding a buy for that item now
  reads "Planned", in the muted colour the table uses for rows nothing is happening to. Only
  the label changes: the status filters, the Update dialog and every figure on the page still
  see one status, so a planned row is still found under "Pending buy" and still counts as an
  active trade. An empty Grand Exchange is an answer like any other — every slot collected is
  the ordinary way to have nothing placed. Nothing is relabelled only when there is no view of
  the slots at all, RuneLite never connected or its saved state unreadable, because "not
  placed" and "cannot say" must not read the same.

### Fixed

- A flip stayed on "Pending buy" for good once its buy offer turned out smaller than the plan
  it was placed against — the lot was bought, sold and the profit banked, while the row still
  read as filling and none of it reached the summary cards. A plan is sized to what the flipper
  recommends and the offer to what the four-hour buy limit actually allows, so a plan of 7,612
  filled by an offer of 3,000 could never buy its way to "Bought", and a sale only ever looked
  at positions that had. A sale now falls back to a position still buying, behind every
  finished holding: what it bought is stock like any other, and selling it says the buying is
  over, so the position is resized to what it really bought and completes from there. Listing
  that stock says the same, so the row reads "Listed for sale" while the offer is up rather
  than waiting for the first sale to land. The trade-off is that listing part of a holding
  while its buy offer is still running ends that position at what it holds and leaves the rest
  of the offer to open a row of its own — better than a position that can never finish at all.
  A row already stuck this way stays as it is: open it with "Update selected trade", set
  "Quantity acquired" to what you really bought, and add the sale fill.
- A flip already part bought could appear twice on the Trade Journal, the second row priced
  from the offer so its sell target equalled its buy target and it read as break-even. The game
  re-sends every Grand Exchange offer whenever a world finishes loading — on a hop, a login, or
  a region change — and an offer arriving that way was read as a newly placed one. The plugin
  now says which offers were re-sent, and a re-sent offer rejoins whatever the Journal is
  already tracking it with, rather than opening a row beside it or claiming a flip you had
  planned and never placed — which quietly resized that plan to an offer it had nothing to do
  with, and did it again on the next hop. An offer you have just placed still takes the plan
  you placed it for. An offer placed where this app could not see it — on mobile, or while it
  was closed — still starts a row of its own the first time it is seen. Older plugin builds
  cannot send that flag, so an offer matching a part-bought position on both size and price is
  recognised as that same offer regardless; two offers differing in either still get a row
  each.
- The estimated profit on a part-bought position priced stock it never bought: a plan for 7,612
  that only bought 3,000 advertised the profit of 7,612 at the price actually paid, two and a
  half times what the position could make. The estimate now prices what a position holds once
  anything has bought, and the whole plan only while nothing has — the reading "Capital traded"
  already takes of the same two cases. The "N left" note beside a realized figure counts the
  same stock.
- Placing or cancelling a Grand Exchange offer edits a Journal position but records nothing in
  the RuneLite activity feed, so unlike a fill there was nothing to recognise it by if it was
  seen twice. A queue file that could not be deleted after it was imported — locked by
  antivirus, say — was applied again on the next pass, opening a duplicate row or deleting a
  position a second time. Each one is now recorded as it applies and only applies once.
- The RuneLite plugin could quietly lose track of an offer it had been following for hours.
  Windows refuses to replace a file while another program has it open, and the plugin saves its
  Grand Exchange slots to a file this app reads every time it draws them, so a save could
  collide with a read for no reason but timing. The save is now retried a moment later, and the
  scratch file a failed one leaves behind is cleaned up rather than waiting for the hourly
  sweep.

## [1.8.3.2] - 2026-08-17

### Added

- An **Old School** theme, in Settings alongside Dark, Midnight, and Light. It dresses the
  toolkit in the game's own interface: carved stone panels over a dark leather canvas,
  square corners instead of rounded ones, warm parchment text, and Gielinor's orange on the
  page you have open and the heading of a card that wants reading. Gold stays the colour of
  the button that does the thing. The numbers keep their own greens and reds — a flip that
  lost money has to look like a loss before it looks like the game — and every status colour
  the Trade Journal uses is retuned to stay legible against stone. The three existing themes
  are untouched, and the theme you have saved is still the one you get.

## [1.8.3.1] - 2026-08-17

A bug-fix release: nothing new to learn, and nothing about using the app has changed.

### Highlights

- Cancelling a Grand Exchange offer no longer deletes a flip you had planned yourself.
- "Capital traded" means the same thing on the Trade Journal and on Performance; the two
  pages could report different amounts for the same half-sold position.
- Editing a finished trade keeps the day it finished instead of moving it to today.
- Prices that downloaded fine are no longer thrown away when the local cache cannot be written.

### Fixed

- Cancelling a Grand Exchange buy offer that had not filled deleted the tracked plan behind
  it, even when that plan was one you had made yourself. Tracking a suggested flip and then
  placing exactly that offer is the ordinary way to use the Journal, and the offer adopts
  the plan you already made rather than opening a second row beside it — so cancelling took
  your plan with it. Only a position an offer opened for itself is removed now; an adopted
  plan is left exactly as it was, tracked and waiting to be placed again.
- Editing a finished position — correcting a fill price weeks later, say — re-dated it to
  the moment you saved. An old trade would jump into "Today" on the Trade Journal and
  Performance period filters, out of the period it really belonged to, and its recorded hold
  time stretched to however long ago it had actually finished. A position that was already
  finished now keeps the time it finished at; reopening one and finishing it again still
  records the new completion.
- "Capital traded" meant two different things under one name. On the Trade Journal it was
  everything a position had spent; on Performance it was the cost of the quantity that had
  actually sold. A position bought in full and only half sold therefore showed 200,000 gp on
  one page and 100,000 gp on the other, beside the same realized profit. Both pages now
  report the cost of what actually sold, so the figure covers the same goods as the profit
  next to it — the rest is money still in the trade, not money the trade has produced a
  result with. Realized profit and win rate were already in agreement and are unchanged;
  all three now come from one calculation rather than two, so they cannot drift apart again.
- A market refresh that downloaded perfectly well but could not write its local cache file —
  a full disk, antivirus scanning, or a second copy of the app holding it open — threw the
  fresh prices away and answered with the older cached ones instead, or reported the whole
  refresh as failed when there was no cache to fall back on. The cache is a fallback for the
  next run, so it no longer decides whether this one succeeded.

### Changed

- The Buy limits tab re-reads every three seconds and only ever looks at the last four
  hours, but was loading every Grand Exchange fill ever imported each time. At twenty
  thousand imported fills that was around a third of a second of the interface's own time,
  every three seconds, and it grew for as long as the app was used. It now reads only the
  window it displays.

## [1.8.3] - 2026-08-17

### Highlights

- The GE Flipper's recommendation reads as a table and grows to fit all eight offers
  instead of showing two and a half lines at a time.
- "What's new" catches you up on every version you missed, not only the newest one.
- Two pages that need the RuneLite plugin no longer read as answers when nothing is
  connected: an empty Buy limits tab said you were clear to buy, and PvM Readiness marked
  every boss "Not ready".

### Fixed

- The Buy limits tab reported "Nothing is currently limited" whether the RuneLite plugin
  was feeding it purchases or was not installed at all. Without the plugin nothing counts
  purchases — manual journal entries included — so that read as room to buy when it was
  really an empty tab. It now says which of the two it is.
- PvM Readiness marked every activity "Not ready" and listed every requirement as missing
  before any gear had been synced, which is a verdict on the account rather than on what
  the app knows about it. Unsynced activities now read "Unknown", in muted grey rather
  than the red used for a requirement you genuinely fall short of, and their missing-skill
  and missing-gear columns stay empty because nothing was compared. Estimated GP/hr, the
  notes, and the wiki guide for each activity are unchanged.

### Changed

- The GE Flipper's recommendation is a table rather than a paragraph per offer: item,
  quantity, buy, sell, total cost, estimated profit, and confidence line up in columns,
  with the plan's totals above it and its caveats below. The card also takes the height
  the plan actually needs, so a full eight-offer recommendation is read in one piece
  instead of two and a half lines at a time through a fixed 110px window. Only when the
  app window is too short to hold both the plan and a usable flip table does the list
  start scrolling, and the summary line and closing note stay put while it does.
- The "What's new" window covers every release between the version you last opened and
  the one you just installed, so skipping two updates no longer means never hearing what
  they changed. It shows headlines rather than entries in full — a release can lead with
  a "Highlights" section saying its own short version, and anything without one is cut
  back to its opening line — with "Full changelog" there for the detail. Opening it from
  Settings shows recent history; a fresh install still opens on its own version alone.

## [1.8.2] - 2026-08-17

### Fixed

- Tables sized themselves to their longest cell with no upper bound, so on a 1920x1080
  monitor the rightmost columns were pushed past the edge of the window with nothing on
  screen to suggest they existed — the "Guide" column on PvM Readiness and Skilling
  Profit was simply missing, and the "Notes" column ran off the side. Columns now fit the
  window they are shown in, shrinking together and eliding their text rather than
  overflowing; the full text is still in each cell's tooltip.
- A column sized to its own widest value could elide that very value, turning a figure
  like "1,900,000 gp" into "1,900,000 …".
- Closing the app in the first second after launch left the start-up update check aimed
  at a window that was already going away.
- Text sitting on a card painted the page background behind itself as a dark block, so the
  savings goal on Performance read as a large empty input box rather than a line of text
  on a panel. The brand card in the sidebar had the same seam.

### Changed

- Your 8 Grand Exchange slots now sit on the Trade Journal page itself, laid out the way
  the game lays them out, instead of behind a "GE Offers" tab. Each slot fills as its
  offer does, colour-coded for buying, selling, and finished-waiting-to-be-collected, so
  progress is something you see rather than a percentage you stop and read. The old tab
  spent six of its eight rows saying "Empty"; it is gone, and nothing else has changed
  about where the figures come from.
- Columns holding prose — missing skills and gear, notes, assumptions, item and character
  names — now read left to right instead of being right-aligned like the figures around
  them. Figures stay right-aligned.
- The GE Flipper's portfolio recommendation is about seven times faster to calculate, so
  changing your cash stack, GE slots, or strategy no longer pauses the window while it
  re-plans. The recommendations themselves are unchanged.

## [1.8.1] - 2026-08-16

### Fixed

- A position marked Supplies showed a projected P/L like "Est. -257,600 gp" in red — its
  sell suggestion mirrors its buy price by design, so the estimate was really just the GE
  tax on reselling at cost, an alarming and meaningless number for something that was
  never going to be sold. It now reads "—", the same as a cancelled trade.
- The savings goal's progress bar rounded a real but tiny sliver of progress straight to
  "0%", reading as no progress at all against a large target; it now shows "<1%" instead.
  Its ETA is also shown in years once it passes a year, rather than as an unreadable raw
  day count.

## [1.8.0] - 2026-08-16

### Added

- Trade Journal positions can now be set to a new "Supplies" status — quest and skilling
  buys made through the Grand Exchange no longer have to sit in your flip totals,
  Performance grading, or the needs-attention list. Select a row, choose "Update selected
  trade", and set its status to Supplies; the existing Status filter picks it out again
  when you want to see just those.
- A "Supplies spend" tab on the Trade Journal totals what those positions actually cost,
  grouped by item, so questing and skilling supplies read as a spend report instead of
  journal clutter.
- A "Buy limits" tab on the Trade Journal shows which items are still inside their 4-hour
  Grand Exchange buy limit from your synced purchases, how much room is left, and when the
  oldest purchase in that window ages out.
- Performance now has a savings goal: give it a label and a target amount and it tracks
  realized profit from the moment the goal is set, with a rough ETA from your last 7 days'
  profit rate.
- A "GE Offers" tab on the Trade Journal shows all 8 Grand Exchange slots live, read
  straight from the RuneLite plugin's own offer state — including an offer just placed
  and not yet filled at all, which no combination of past sync events could show.

## [1.7.3] - 2026-08-15

### Fixed

- Installing an update could fail with a raw "WinError 5: Access is denied" if the
  downloaded installer's destination file was locked — usually antivirus briefly
  scanning it, or a copy of the installer left running from an earlier attempt. The
  update now retries past a transient lock, and a lock that doesn't clear gets a plain
  explanation and next step instead of the OS error.

## [1.7.2] - 2026-08-15

### Fixed

- The Trade Journal's "Needs attention" tooltip described a stale ask in words but never
  showed the asking price or the live market suggestion it was comparing, and neither
  number appears elsewhere in the table — no way to check the flag against the market
  yourself. It now states both prices and the percentage drop.

## [1.7.1] - 2026-08-15

### Added

- The Trade Journal now warns when a Listed for sale or Partially sold position's asking
  price has drifted at least 2% above what the current market suggests. Overnight
  positions already review their target once a day on their own; Quick and Balanced
  targets never move after they are first set, so this is where a stale ask is easiest to
  miss. A new "Needs attention" summary card counts every flagged position regardless of
  the status or period filter currently selected, and the item name in the table is
  marked with a tooltip explaining why.
- Added an "Export CSV" button to the Trade Journal. It writes every tracked position and
  manually entered trade — not just the rows the current status and period filters
  happen to show — to a CSV file: date, status, item, quantity, buy/sell targets, actual
  average fill prices, strategy, and profit, with whether that profit is realized or
  still an estimate.
- Item Details now has a "Price history" tab showing roughly the last 75 days of
  six-hour average instant-buy and instant-sell prices from the OSRS Wiki, so a price's
  recent trend is visible without leaving the app. It loads in the background the first
  time the tab is opened rather than on every item lookup.

## [1.7.0] - 2026-08-15

### Added

- Added a Performance page that grades the plans in your Trade Journal against what
  actually happened. The app already suggested a strategy and target prices and already
  recorded your real fills, but nothing compared the two — so a journal full of history
  could not answer whether Overnight actually beat Quick, whether the buy targets were
  reachable, or which items were genuinely worth trading.
- Performance "By strategy" reports realized profit, win rate, return on capital, and
  median hold time for each strategy you traded under, so the comparison is between your
  own results rather than between the strategies' descriptions.
- Performance "Plan vs. actual" compares the original buy and sell targets against the
  prices you really filled at, weighted by quantity, and what those targets promised for
  the quantity that actually sold against what it really made after tax. Paying under a
  buy target counts as the win it is rather than being colored like a shortfall.
- Performance "By item" shows which items you actually make money on. Items traded only
  once are hidden until you ask for them, because a single flip says little about an item.
- Every figure on the Performance page is realized from recorded fills; a projection never
  counts as a result. Return on capital is weighted by the money at work, so one large flip
  is not averaged away by a small lucky one, and a partly sold position counts only the
  part that has sold.
- The Performance page has its own period filter and reads the same history as the Trade
  Journal under the same rules, so the two pages cannot disagree about the same trades.
  Manually entered completed trades are included under "Manual entry"; they carry no plan,
  so they are left out of Plan vs. actual.

## [1.6.1] - 2026-08-15

### Added

- Filling (or placing) a Grand Exchange buy offer with nothing already tracked for that item now
  seeds its sell target from the app's current suggested price when one is available, instead of
  always mirroring the buy price. The Journal row shows a real profit estimate right away rather
  than one that reads as guaranteed break-even. Your actual buy price is never changed by this —
  only the sell target, and only when the suggestion beats what you paid. Tracking a flip
  yourself before you buy still takes priority, exactly as before.
- PvM Readiness now covers 20 bosses, up from 8 — Kalphite Queen, Dagannoth Kings, Corporeal
  Beast, Thermonuclear Smoke Devil, Kraken, Alchemical Hydra, Grotesque Guardians, Sarachnis,
  and the three wilderness bosses (Vet'ion, Callisto, Venenatis) join the existing checklist.
- PvM Readiness's GP/hr now nets the live cost of what a trip actually consumes — prayer
  potions and food — off the community loot-value estimate, so the number moves with real
  market prices instead of sitting fixed. Hover a row's GP/hr for the loot-value and supply-cost
  breakdown. If a supply item has no current price, the figure quietly falls back to the loot
  estimate alone rather than showing a wrong number or hiding the row.
- The Watchlist tab can now add an item directly by typing its name, instead of needing to find
  it in GE Flipper or Alch Finder first and toggle it on from there.

### Removed

- The Kingdom Optimizer tab. Its Royal Trouble worker-assignment comparison saw little real use
  next to the rest of the toolkit.

## [1.6.0] - 2026-08-15

### Added

- A "What's new" window opens the first time you run a new version, listing that version's
  changes from the bundled changelog. It appears once per version and stays available from
  Settings → About, so an update no longer arrives with no explanation of what changed.
- OSRS Toolkit now checks for a newer official release in the background at start-up. While you
  are up to date nothing appears and start-up is unaffected; when a newer version exists, a
  window offers to install it, remind you later, or skip that version for good. A failed check —
  usually no network — stays silent, and the manual check in Settings → About still reports its
  result. Previously an update was only found if you went looking for it.

## [1.5.3] - 2026-08-15

### Added

- The RuneLite plugin now reports when you cancel a Grand Exchange offer. A position opened for
  an offer that never filled is removed, and one that part filled is resized down to what
  actually bought and marked "Bought" — previously a cancelled offer left a "Pending buy" in the
  Journal that could never resolve, and reducing it was left to you.

### Fixed

- Tracking a suggested flip and then placing that buy offer at the Grand Exchange added a second
  Journal row for the same trade, priced from the offer so its sell suggestion equalled its buy
  suggestion and it always showed an estimated loss. A placed buy offer now adopts the plan you
  already tracked for that item — keeping your own buy and sell targets — and grows it if you
  bought more than you planned. Two genuinely separate offers still get a row each.
- A gear and bank snapshot from a bank with more than 1,200 distinct stacks was rejected whole,
  silently taking your equipment, inventory and skill levels with it, so PvM Readiness never
  updated for a full bank. The plugin now sends the most valuable 1,200 stacks.
- An offer placed while the desktop app was closed could import as two Journal positions instead
  of one: queue files are named by random ID, so the offer's first fill could be applied before
  the event that opened it. Queued events are now replayed in the order they happened.
- Grand Exchange offers belonging to a newly logged-in character could be recorded against the
  character logged in before them, and compared against that character's saved offer slots.
- A player trade could be recorded as empty if the game cleared the trade window before the
  "Accepted trade." message arrived. Contents are now taken as of the confirmation screen.
- A damaged offer-state file stopped the plugin's Grand Exchange tracking for that character for
  good, with nothing said about it. It now starts over from an empty state and repairs itself.
- An event from a RuneLite plugin newer than this app was quarantined as invalid and lost. The
  plugin updates itself through the Plugin Hub while this app is updated by hand, so a plugin
  running ahead is normal — such events now wait in the queue and import once you update, and
  the sync status says so instead of reporting them as invalid. Genuinely malformed events are
  still quarantined.
- Rejected events accumulated in `.runelite/osrs-toolkit/rejected` with nothing ever removing
  them; the directory is now capped. Scratch files left behind by a write interrupted at client
  shutdown are swept too, and the plugin re-runs its housekeeping periodically rather than only
  at start-up.

## [1.5.2] - 2026-08-15

### Fixed

- Sorting the Skilling Profit table by any column other than the default and then re-rendering
  it — which happens on every keystroke in the search box — attached each row's guide link and
  assumption tooltip to the wrong row, so "Open guide" could open a different skill's wiki page.
- Sorting the PvM Readiness table had the same effect: rows could show "Ready" in the red
  not-ready color, and the guide column could link to a different boss's wiki page.
- Sorting the RuneLite activity feed made "View details" show a different trade than the
  selected row, and "Delete entry" delete that wrong trade. Rows are now identified by their
  event, not by their position on screen.
- A partially sold position's realized profit only counted toward the Trade Journal summary
  cards under "All time," so the row could show a realized gain while the "Realized profit" card
  above it read zero. Positions still in progress now count under every period, matching the
  rows that are always visible.
- "Today," "This week," "This month," and "This year" used UTC calendar days, so trades made in
  the evening were filed under the next day. They now follow your own clock.
- "Capital traded" counted the full planned quantity of a position even when only part of the
  buy had filled; it now counts what the recorded buy fills actually cost.

## [1.5.1] - 2026-08-15

### Added

- Added a PvM Readiness page. Enable the optional gear sync in the RuneLite plugin settings and
  open your bank in-game to compare your equipped gear, inventory, bank contents, and skill
  levels against a hand-picked checklist for Vorkath, Zulrah, General Graardor, Kree'arra,
  Cerberus, King Black Dragon, Giant Mole, and Barrows. Shows Ready/Not ready per activity,
  exactly which skills and gear are missing, and a rough GP/hr figure; double-click a row to open
  its OSRS Wiki page. Gear sync is off by default and nothing is uploaded.
- Added a "Recommend something else" button to GE Flipper that swaps the current recommendation
  for the next-best combination without reusing any item already recommended this session.
- Trade Journal positions now start tracking the moment a Grand Exchange offer is placed instead
  of waiting for the first fill. A buy offer opens a "Pending buy" position sized to the full
  order immediately; a sell offer against something already "Bought" advances it to "Listed for
  sale" right away. Requires the updated RuneLite plugin.
- RuneLite-synced GE fills now automatically apply to a matching tracked Trade Journal position,
  and a buy fill with nothing tracked yet starts a new position sized to the offer's real total
  quantity — so partial fills of one large order keep landing on the same position instead of
  only showing up once everything is filled.
- The RuneLite activity feed now merges every fill belonging to the same Grand Exchange offer
  into one row that updates in place, instead of adding a new row per partial fill.

### Fixed

- The GE Flipper and Skilling Profit search fields could shrink to a few unusable pixels wide at
  small window sizes.
- The GE Flipper recommendation list could grow tall enough to push the offer table out of view;
  it now scrolls within a fixed height instead.
- The Skilling Profit and PvM Readiness guide columns rendered the entire assumption/notes
  sentence as a hyperlink; only the actual guide link is styled as one now, with the notes text
  moved to its own column.
- PvM Readiness showed "Not ready" in the same muted gray as neutral text instead of a color that
  stands out next to "Ready."
- A synced buy fill with no real order size to go on could be auto-tracked at just that fill's
  own quantity and marked complete, so the next fill of the same still-open order had nothing
  eligible to match and started a separate position — fragmenting one order into several. It is
  now left for the RuneLite activity feed until its real total quantity is known.

## [1.5.0] - 2026-08-14

### Added

- Trade Journal now records buy fills at multiple prices, the same way sale fills already work,
  with a weighted-average cost basis.
- Added a "Quantity acquired" field so a buy order cancelled mid-fill can shrink to the amount
  actually bought and continue through Bought → Listed for sale → Partially sold → Completed
  instead of being stuck as an all-or-nothing Cancelled entry with no way to sell the leftover.
- Added an earnings period filter (Today, this week, this month, this year, rolling windows, all
  time) to the Trade Journal. It scopes both the summary cards and the visible rows; positions
  still in progress always stay visible regardless of the selected period.
- Added a Data tab in Settings to move the local database to a location of your choice; existing
  data is copied automatically and the app switches to it immediately, no restart required.
- Sorting the Trade Journal by Status now follows the trade lifecycle (Pending buy → Bought →
  Listed for sale → Partially sold → Completed → Cancelled) instead of alphabetically, so
  finished trades no longer land between active ones.

### Fixed

- A malformed OSRS hiscores response could permanently disable character lookup until restart.
- Selected navigation item, selected table row, and Skilling Profit guide links were unreadable in
  the Light theme.
- Kingdom Optimizer could get stuck showing "Waiting for market prices…" with fewer than two
  priced resources.
- The Skilling Profit guide link opened a browser tab on a single click instead of requiring a
  double-click.
- A corrupted saved setting could crash the app on startup instead of falling back to its default.
- Background threads were not stopped when closing the app.
- Saving an invalid trade update could raise an unhandled error instead of a message box.
- Auto-refreshing market data every five minutes silently discarded a manually chosen table sort.
- Manually resized table columns snapped back to their computed width on the next window resize.
- RuneLite sync now imports a batch of events over one database connection instead of one per
  event, and synced trade item lookups use an index instead of a full table scan.
- Wiki price requests now ask for gzip and identify a contact URL in the User-Agent.

## [1.4.7] - 2026-08-11

### Added

- Added restrained, theme-aware Trade Journal status colors for pending, bought, listed,
  partially sold, completed, and cancelled positions.
- Added a Trade Journal filter for quickly separating active trades, completed history, and
  cancelled positions.

### Changed

- Muted positive projected journal P/L so it is visually distinct from realized gains, while
  projected losses remain red and zero remains neutral.
- Cancelled positions without realized sale fills now show a neutral dash instead of a positive
  hypothetical estimate.
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
