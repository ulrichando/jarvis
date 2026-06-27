# Disaster recovery

How to get JARVIS's data back after a disk failure, theft, or a fat-fingered
`rm`. Single-user box, so the targets are pragmatic, not five-nines.

## Targets

| | Target | How it's met |
|---|---|---|
| **RPO** (max data loss) | **≤ 1h** if the disk survives; **≤ 24h** if the disk is gone | hourly local snapshot (`jarvis-backup-local.timer`) + daily encrypted off-box push (`jarvis-backup-offsite.timer`) |
| **RTO** (time to restore) | **minutes** for one component; **~30–60 min** for a full rebuild | `bin/jarvis-restore` / reinstall + restore |

## What is protected, and where

| Data | Live path | In backup? |
|---|---|---|
| Conversations + memories | `~/.jarvis/hub/state.db` | yes (snapshot + offsite) |
| Per-turn telemetry | `~/.local/share/jarvis/turn_telemetry.db` | yes |
| Markdown memory store | `~/.claude/projects/.../memory/` | yes (tarball) |
| Web workspace index | `~/.jarvis/workspaces/_meta.json` | yes |
| Secrets / config | `~/.jarvis/keys.env`, `alerts.env` | yes (offsite bundle only — never in plaintext snapshots) |
| Face enrollments | `~/.jarvis/faces/faces.json` | yes |

- **Local snapshots** (`~/.jarvis/snapshots/`) — hourly, same disk. Fast restore, but **a disk failure loses them**.
- **Off-box bundle** — daily, `tar` of the latest snapshots + secrets, **encrypted** (gpg AES256, or `age` if installed), pushed to `JARVIS_BACKUP_OFFSITE_DIR` and/or an rclone remote.

## ⚠️ Activate the off-box layer (one-time)

Off-box backup is **inert until a destination is set**. Add to `~/.jarvis/alerts.env`:

```sh
# a path on a DIFFERENT disk / USB / synced folder (Nextcloud, Dropbox, ...)
JARVIS_BACKUP_OFFSITE_DIR=/mnt/backup/jarvis
# and/or an rclone remote (run `rclone config` first)
JARVIS_BACKUP_RCLONE_REMOTE=b2:jarvis-backups
```

Then test it now (don't wait for 03:30): `scripts/jarvis-backup-offsite.sh`

## ⚠️⚠️ Back up the BACKUP KEY off-box

The bundle is encrypted with a key auto-generated at **`~/.jarvis/backup/`**
(`backup.pass` for gpg, or `age-identity.txt` for age). **If the disk dies and
you don't have this key elsewhere, the encrypted bundles are useless.**

Copy `~/.jarvis/backup/` into your password manager (or onto the USB you back up
to) **now**. This is the single point of failure in the whole scheme.

## Restore

### One component (disk intact, oops-I-deleted-it)

```sh
bin/jarvis-restore --list           # show available bundles
bin/jarvis-restore                  # decrypt newest into ~/.jarvis/restore-<stamp>/, print apply cmds
bin/jarvis-restore --apply          # ...and copy components back (prompts first)
bin/jarvis-restart-all              # if you restored live DBs/config
```

### Full rebuild (new disk / new box)

```sh
# 1. restore the backup key first (from your password manager / USB)
mkdir -p ~/.jarvis/backup && cp <your-saved>/backup.pass ~/.jarvis/backup/

# 2. get the code + install the stack
git clone <repo-url> ~/Documents/Projects/jarvis
cd ~/Documents/Projects/jarvis && ./install.sh

# 3. restore state from the newest off-box bundle
JARVIS_BACKUP_OFFSITE_DIR=/mnt/backup/jarvis bin/jarvis-restore --apply

# 4. bring services up
systemctl --user start livekit-server jarvis-voice-agent jarvis-voice-client jarvis-proxy
bin/jarvis-health
```

## Is the backup actually restorable?

`jarvis-backup-verify.timer` runs weekly: it decrypts the newest off-box bundle,
runs `PRAGMA integrity_check` on every DB, and **pushes an urgent alert if the
backup can't be restored**. Run it on demand any time: `bin/jarvis-backup-verify`.

## Encryption at rest — ACTUAL STATE (verified 2026-06-27)

`docs/runbook/encryption-at-rest.md` *decided* on LUKS (2026-05-04), but it was
**never applied on this box** — `lsblk` shows no `crypto_LUKS` device, so live
`~/.jarvis` state (conversations, memories, **keys.env**) is **plaintext at rest**.

Consequence: **laptop theft = full data + key exposure.** The off-box *backup*
bundles are encrypted (gpg), but the live disk is not.

To close it, do the LUKS migration in `encryption-at-rest.md`, or — lighter —
keep `~/.jarvis` on a `gocryptfs`/`fscrypt` mount. Until then this is an accepted,
documented gap, consistent with the box's single-user threat model.
