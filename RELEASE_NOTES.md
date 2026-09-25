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

