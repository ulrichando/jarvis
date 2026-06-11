# Encryption at rest

## Decision (2026-05-04)

**Use LUKS for disk-level encryption. Do not migrate to SQLCipher.**

JARVIS holds three SQLite databases with sensitive content:

| DB | Path | Contents |
|---|---|---|
| Hub state | `~/.jarvis/hub/state.db` | Conversations, durable user memories (Phase 12) |
| Telemetry | `~/.local/share/jarvis/turn_telemetry.db` | Per-turn metadata (no transcript bodies) |
| Snapshots | `~/.jarvis/snapshots/*.db` | Hourly backups of the above |

### Why LUKS over SQLCipher

| Factor | LUKS | SQLCipher |
|---|---|---|
| Code change | Zero | Hub daemon + voice-agent + telemetry writer + memory layer all need to swap `sqlite3` → `sqlcipher3` |
| Risk to live memories | None | High — memory layer has no migration story today; a bad transcode loses user memories |
| Performance | Native (no per-page crypto) | ~5–15% throughput hit on writes |
| Threat model fit | Laptop theft / cold disk | Same |
| Attacker who roots the laptop | Defeated either way | Defeated either way (key in memory once unlocked) |
| Backups | Encrypted automatically (snapshots live on the encrypted volume) | Each snapshot must be re-encrypted |

The threat model for a single-user laptop is **lost/stolen device** + **shoulder surfing of disk during repair**. LUKS handles both. The threat model SQLCipher uniquely defeats — root attacker who can't enter your unlock passphrase but can read disk — doesn't apply on a personal device where root === you.

If JARVIS later runs on a shared machine or a VPS, revisit.

## How to enable LUKS

This requires reinstalling the OS or doing a backup → wipe → restore migration. Not a same-day fix. Steps:

1. **Backup everything** to an external drive: `rsync -aHAX --info=progress2 /home/ulrich/ /mnt/backup-2026-05-04/`
2. **Reinstall Arch / Kali** with the installer's "Encrypt full disk" option (LUKS2 on root, or LUKS on `/home` only).
3. **Verify** with `cryptsetup status root` — should show `cipher: aes-xts-plain64` (default).
4. **Restore** home from backup.
5. **Validate** JARVIS comes back up: `systemctl --user start livekit-server jarvis-voice-agent jarvis-voice-client`, then `sqlite3 ~/.local/share/jarvis/turn_telemetry.db "PRAGMA integrity_check;"` and the same for `~/.jarvis/conversations.db`.

## Interim mitigations until LUKS is enabled

These reduce blast radius without requiring a reinstall:

- **`chmod 700 ~/.jarvis ~/.local/share/jarvis`** — already strict but verify
- **Don't sleep with the laptop unlocked in a public place** — once LUKS is enabled, sleep + screen-lock combine to require passphrase on resume
- **Snapshot directory permissions:** `chmod 700 ~/.jarvis/snapshots`

```bash
chmod 700 ~/.jarvis ~/.local/share/jarvis ~/.jarvis/snapshots ~/.jarvis/hub
```

## Per-file encryption alternative

If LUKS isn't viable but you want stronger-than-FS protection for memories specifically, `fscrypt` (kernel-supported per-directory encryption, supported on ext4) is the middle ground:

```bash
sudo apt install fscrypt
sudo fscrypt setup
fscrypt setup ~/.jarvis
fscrypt encrypt ~/.jarvis/hub
```

Same threat-model coverage as LUKS for that subdir, no full-disk reinstall. Locks/unlocks tied to your login PAM session.
