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

