# Scheduled-task voice reminders (local + VPS)

Scheduled tasks (Home → Scheduled) run a prompt on a cron and land as a chat.
When one fires, JARVIS voices a reminder ("your <task> is ready — want me to
read it?"). The voice agent runs **locally**; the web app can run locally
(`127.0.0.1:3000`) or on the **VPS** (`0wlan.com`). Delivery adapts:

- **Task fires on the local web** → the runner probes the local voice client
  (`127.0.0.1:8767`) and delivers directly: live session → `/user-input`
  (a spoken "yes" reads it); idle client → queued to `~/.jarvis/cron/pending.jsonl`
  (voiced on next connect).
- **Task fires on the VPS** → no local voice client there, so the reminder is
  left **pending** in the store. A local poller pulls it and speaks it on the
  box where the voice agent runs.

`JARVIS_SCHEDULED_VOICE=0` disables voice reminders entirely.

## Local box — persistent web + the poller

1. **Persistent local web** (so locally-created tasks fire even without the dev
   server, and the voice agent's `web_*` tools have a target):
   ```sh
   cd src/web && bun run build
   cp setup/systemd/jarvis-web.service ~/.config/systemd/user/
   systemctl --user enable --now jarvis-web.service
   ```
   Rebuild (`bun run build`) + `systemctl --user restart jarvis-web.service`
   after pulling web changes. The unit sets `JARVIS_LOCAL_VOICE=1` (forces
   local delivery even if the voice client is momentarily down).

2. **The VPS→local poller** (only needed if you also run scheduled tasks on the
   VPS). Create `~/.jarvis/scheduled-voice-poll.env`:
   ```sh
   JARVIS_VPS_WEB_URL=https://0wlan.com
   JARVIS_SCHEDULED_VOICE_TOKEN=<a long random shared secret>
   ```
   Then:
   ```sh
   cp setup/systemd/jarvis-scheduled-voice-poll.{service,timer} ~/.config/systemd/user/
   systemctl --user enable --now jarvis-scheduled-voice-poll.timer
   ```
   The timer no-ops until the env file has both values, so enabling it early is
   safe. Logs: `~/.local/share/jarvis/logs/scheduled-voice-poll.log`.

## VPS — expose the pending endpoint

The poller pulls `GET /api/scheduled/voice-pending`, authed by a dedicated
shared bearer token (fail-closed — disabled until the token is set):

1. Set the **same** secret in the VPS web env:
   ```
   JARVIS_SCHEDULED_VOICE_TOKEN=<same value as the poller>
   ```
2. **Exclude the path from Cloudflare Access** (like `/api/auth/*` and
   `/api/bridge/*`) so the headless poller can reach it — add a bypass rule for
   `0wlan.com/api/scheduled/voice-pending`. It's already allowlisted in
   `proxy.ts` (self-auths in-handler against the token) and returns 503 until
   the token is configured, so leaving it unset keeps it closed.

## Verify

- `curl -H "Authorization: Bearer <token>" https://0wlan.com/api/scheduled/voice-pending`
  → `{"reminders":[]}` (200) once configured; `401` with a wrong token; `503`
  if the token isn't set on the server.
- Create a task, **Run now**, and confirm the reminder is spoken (local) or
  pulled within ~1 min (VPS).
