#!/usr/bin/env bash
# jarvis-backup-offsite — the DURABILITY layer.
#
# scripts/jarvis-backup-local.sh writes atomic snapshots to ~/.jarvis/snapshots
# — on the SAME DISK as the originals. One drive failure loses every
# conversation, memory, and workspace. This script bundles the latest snapshots
# PLUS the secrets/config that aren't snapshotted (keys.env, alerts.env, faces),
# ENCRYPTS the bundle, and pushes it off the box.
#
# Configure at least one destination (in ~/.jarvis/alerts.env or keys.env):
#   JARVIS_BACKUP_OFFSITE_DIR     a path on a DIFFERENT disk / USB / synced folder
#   JARVIS_BACKUP_RCLONE_REMOTE   an rclone "remote:path" (run `rclone config` first)
# Until one is set this script is inert (logs + exits 0 — no alert spam).
#
# Encryption is MANDATORY (the bundle contains secrets): uses `age` if installed,
# else `gpg --symmetric`. The key/passphrase is auto-generated under
# ~/.jarvis/backup/ (mode 600).
#   *** COPY ~/.jarvis/backup/ OFF THE BOX (password manager / the USB) ***
#   Without it you cannot decrypt after a disk loss. See
#   docs/runbook/disaster-recovery.md.
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NOTIFY="$(cd "${SELF_DIR}/../bin" && pwd)/jarvis-notify"
SNAP_DIR="${HOME}/.jarvis/snapshots"
BK_DIR="${HOME}/.jarvis/backup"
LOG="${HOME}/.local/share/jarvis/logs/backup-offsite.log"
RETENTION="${JARVIS_OFFSITE_RETENTION:-14}"
mkdir -p "$BK_DIR" "$(dirname "$LOG")"
chmod 700 "$BK_DIR" 2>/dev/null || true

for f in "${HOME}/.jarvis/keys.env" "${HOME}/.jarvis/alerts.env"; do
  [ -f "$f" ] && { set -a; . "$f" 2>/dev/null || true; set +a; }
done

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" | tee -a "$LOG" >&2; }
die_soft() { log "$1"; exit 0; }   # never hard-fail the timer

OFFSITE_DIR="${JARVIS_BACKUP_OFFSITE_DIR:-}"
RCLONE_REMOTE="${JARVIS_BACKUP_RCLONE_REMOTE:-}"
if [ -z "$OFFSITE_DIR" ] && [ -z "$RCLONE_REMOTE" ]; then
  die_soft "no destination configured (set JARVIS_BACKUP_OFFSITE_DIR and/or JARVIS_BACKUP_RCLONE_REMOTE) — inert"
fi

# ── stage: latest snapshots + un-snapshotted secrets/config ──────────────────
stamp="$(date +%Y-%m-%d-%H%M)"
stage="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-offsite.XXXXXX")"
bundle=""; enc=""
trap 'rm -rf "$stage" "${bundle:-}" "${enc:-}"' EXIT
chmod 700 "$stage"

copied=0
if [ -d "$SNAP_DIR" ]; then
  # -L dereferences the *-latest symlinks to their real timestamped targets.
  for s in "$SNAP_DIR"/*-latest.*; do
    [ -e "$s" ] || continue
    cp -L "$s" "$stage/" && copied=$((copied+1))
  done
fi
for f in "${HOME}/.jarvis/keys.env" "${HOME}/.jarvis/alerts.env" "${HOME}/.jarvis/faces/faces.json"; do
  [ -f "$f" ] && cp "$f" "$stage/$(basename "$f")" && copied=$((copied+1))
done
[ "$copied" -gt 0 ] || die_soft "nothing to back up (no snapshots/secrets found)"

# Stage the plaintext bundle (contains keys.env) in the 700 backup dir, never
# in world-readable /tmp — it exists only until the encrypt step rm's it.
bundle="${BK_DIR}/.staging-${stamp}.tar.gz"
tar czf "$bundle" -C "$stage" . || die_soft "tar failed"

# ── encrypt (mandatory) ──────────────────────────────────────────────────────
enc=""
if command -v age >/dev/null 2>&1 && command -v age-keygen >/dev/null 2>&1; then
  id="${BK_DIR}/age-identity.txt"
  [ -f "$id" ] || { age-keygen -o "$id" 2>/dev/null && chmod 600 "$id" && log "generated age identity ${id} — COPY OFF-BOX"; }
  recip="$(age-keygen -y "$id" 2>/dev/null)"
  enc="${BK_DIR}/jarvis-backup-${stamp}.tar.gz.age"
  age -r "$recip" -o "$enc" "$bundle" || die_soft "age encrypt failed"
elif command -v gpg >/dev/null 2>&1; then
  pass="${BK_DIR}/backup.pass"
  [ -f "$pass" ] || { head -c 32 /dev/urandom | base64 > "$pass" && chmod 600 "$pass" && log "generated gpg passphrase ${pass} — COPY OFF-BOX"; }
  enc="${BK_DIR}/jarvis-backup-${stamp}.tar.gz.gpg"
  gpg --batch --yes --pinentry-mode loopback --passphrase-file "$pass" \
      -c --cipher-algo AES256 -o "$enc" "$bundle" || die_soft "gpg encrypt failed"
else
  die_soft "no encryptor (install age or gpg) — refusing to push secrets unencrypted"
fi
rm -f "$bundle"   # drop the plaintext bundle immediately
encname="$(basename "$enc")"
encsize="$(du -h "$enc" | cut -f1)"

# ── push ─────────────────────────────────────────────────────────────────────
pushed=0
if [ -n "$OFFSITE_DIR" ]; then
  if mkdir -p "$OFFSITE_DIR" 2>/dev/null && cp "$enc" "$OFFSITE_DIR/$encname" && [ -f "$OFFSITE_DIR/$encname" ]; then
    pushed=$((pushed+1)); log "offsite-dir OK: ${OFFSITE_DIR}/${encname} (${encsize})"
    # retain newest N, prune older
    ls -1t "$OFFSITE_DIR"/jarvis-backup-*.age "$OFFSITE_DIR"/jarvis-backup-*.gpg 2>/dev/null \
      | tail -n +"$((RETENTION+1))" | while read -r old; do rm -f "$old"; done
  else
    log "offsite-dir FAILED: ${OFFSITE_DIR}"
  fi
fi
if [ -n "$RCLONE_REMOTE" ]; then
  if command -v rclone >/dev/null 2>&1; then
    if rclone copy "$enc" "$RCLONE_REMOTE" --quiet 2>>"$LOG" && rclone lsf "$RCLONE_REMOTE" 2>/dev/null | grep -qF "$encname"; then
      pushed=$((pushed+1)); log "rclone OK: ${RCLONE_REMOTE}/${encname} (${encsize})"
      rclone delete "$RCLONE_REMOTE" --min-age "$((RETENTION*24))h" --include 'jarvis-backup-*' --quiet 2>>"$LOG" || true
    else
      log "rclone FAILED: ${RCLONE_REMOTE}"
    fi
  else
    log "rclone remote set but rclone not installed"
  fi
fi

cp "$enc" "${BK_DIR}/jarvis-backup-latest.${enc##*.}" 2>/dev/null || true   # local copy of newest encrypted bundle for verify
rm -f "$enc"

if [ "$pushed" -eq 0 ]; then
  log "ALL destinations FAILED"
  "$NOTIFY" "JARVIS backup failed" "Off-box backup pushed to 0 destinations. Check ${LOG}." urgent >/dev/null 2>&1 || true
  exit 0
fi
log "done — pushed to ${pushed} destination(s)"
exit 0
