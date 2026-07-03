# JARVIS gh-agent — P3: Deployment (local systemd timer)

> P3 is infra (two static systemd `--user` units), not code — no TDD task list.
> This doc is the build record + enable/rollback runbook + the VPS follow-on and
> the deferred hardening carried over from P2.

**Goal:** run `jarvis gh-agent` automatically on Ulrich's box so `@jarvis`
mentions get handled without a manual invocation.

**Shape:** a `--user` **timer + oneshot** (not a long-running `--watch` daemon).
A oneshot that exits and re-arms crash-recovers for free and never leaks a wedged
process; `--watch` would be more code for worse failure behavior. YAGNI on `--watch`.

---

## What was built (branch `cli-feature-unlock`)

- `setup/systemd/jarvis-gh-agent.service` — `Type=oneshot`, runs
  `bin/jarvis gh-agent --once --repo ulrichando/jarvis`.
  - `Wants=/After=jarvis-proxy.service` — forces the proxy-**reuse** path so the
    nested `jarvis -p` shares the persistent `:4000` proxy instead of spawning a
    session proxy that would race `proxy-runtime.sh`'s stale-kill.
  - `OnFailure=jarvis-alert@%n.service` — pages the phone on a failed sweep,
    same as the other core units.
  - **No strict sandbox.** Unlike `jarvis-dep-check.service`, this runs a full
    `jarvis -p` agent (network + `$HOME` + bash), so `ProtectHome=read-only` /
    `AF_UNIX`-only / `SystemCallFilter` would break it. Isolation is the P2 layer
    (allowlist + throwaway clone + never-auto-merge + untrusted-PR-head refusal).
  - `TimeoutStartSec=900` — headroom for one task (`executionTimeoutSec` 600s +
    clone/push). Logs → `~/.local/share/jarvis/logs/gh-agent.log`.
- `setup/systemd/jarvis-gh-agent.timer` — `OnBootSec=3min`,
  `OnUnitInactiveSec=10min` (≈10-min gap measured from the END of each sweep, so
  a long task never stampedes the next), `RandomizedDelaySec=60s`.

**Verification done:** `systemd-analyze --user verify` clean for both units; a
dry-run under the real user-manager env (`systemd-run --user … --dry-run`)
finished `0/SUCCESS` in 1.4s — proved `gh` is reachable + authed, PATH resolves
`gh`/`git`/`bun`, proxy reuse works, and dry-run posts nothing.

## Enable (the outward-facing step — do this deliberately)

Nothing is installed or enabled by the build. The units live in the repo only.

```bash
# 1. Install (symlink the repo units into the user unit dir), or copy.
mkdir -p ~/.config/systemd/user
ln -sf ~/Documents/Projects/jarvis/setup/systemd/jarvis-gh-agent.service ~/.config/systemd/user/
ln -sf ~/Documents/Projects/jarvis/setup/systemd/jarvis-gh-agent.timer   ~/.config/systemd/user/
systemctl --user daemon-reload

# 2. DRY-RUN FIRST — one manual sweep that posts nothing. Confirm the log.
bin/jarvis gh-agent --once --repo ulrichando/jarvis --dry-run
#    or, to test the exact service env without editing the unit:
systemd-run --user --wait --pipe \
  --property=Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin \
  ~/Documents/Projects/jarvis/bin/jarvis gh-agent --once --repo ulrichando/jarvis --dry-run

# 3. LIVE end-to-end (one real issue → PR), still manual:
#    open a throwaway issue, comment "@jarvis add a file HELLO.md saying hi",
#    then:  bin/jarvis gh-agent --once --repo ulrichando/jarvis
#    verify the jarvis/gh-* branch + PR, then close/delete.

# 4. Only after 2–3 are clean, arm the timer:
systemctl --user enable --now jarvis-gh-agent.timer
systemctl --user list-timers jarvis-gh-agent.timer      # confirm next fire
tail -f ~/.local/share/jarvis/logs/gh-agent.log
```

## Rollback

```bash
systemctl --user disable --now jarvis-gh-agent.timer
rm ~/.config/systemd/user/jarvis-gh-agent.{service,timer}
systemctl --user daemon-reload
```
The agent never auto-merges, so the worst uncaught case is an unwanted PR —
close it. Delete stray `jarvis/gh-*` branches with `git push origin --delete`.

## Deferred hardening (carried from the P2 review — do before/with 24-7 use)

1. **Reliable timeout teardown.** `jarvis -p`'s timeout currently SIGTERMs only
   the bash launcher; the real `bun` child (esp. under `start.sh`'s
   `systemd-run --scope`) can orphan and burn tokens on a genuine >600s hang.
   For unattended running, wrap the child so the whole process group/cgroup dies
   — e.g. give the *service* `RuntimeMaxSec`, or run the child under its own
   scope with `--property=RuntimeMaxSec=`. (Manual `--once` is fine as-is: execa's
   timeout surfaces failure → retried next sweep.)
2. **Process/secret sandbox.** `bin/jarvis` re-sources `~/.jarvis/keys.env`, so
   exec-layer env-scrubbing is useless — real isolation needs a
   container/namespace with only the clone writable and no host secrets in env.
   Required for the VPS variant; optional on the trusted single-user box.

## VPS variant (follow-on, not built here)

For 24-7 coverage when the laptop is off, run the same oneshot+timer on the
Hetzner VPS (see memory `jarvis-vps-deploy-hetzner`). Prereqs there:
`gh auth login` on the box, the `jarvis` CLI + its proxy deployed, and
`~/.jarvis/gh-agent.json` with the allowlist. **Do the sandbox (deferred #2)
first** — a VPS is a wider trust boundary than the personal box. Keep it on
exactly one host at a time (both polling the same repo would double-handle;
GitHub `?since=` is inclusive and dedup is per-host in `~/.jarvis/gh-agent/`).
