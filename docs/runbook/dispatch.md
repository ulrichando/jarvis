# Dispatch — runbook

Dispatch (src/web `/code`) lets you assign a task from your phone, run it on your
always-on desktop's local `/code` worker, and get pinged when it finishes or needs
a go-ahead. A dispatched session is badged **Dispatch** in the Code sidebar.

Nothing below is required to *see* the Dispatch UI — the composer, toggles, badge,
Outputs tray, and phone check-in (open the session URL on your phone) all work out
of the box. The two items here make the optional bits fully functional.

## Phone push notifications (VAPID)

Push is **fail-soft**: with no VAPID keys set, subscribe returns `{enabled:false}`
and Dispatch degrades to the in-tab notification the session view already shows.
To enable real phone push:

1. Generate a keypair once:

   ```bash
   cd src/web && npx web-push generate-vapid-keys
   ```

2. Put them in the web app's environment (`src/web/.env.local` for local, or the
   deploy env):

   ```
   JARVIS_VAPID_PUBLIC_KEY=<public>
   JARVIS_VAPID_PRIVATE_KEY=<private>
   JARVIS_VAPID_SUBJECT=mailto:you@example.com
   ```

3. Restart the web app. Open `/code` in the browser and grant the notification
   prompt (the `PushSubscribe` client registers `/sw.js` and subscribes).

- **iOS**: Web Push only works from an **installed PWA** — "Add to Home Screen"
  first (iOS 16.4+). Android browsers and desktop work directly.
- **Behind Cloudflare Access**: exclude `/api/push/*` and `/sw.js` (like
  `/api/bridge/*`) or the subscribe/callbacks get gated.
- Fires: session → `requires_action` = "needs a go-ahead"; → `idle` / a `result`
  event = "task finished". Deduped via a `last_push_status` marker in
  `worker_state_json`; a re-run (status → `running`) re-arms it.

## Keep-awake (enforced when the web server is on the desktop)

The **Keep awake** toggle now really enforces: on dispatch-create with
keep-awake on, the web server spawns

```bash
systemd-inhibit --what=idle:sleep --mode=block --who="Jarvis Dispatch" \
  --why="dispatch task running" sleep 21600
```

and holds it for the session (`src/web/src/lib/dispatch/keep-awake.ts`). The
lock is released when the task finishes (the done-push path in
`src/web/src/lib/push/fire.ts`), and also on interrupt, archive, delete, or
flipping the toggle off — plus a 6h safety cap (`sleep 21600`) so an orphaned
inhibitor (web server killed mid-task) self-releases.

**Limitation:** this only works when the web server is co-located with the
desktop — the standard local `jarvis-web.service` on 127.0.0.1:3000, same box
as the `/code` worker. A VPS-deployed web app can't inhibit the local desktop;
there (and on non-Linux hosts without `systemd-inhibit`) acquire silently
no-ops (one `console.warn`). Check a held lock with `systemd-inhibit --list`.

## Deferred

- Agent-initiated "share a file" to the Outputs tray needs a new CLI worker event
  (`src/cli`, off-limits). v1 derives the Outputs tray read-only from the
  session's git diff (`last_diff_json`).
