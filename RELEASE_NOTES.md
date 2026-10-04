# Release Notes - v1.11.3

Every game in the Discord digest now gets its own card. The "More games" catch-all is gone.

### ✨ Improvements
- **One card per game**: games that only have a new campaign get their own card too,
  with their `New:` lines and box art when available.
- **Digest splits across messages**: cards are packed in rank order into up to 5 messages
  (Discord allows 10 cards and 6000 characters per message). The totals line is on the
  first message; Needs attention and the window footer are on the last. Only if 5 messages
  are not enough does the last one end with "…and N more games", and the totals still
  count every game.

### 🐛 Reliability
- **Partial-failure-safe delivery**: messages are posted in order, and if one fails only
  the unsent ones are retried, so nothing is posted twice. The queue clears only after
  every part is delivered.
- **Stuck-part escape**: a part Discord keeps rejecting is rebuilt from the queued events
  after 3 failed attempts, or after an app update, instead of being resent forever.
- **Pacing**: messages go out about 1.2 seconds apart to stay within Discord's rate limit.

# Release Notes - v1.11.2

A bug-fix release for the Discord digest. The same drop no longer shows up in more than one
digest, and the digest's header count matches the cards.

### 🐛 Bug Fixes
- **Each drop claim is notified once**: claims are deduplicated by campaign, drop, and
  benefit in a persisted 7-day history, so a restart, an inventory refresh, or a websocket
  report of the same claim no longer repeats it. The history is seeded on upgrade from the
  existing claim log.
- **Digest count matches the cards**: the "drops claimed" count in the message text now
  adds up the claims shown on the cards, including ×N lines and "+N more".
- **Same reward from different campaigns**: rewards with the same name from two campaigns
  stay separate lines, each labelled with its campaign.

# Release Notes - v1.11.1

A security release. Three dependencies are updated to fix six published advisories,
including one critical and one high. There are no functional changes.

### 🔒 Security
- **aiohttp 3.14.3**: Fixes an out-of-bounds read in the C HTTP response parser (high),
  HTTP request smuggling via WebSocket upgrade, and the WebSocket client accepting
  compressed frames without negotiated permessage-deflate.
- **anyio 4.14.2**: Fixes TLS certificate host-name spoofing through IDNA 2003 encoding
  (critical) and process-pool workers hanging on undrained stderr.
- **idna 3.15**: Fixes crafted inputs to `idna.encode()` bypassing the CVE-2024-3651 fix.

# Release Notes - v1.11.0

Digest v2: the Discord digest is rebuilt around games. Each game gets its own card with a
reward picture, totals move into the message text, and "Needs attention" now respects
every toggle.

### ✨ New Features
- **One Card per Game**: Games are ranked (mining now, then most claims, then latest
  claim) and the top six get their own card with a thumbnail. The latest claimed reward
  image is used, then the in-progress reward, then the game's box art.
- **More Games Overflow**: Everything past the top six is listed in a compact
  "More games" card.
- **No Header Embed**: Totals sit in the message text. The window and the next send time
  are in the last card's footer.
- **Drop Thumbnail in Immediate Mode**: A claimed drop's immediate message now shows the
  reward picture. Immediate mode is otherwise unchanged.

### 🐛 Bug Fixes
- **Toggles Apply at Send Time**: "Needs attention" and every other block are filtered by
  the current toggles when the digest is built, not only when an event is queued. Events
  queued before a toggle was switched off no longer show up or count, and a digest with
  nothing left to show is skipped.

### 🎨 Improvements
- **Urgent Alert Cap**: Stall and sign-in alerts are grouped as ×N, newest first, and
  long lists end with "…and N more urgent" so log lines and the queue note always fit.
- **Safe Thumbnails**: Image links are validated (https only, sane length). If Discord
  rejects a message, it is retried once without pictures so a bad image can't block the
  queue.

# Release Notes - v1.10.1

A polish release for digest mode: the Discord digest reads more cleanly, the notification
settings now match the rest of the web GUI, and an invalid server time zone no longer
shifts digests silently.

### 🐛 Bug Fixes
- **Time Zone Fallback**: An unknown `TZ` value (e.g. `Germany/Berlin` instead of
  `Europe/Berlin`) made digests go out on UTC while the settings page still showed the
  invalid name. The page now shows the zone actually in use and warns about the bad
  value, and a warning is logged at startup.
- **Digest Header**: Previews no longer show a zero-length `22:52 – 22:52` range or claim
  to cover "last 24 hours". The header now reads "Since <start>", and "Next digest" also
  shows how long until then.

### 🎨 Improvements
- **Readable Progress Rows**: Progress bars are fixed-width, so bars and percentages line
  up in Discord. Drops with generic names like "Drop" show their campaign name instead,
  and drops with progress are listed before untouched ones.
- **Notification Settings Design**: The remaining checkboxes are now toggles, event
  toggles sit in tiles next to their labels, interval presets use the segmented-control
  style, and the queue status and preview button share one panel.

# Release Notes - v1.10.0

This release adds Discord digest delivery: events can still go out one-by-one, or they can
collect into a single scheduled digest. Existing installs stay on immediate mode.

### ✨ New Features
- **Discord Digest Mode**: Choose between one message per event or a daily/weekly digest
  with interval, weekday, send time, empty-window sends, and optional progress/error
  sections. The same event toggles apply to both modes.
- **Urgent Alerts Still Immediate**: In digest mode the cooldown only throttles urgent
  alerts (mining stalled and sign-in). Those alerts still go out right away and are also
  listed in the next digest.
- **Digest Preview**: Settings can post a preview of the saved queue without clearing it.
  The preview button stays disabled while the form is dirty. Leaving digest mode with a
  queued digest shows a flush warning and sends one final digest.

### 🔧 Under the Hood
- A `notification-digest` task renders one Discord message (description lines, drops
  grouped by game and campaign, Discord timestamps, longest-section trimming within
  6000 characters) and clears the queue only after a 2xx response.
- `GET /api/notifications/status` now reports mode, queued count, next digest time,
  timezone, last digest, and dropped count.

