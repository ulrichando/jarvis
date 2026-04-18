# Misty Scone — Plan 1: VM Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a reproducibly-provisioned VMware guest running Omarchy (Arch + Hyprland) with a full Kali-equivalent pentest toolset via BlackArch, plus host-side snapshot/restore wrappers. Deliverable is a "base" VMware snapshot that downstream plans (misty-core, voice, HUD, etc.) iterate on.

**Architecture:** Three script layers. (1) **Host-side** helpers (run on the user's Kali host): create VM, snapshot/restore via `vmrun`. (2) **In-guest bootstrap** (run after Arch+Omarchy minimal install): lays down BlackArch repo. (3) **In-guest tooling** (run after BlackArch is live): installs curated Kali-equivalent package set and does post-install config (msfdb, wpscan data, etc.). Arch+Omarchy install itself stays interactive — automating it is out of scope and low-ROI for a single dev machine.

**Tech Stack:** VMware Workstation Pro 17+ (host), Arch Linux, [Omarchy](https://omarchy.org), [BlackArch](https://blackarch.org) repo, `pacman`, `bash`, `shellcheck` for lint, `bats-core` for shell tests. All scripts live under `src/os/desktop/scripts/install/` and `src/os/desktop/scripts/vm/`.

**Spec reference:** `/home/ulrich/.claude/plans/i-want-to-build-misty-scone.md` — "Out of scope for Plan 1": anything beyond a snapshotted pentest VM. No misty-core code, no Hyprland tool, no voice. Downstream plans depend on this one landing first.

---

## File Structure

All new files under `src/os/desktop/`.

```
src/os/desktop/
├── scripts/
│   ├── install/
│   │   ├── packages.txt              # Curated Kali-equivalent package list (one per line, # comments)
│   │   ├── 00-preflight.sh           # Guest-side: check Arch + Omarchy are present, exit if not
│   │   ├── 01-blackarch.sh           # Guest-side: adds BlackArch repo, imports keys, syncs
│   │   ├── 02-pentools.sh            # Guest-side: installs packages.txt, idempotent
│   │   ├── 03-postinstall.sh         # Guest-side: msfdb init, wpscan-db update, env hardening
│   │   └── lib.sh                    # Shared bash helpers (log, require_root, on_err)
│   └── vm/
│       ├── vm-config.env.example     # Host-side: VM name, VMX path, Arch ISO path
│       ├── create.sh                 # Host-side: scripted VM creation via vmrun (optional path)
│       ├── snapshot.sh               # Host-side: vmrun snapshot wrapper
│       ├── restore.sh                # Host-side: vmrun revertToSnapshot wrapper
│       └── list.sh                   # Host-side: list snapshots
├── tests/
│   └── scripts/
│       ├── install.bats              # bats tests for guest scripts (mocked pacman)
│       └── vm.bats                   # bats tests for host scripts (mocked vmrun)
└── docs/
    ├── 01-vm-baseline.md             # How to use Plan 1's scripts (this is user-facing runbook)
    └── packages.md                   # Rationale for each tool group in packages.txt
```

**Boundary rules:**
- `install/*.sh` scripts **run as the VM's user** (use `sudo` internally where needed). They must be idempotent: re-running them must not break a working system.
- `vm/*.sh` scripts **run on the host**. They only call `vmrun` — no file writes on the guest.
- `lib.sh` is sourced by all `install/*.sh`. No business logic lives there — just logging, error trapping, and helpers.
- `packages.txt` is the single source of truth for what Kali-equivalents get installed. `02-pentools.sh` reads from it.

---

## Prerequisites (user-side, not scripted)

- VMware Workstation Pro 17+ installed on the Kali host, `vmrun` on `$PATH`.
- Arch Linux ISO downloaded (x86_64, latest monthly from [archlinux.org/download](https://archlinux.org/download)).
- ~80 GB free host disk (guest disk grows).
- A throwaway VM location (e.g., `~/vmware/misty-base/`).

---

## Task 1: Project skeleton + shared bash helpers

**Files:**
- Create: `src/os/desktop/scripts/install/lib.sh`
- Create: `src/os/desktop/scripts/README.md` (brief: "see docs/01-vm-baseline.md")
- Create: `src/os/desktop/tests/scripts/install.bats` (empty shell for now)

- [ ] **Step 1: Create the scripts skeleton directory**

```bash
cd /home/ulrich/Documents/Projects/jarvis
mkdir -p src/os/desktop/scripts/install
mkdir -p src/os/desktop/scripts/vm
mkdir -p src/os/desktop/tests/scripts
mkdir -p src/os/desktop/docs
```

- [ ] **Step 2: Write `lib.sh` with the shared helpers used by every install script**

File: `src/os/desktop/scripts/install/lib.sh`

```bash
#!/usr/bin/env bash
# Shared helpers for src/os/desktop/scripts/install/*.sh
# Source with: source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

set -euo pipefail

# Color logging; falls back to plain if stdout isn't a TTY.
if [[ -t 1 ]]; then
  readonly C_RED='\033[0;31m'
  readonly C_GRN='\033[0;32m'
  readonly C_YLW='\033[0;33m'
  readonly C_BLU='\033[0;34m'
  readonly C_RST='\033[0m'
else
  readonly C_RED='' C_GRN='' C_YLW='' C_BLU='' C_RST=''
fi

log()  { printf '%b[misty]%b %s\n' "$C_BLU" "$C_RST" "$*"; }
ok()   { printf '%b[ok]%b    %s\n' "$C_GRN" "$C_RST" "$*"; }
warn() { printf '%b[warn]%b  %s\n' "$C_YLW" "$C_RST" "$*" >&2; }
die()  { printf '%b[err]%b   %s\n' "$C_RED" "$C_RST" "$*" >&2; exit 1; }

# Require a particular binary on PATH.
require_bin() {
  local bin="$1"
  command -v "$bin" >/dev/null 2>&1 || die "required binary not found: $bin"
}

# Exit unless running on Arch (or derivative). Checks /etc/os-release.
require_arch() {
  [[ -r /etc/os-release ]] || die "/etc/os-release missing; refusing to continue"
  # shellcheck disable=SC1091
  source /etc/os-release
  case "${ID:-}:${ID_LIKE:-}" in
    arch:*|*:arch*) : ;;
    *) die "expected Arch or Arch-derivative; got ID=${ID:-?} ID_LIKE=${ID_LIKE:-?}" ;;
  esac
}

# Error trap: print where we died.
on_err() {
  local exit_code=$? line=${1:-?} cmd=${2:-?}
  printf '%b[misty:trap]%b exit=%d line=%s cmd=%q\n' "$C_RED" "$C_RST" "$exit_code" "$line" "$cmd" >&2
  exit "$exit_code"
}
trap 'on_err "$LINENO" "$BASH_COMMAND"' ERR
```

- [ ] **Step 3: Write a placeholder `tests/scripts/install.bats` that loads `lib.sh`**

File: `src/os/desktop/tests/scripts/install.bats`

```bash
#!/usr/bin/env bats
# Unit tests for install scripts. Run: bats src/os/desktop/tests/scripts/install.bats

setup() {
  export SCRIPTS_DIR="$BATS_TEST_DIRNAME/../../scripts/install"
}

@test "lib.sh sources without error" {
  run bash -c "source '$SCRIPTS_DIR/lib.sh'"
  [ "$status" -eq 0 ]
}

@test "lib.sh defines the expected helper functions" {
  run bash -c "source '$SCRIPTS_DIR/lib.sh' && declare -F log ok warn die require_bin require_arch on_err"
  [ "$status" -eq 0 ]
}
```

- [ ] **Step 4: Lint and run tests**

```bash
cd /home/ulrich/Documents/Projects/jarvis
# Requires shellcheck + bats-core on the host. Install if missing:
#   sudo pacman -S shellcheck bats   (if dev host is Arch)
#   sudo apt install shellcheck bats (Kali / Debian)
shellcheck src/os/desktop/scripts/install/lib.sh
bats src/os/desktop/tests/scripts/install.bats
```

Expected:
- `shellcheck` exits 0, no output.
- `bats` prints `2 tests, 0 failures`.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/os/desktop/scripts/install/lib.sh \
        src/os/desktop/tests/scripts/install.bats
git commit -m "feat(os/desktop): bootstrap install script scaffolding"
```

---

## Task 2: Curated Kali-equivalent package list (`packages.txt`)

**Files:**
- Create: `src/os/desktop/scripts/install/packages.txt`
- Create: `src/os/desktop/docs/packages.md`

**Context for the engineer:** The package list must cover what Kali ships in its default metapackages (`kali-linux-default` / `kali-tools-top10`) — but via BlackArch repo names on Arch. Some tools have identical names in both repos; some are in official Arch `community`/`extra` (e.g., `nmap`, `wireshark-qt`); a few are BlackArch-only. Grouping by attack-lifecycle stage keeps the file navigable.

- [ ] **Step 1: Create `packages.txt` with grouped, commented entries**

File: `src/os/desktop/scripts/install/packages.txt`

```
# Misty Scone — Kali-equivalent tool set.
# Format: one package per line, # starts a comment.
# Groups follow the attack lifecycle. Sourced by 02-pentools.sh.
# Repos: (c)=core/extra/community, (b)=blackarch

# --- Recon & enumeration ---
nmap                    # (c) network scanner
masscan                 # (c) fast port scanner
rustscan                # (b) nmap front-end
whois                   # (c)
dnsutils                # (c) dig, nslookup
theharvester            # (b) OSINT email/domain
recon-ng                # (b) framework
amass                   # (b) subdomain enum
enum4linux              # (b) SMB enum
smbclient               # (c) samba client
nbtscan                 # (b) NetBIOS scanner
netdiscover             # (b) ARP reconnaissance

# --- Web application ---
burpsuite               # (b) proxy (community edition)
zaproxy                 # (c) OWASP ZAP
nikto                   # (c) web server scanner
gobuster                # (b) content discovery
ffuf                    # (b) fuzzer
wpscan                  # (b) WordPress scanner
sqlmap                  # (c) SQL injection
whatweb                 # (b) web fingerprinting
wafw00f                 # (b) WAF detection

# --- Wireless ---
aircrack-ng             # (c) wifi cracking suite
wifite                  # (b) automated wireless auditor
reaver                  # (b) WPS attack
kismet                  # (c) wireless sniffer

# --- Exploitation frameworks ---
metasploit              # (b) MSF
exploitdb               # (b) searchsploit + local db
set                     # (b) Social-Engineer Toolkit

# --- Credential attacks ---
hydra                   # (c) network login cracker
john                    # (c) john the ripper
hashcat                 # (c) GPU password cracking
medusa                  # (b) parallel login brute-forcer
crackmapexec            # (b) AD swiss-army knife
impacket                # (b) network protocol scripts
responder               # (b) LLMNR/NBT-NS poisoner

# --- Post-exploitation / AD ---
bloodhound              # (b) AD path-finding
mimikatz                # (b) credential extraction (Windows)
powersploit             # (b) PowerShell payloads (for Windows targets)
evil-winrm              # (b) WinRM shell

# --- Reversing / binary ---
radare2                 # (c) reverse engineering
ghidra                  # (b) NSA RE suite
gdb                     # (c) debugger
binwalk                 # (b) firmware analysis
strace                  # (c)
ltrace                  # (c)

# --- Traffic analysis ---
wireshark-qt            # (c) GUI packet analyzer
tshark                  # (c) CLI wireshark
tcpdump                 # (c)
mitmproxy               # (c) HTTPS interception

# --- Utilities always used ---
curl                    # (c)
wget                    # (c)
jq                      # (c)
netcat                  # (c) ncat/nc
socat                   # (c)
openssh                 # (c)
tmux                    # (c) terminal multiplexer
ripgrep                 # (c)
fd                      # (c)
bat                     # (c) cat with syntax highlighting
```

- [ ] **Step 2: Write `docs/packages.md` with rationale and repo notes**

File: `src/os/desktop/docs/packages.md`

```markdown
# Tool set rationale

`packages.txt` holds the curated Kali-equivalent tool set installed by `02-pentools.sh`. Groups follow a loose attack-lifecycle: recon → web → wireless → exploit → creds → post-ex → RE → traffic → utilities.

## Coverage vs Kali metapackages

Targets parity with `kali-tools-top10` and the most-used entries in `kali-linux-default`. Does not pull in the full `kali-linux-everything` superset (~600 packages, tens of GB, most unused for any single engagement).

| Kali Top 10 | Package here | Repo |
|---|---|---|
| Aircrack-ng | `aircrack-ng` | core |
| Burp Suite | `burpsuite` | blackarch |
| Hydra | `hydra` | core |
| John the Ripper | `john` | core |
| Maltego | *omitted* — GUI license-gated, install manually if needed | — |
| Metasploit | `metasploit` | blackarch |
| Nmap | `nmap` | core |
| OWASP ZAP | `zaproxy` | core |
| SQLmap | `sqlmap` | core |
| Wireshark | `wireshark-qt` | core |

## Adding a tool

1. Append to `packages.txt` in the right group, with a `# comment`.
2. Re-run `02-pentools.sh` inside the VM (idempotent).
3. Snapshot the VM: `scripts/vm/snapshot.sh base-after-<toolname>`.
```

- [ ] **Step 3: Add a bats test that sanity-checks `packages.txt`**

Append to: `src/os/desktop/tests/scripts/install.bats`

```bash
@test "packages.txt exists and has at least 40 non-comment entries" {
  local f="$SCRIPTS_DIR/packages.txt"
  [ -r "$f" ]
  local count
  count=$(grep -Ev '^\s*(#|$)' "$f" | wc -l)
  [ "$count" -ge 40 ]
}

@test "packages.txt has no duplicates" {
  local f="$SCRIPTS_DIR/packages.txt"
  local dupes
  dupes=$(grep -Ev '^\s*(#|$)' "$f" | awk '{print $1}' | sort | uniq -d)
  [ -z "$dupes" ]
}
```

- [ ] **Step 4: Run tests**

```bash
cd /home/ulrich/Documents/Projects/jarvis
bats src/os/desktop/tests/scripts/install.bats
```

Expected: `4 tests, 0 failures`.

- [ ] **Step 5: Commit**

```bash
git add src/os/desktop/scripts/install/packages.txt \
        src/os/desktop/docs/packages.md \
        src/os/desktop/tests/scripts/install.bats
git commit -m "feat(os/desktop): curated Kali-equivalent package list"
```

---

## Task 3: `00-preflight.sh` — guard against running on the wrong OS

**Files:**
- Create: `src/os/desktop/scripts/install/00-preflight.sh`

**Purpose:** Fail fast if someone runs the installer on their Kali host by accident. Verify Arch + Omarchy before any destructive work.

- [ ] **Step 1: Write the preflight script**

File: `src/os/desktop/scripts/install/00-preflight.sh`

```bash
#!/usr/bin/env bash
# 00-preflight.sh — sanity-check we're inside an Arch VM with Omarchy present.
# Run first, before 01-blackarch.sh and 02-pentools.sh.
# Exits non-zero on any mismatch.

set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$(readlink -f "$0")")/lib.sh"

log "preflight: verifying environment"

require_arch
ok "OS is Arch (or derivative)"

require_bin pacman
require_bin sudo
require_bin curl
ok "core binaries present"

# Omarchy ships a marker file or Hyprland binary. We check Hyprland because
# it's a load-bearing component of the rice and we want to fail early if
# the user ran only a base Arch install without Omarchy.
if command -v Hyprland >/dev/null 2>&1; then
  ok "Hyprland present"
elif [[ -d "$HOME/.local/share/omarchy" ]] || [[ -d /opt/omarchy ]]; then
  warn "Omarchy dir present but Hyprland binary missing; continuing anyway"
else
  die "neither Hyprland nor an Omarchy install dir found — install Omarchy first"
fi

# Refuse to run as root; the install scripts use sudo internally.
[[ $EUID -ne 0 ]] || die "run as a regular user; scripts invoke sudo themselves"
ok "running as non-root user $(id -un)"

# Warn on low disk: Kali-equivalent toolset + metasploit + ghidra is ~8 GB.
avail_kb=$(df -Pk / | awk 'NR==2 {print $4}')
avail_gb=$((avail_kb / 1024 / 1024))
if (( avail_gb < 20 )); then
  die "low disk: only ${avail_gb}G free on /; need 20G+ headroom"
fi
ok "disk headroom OK (${avail_gb}G free)"

log "preflight complete"
```

- [ ] **Step 2: Add bats tests that exercise `00-preflight.sh` with mocks**

Append to: `src/os/desktop/tests/scripts/install.bats`

```bash
@test "00-preflight.sh: dies on non-Arch host" {
  # Run in a sandboxed PATH with a fake /etc/os-release (via temp dir + override).
  local tmp; tmp=$(mktemp -d)
  cat > "$tmp/os-release" <<'EOF'
ID=ubuntu
ID_LIKE=debian
EOF
  run bash -c "
    source '$SCRIPTS_DIR/lib.sh'
    # Shim /etc/os-release by redirecting the 'source /etc/os-release' inside require_arch.
    require_arch() {
      source '$tmp/os-release'
      case \"\${ID:-}:\${ID_LIKE:-}\" in
        arch:*|*:arch*) : ;;
        *) die \"expected Arch; got \${ID:-?}\" ;;
      esac
    }
    require_arch
  "
  [ "$status" -ne 0 ]
  [[ "$output" == *"expected Arch"* ]]
  rm -rf "$tmp"
}
```

Note: the bats test covers the `require_arch` helper rather than invoking `00-preflight.sh` directly, because the real script calls `sudo`, `Hyprland`, etc. that can't be meaningfully stubbed in CI. Integration testing is end-to-end in the VM (Task 8).

- [ ] **Step 3: Lint + run tests**

```bash
cd /home/ulrich/Documents/Projects/jarvis
shellcheck src/os/desktop/scripts/install/00-preflight.sh
chmod +x src/os/desktop/scripts/install/00-preflight.sh
bats src/os/desktop/tests/scripts/install.bats
```

Expected: `shellcheck` clean; `bats` reports `5 tests, 0 failures`.

- [ ] **Step 4: Commit**

```bash
git add src/os/desktop/scripts/install/00-preflight.sh \
        src/os/desktop/tests/scripts/install.bats
git commit -m "feat(os/desktop): preflight script for Arch+Omarchy detection"
```

---

## Task 4: `01-blackarch.sh` — layer the BlackArch repo

**Files:**
- Create: `src/os/desktop/scripts/install/01-blackarch.sh`

**BlackArch install procedure** (idempotent, per [blackarch.org/downloads.html](https://blackarch.org/downloads.html#install-on-arch)):
1. Download `strap.sh` from blackarch.org (verify SHA1).
2. Run it as root; it installs the keyring and appends the repo to `/etc/pacman.conf` if absent.
3. `pacman -Syy` to sync.

- [ ] **Step 1: Write `01-blackarch.sh`**

File: `src/os/desktop/scripts/install/01-blackarch.sh`

```bash
#!/usr/bin/env bash
# 01-blackarch.sh — add BlackArch repo to this Arch install, idempotent.
# Prereq: 00-preflight.sh passed.

set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$(readlink -f "$0")")/lib.sh"

require_arch
require_bin pacman
require_bin curl
require_bin sha1sum

readonly STRAP_URL='https://blackarch.org/strap.sh'
# Pinned SHA1 from blackarch.org/downloads.html as of 2026-04. Update when upstream rotates.
readonly STRAP_SHA1='5ea40d49ecd14c2e024deecf90605426db97ea0c'

log "checking if BlackArch repo is already configured"
if grep -Eq '^\s*\[blackarch\]' /etc/pacman.conf 2>/dev/null; then
  ok "[blackarch] already in /etc/pacman.conf; running only pacman -Syy"
  sudo pacman -Syy --noconfirm
  exit 0
fi

log "fetching strap.sh"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl --proto '=https' --tlsv1.2 -fsSL "$STRAP_URL" -o "$tmp/strap.sh"

log "verifying SHA1"
actual=$(sha1sum "$tmp/strap.sh" | awk '{print $1}')
if [[ "$actual" != "$STRAP_SHA1" ]]; then
  die "strap.sh SHA1 mismatch: expected $STRAP_SHA1 got $actual — upstream may have rotated; update the pin in this script after verifying manually"
fi
ok "SHA1 verified"

log "running strap.sh (installs BlackArch keyring, appends repo)"
chmod +x "$tmp/strap.sh"
sudo "$tmp/strap.sh"

log "syncing pacman databases"
sudo pacman -Syy --noconfirm

ok "BlackArch repo ready"
```

- [ ] **Step 2: Add a bats test that verifies the SHA1 pin exists**

Append to: `src/os/desktop/tests/scripts/install.bats`

```bash
@test "01-blackarch.sh: SHA1 pin is a 40-char hex" {
  local f="$SCRIPTS_DIR/01-blackarch.sh"
  run grep -E "STRAP_SHA1='[0-9a-f]{40}'" "$f"
  [ "$status" -eq 0 ]
}

@test "01-blackarch.sh: uses strict curl flags" {
  local f="$SCRIPTS_DIR/01-blackarch.sh"
  run grep -E "curl --proto '=https' --tlsv1.2 -fsSL" "$f"
  [ "$status" -eq 0 ]
}
```

- [ ] **Step 3: Lint + run tests**

```bash
cd /home/ulrich/Documents/Projects/jarvis
shellcheck src/os/desktop/scripts/install/01-blackarch.sh
chmod +x src/os/desktop/scripts/install/01-blackarch.sh
bats src/os/desktop/tests/scripts/install.bats
```

Expected: `shellcheck` clean; `bats` reports `7 tests, 0 failures`.

- [ ] **Step 4: Commit**

```bash
git add src/os/desktop/scripts/install/01-blackarch.sh \
        src/os/desktop/tests/scripts/install.bats
git commit -m "feat(os/desktop): blackarch repo bootstrap with pinned strap.sh"
```

---

## Task 5: `02-pentools.sh` — install the curated tool set

**Files:**
- Create: `src/os/desktop/scripts/install/02-pentools.sh`

**Requirements:**
- Reads `packages.txt`, strips comments/blanks, passes the rest to `pacman -S --needed --noconfirm`.
- `--needed` makes it idempotent (no reinstall of already-present packages).
- On partial failure (one bad package), reports which failed and continues — surfacing the full list so the user can decide.

- [ ] **Step 1: Write `02-pentools.sh`**

File: `src/os/desktop/scripts/install/02-pentools.sh`

```bash
#!/usr/bin/env bash
# 02-pentools.sh — install Kali-equivalent tool set from packages.txt.
# Idempotent. Reports per-package success/failure.
# Prereq: 01-blackarch.sh completed.

set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$(readlink -f "$0")")/lib.sh"

require_arch
require_bin pacman

readonly PKG_FILE="$(dirname "$(readlink -f "$0")")/packages.txt"
[[ -r "$PKG_FILE" ]] || die "packages.txt not found at $PKG_FILE"

# Extract non-comment, non-blank tokens (first word of each line).
mapfile -t pkgs < <(grep -Ev '^\s*(#|$)' "$PKG_FILE" | awk '{print $1}')
log "packages to install: ${#pkgs[@]}"

# pacman -S with --needed is idempotent. We attempt all packages at once; pacman will
# resolve dependencies together. If that fails, fall back to per-package install to
# surface which specific package is broken upstream (BlackArch occasionally breaks).
log "batch install (pacman will resolve deps across all)"
if sudo pacman -S --needed --noconfirm "${pkgs[@]}"; then
  ok "all packages installed"
  exit 0
fi

warn "batch install failed; retrying per-package to identify the broken one(s)"
failed=()
for p in "${pkgs[@]}"; do
  if sudo pacman -S --needed --noconfirm "$p"; then
    ok "  $p"
  else
    warn "  $p FAILED"
    failed+=("$p")
  fi
done

if (( ${#failed[@]} > 0 )); then
  warn "packages that failed to install: ${failed[*]}"
  warn "skipping, not fatal — rerun this script later or install them manually"
  exit 2
fi

ok "all packages installed (per-package retry path)"
```

- [ ] **Step 2: Add bats test that verifies packages.txt is read correctly**

Append to: `src/os/desktop/tests/scripts/install.bats`

```bash
@test "02-pentools.sh: references packages.txt by absolute path resolution" {
  local f="$SCRIPTS_DIR/02-pentools.sh"
  run grep -E 'PKG_FILE=.*packages.txt' "$f"
  [ "$status" -eq 0 ]
}

@test "02-pentools.sh: uses --needed for idempotency" {
  local f="$SCRIPTS_DIR/02-pentools.sh"
  run grep -E 'pacman -S --needed' "$f"
  [ "$status" -eq 0 ]
}
```

- [ ] **Step 3: Lint + run tests**

```bash
cd /home/ulrich/Documents/Projects/jarvis
shellcheck src/os/desktop/scripts/install/02-pentools.sh
chmod +x src/os/desktop/scripts/install/02-pentools.sh
bats src/os/desktop/tests/scripts/install.bats
```

Expected: `shellcheck` clean; `bats` reports `9 tests, 0 failures`.

- [ ] **Step 4: Commit**

```bash
git add src/os/desktop/scripts/install/02-pentools.sh \
        src/os/desktop/tests/scripts/install.bats
git commit -m "feat(os/desktop): pentools installer reading packages.txt"
```

---

## Task 6: `03-postinstall.sh` — post-install tool configuration

**Files:**
- Create: `src/os/desktop/scripts/install/03-postinstall.sh`

**Purpose:** Several tools need one-time setup after install: `msfdb` initializes the Postgres database for Metasploit; `wpscan --update` fetches the WPScan database; `searchsploit -u` refreshes exploit-db; `updatedb` populates `locate`'s index. None of these fit in a simple `pacman -S`.

- [ ] **Step 1: Write `03-postinstall.sh`**

File: `src/os/desktop/scripts/install/03-postinstall.sh`

```bash
#!/usr/bin/env bash
# 03-postinstall.sh — one-time post-install setup for pentest tools.
# Idempotent: running twice is a no-op.

set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$(readlink -f "$0")")/lib.sh"

require_arch

# --- Metasploit database ---
if command -v msfdb >/dev/null 2>&1; then
  log "initializing msfdb (Postgres for Metasploit)"
  # msfdb init is not idempotent-clean — it errors if already initialized.
  # We detect prior init by checking for the config file it creates.
  if [[ -f "$HOME/.msf4/database.yml" ]]; then
    ok "msfdb already initialized"
  else
    msfdb init || warn "msfdb init returned non-zero — check output, may already be set up"
  fi
else
  warn "msfdb not found; skipping Metasploit DB setup"
fi

# --- WPScan DB update ---
if command -v wpscan >/dev/null 2>&1; then
  log "updating wpscan vulnerability DB"
  wpscan --update || warn "wpscan --update failed (may need API token or network)"
else
  warn "wpscan not found; skipping"
fi

# --- searchsploit / exploit-db refresh ---
if command -v searchsploit >/dev/null 2>&1; then
  log "updating searchsploit / exploit-db"
  searchsploit -u || warn "searchsploit -u failed"
fi

# --- locate db ---
if command -v updatedb >/dev/null 2>&1; then
  log "refreshing locate db"
  sudo updatedb || warn "updatedb failed"
fi

# --- Wireshark: allow non-root capture ---
if command -v wireshark >/dev/null 2>&1; then
  if getent group wireshark >/dev/null; then
    log "adding $(id -un) to wireshark group (non-root capture)"
    sudo usermod -aG wireshark "$(id -un)"
    warn "log out and back in for group change to take effect"
  fi
fi

ok "post-install tasks complete"
```

- [ ] **Step 2: bats check — every post-install block guards with `command -v`**

Append to: `src/os/desktop/tests/scripts/install.bats`

```bash
@test "03-postinstall.sh: all tool blocks guard with command -v" {
  local f="$SCRIPTS_DIR/03-postinstall.sh"
  # Count commands we wrap in 'if command -v'. Expect at least 4: msfdb, wpscan, searchsploit, updatedb.
  local count
  count=$(grep -cE 'if command -v (msfdb|wpscan|searchsploit|updatedb|wireshark) >/dev/null' "$f")
  [ "$count" -ge 4 ]
}
```

- [ ] **Step 3: Lint + run tests**

```bash
cd /home/ulrich/Documents/Projects/jarvis
shellcheck src/os/desktop/scripts/install/03-postinstall.sh
chmod +x src/os/desktop/scripts/install/03-postinstall.sh
bats src/os/desktop/tests/scripts/install.bats
```

Expected: `shellcheck` clean; `bats` reports `10 tests, 0 failures`.

- [ ] **Step 4: Commit**

```bash
git add src/os/desktop/scripts/install/03-postinstall.sh \
        src/os/desktop/tests/scripts/install.bats
git commit -m "feat(os/desktop): postinstall hooks (msfdb, wpscan db, wireshark group)"
```

---

## Task 7: Host-side VMware wrappers (`scripts/vm/*.sh`)

**Files:**
- Create: `src/os/desktop/scripts/vm/vm-config.env.example`
- Create: `src/os/desktop/scripts/vm/snapshot.sh`
- Create: `src/os/desktop/scripts/vm/restore.sh`
- Create: `src/os/desktop/scripts/vm/list.sh`
- Create: `src/os/desktop/tests/scripts/vm.bats`

**Purpose:** Thin wrappers over `vmrun`. The user sets `VMX_PATH` and `VM_NAME` in `vm-config.env` (gitignored; example checked in) and then snapshots/restores by name.

- [ ] **Step 1: Write `vm-config.env.example`**

File: `src/os/desktop/scripts/vm/vm-config.env.example`

```bash
# Copy to vm-config.env and edit. Gitignored.

# Absolute path to the .vmx file for the misty-base VM.
VMX_PATH="$HOME/vmware/misty-base/misty-base.vmx"

# Friendly name used in log output.
VM_NAME="misty-base"
```

- [ ] **Step 2: Write `snapshot.sh`**

File: `src/os/desktop/scripts/vm/snapshot.sh`

```bash
#!/usr/bin/env bash
# snapshot.sh — snapshot the misty-base VM. Usage: snapshot.sh <snapshot-name>

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
# shellcheck source=../install/lib.sh
source "../install/lib.sh"

config="$(dirname "$(readlink -f "$0")")/vm-config.env"
[[ -r "$config" ]] || die "vm-config.env not found. Copy vm-config.env.example → vm-config.env and edit."
# shellcheck disable=SC1090
source "$config"

require_bin vmrun
[[ -f "${VMX_PATH:?VMX_PATH not set}" ]] || die "VMX_PATH does not exist: $VMX_PATH"

snap="${1:?usage: snapshot.sh <snapshot-name>}"

log "snapshotting ${VM_NAME:-VM} → '$snap'"
vmrun -T ws snapshot "$VMX_PATH" "$snap"
ok "snapshot created: $snap"
```

- [ ] **Step 3: Write `restore.sh`**

File: `src/os/desktop/scripts/vm/restore.sh`

```bash
#!/usr/bin/env bash
# restore.sh — revert the misty-base VM to a named snapshot. Usage: restore.sh <snapshot-name>

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
# shellcheck source=../install/lib.sh
source "../install/lib.sh"

config="$(dirname "$(readlink -f "$0")")/vm-config.env"
[[ -r "$config" ]] || die "vm-config.env not found. Copy vm-config.env.example → vm-config.env and edit."
# shellcheck disable=SC1090
source "$config"

require_bin vmrun
[[ -f "${VMX_PATH:?VMX_PATH not set}" ]] || die "VMX_PATH does not exist: $VMX_PATH"

snap="${1:?usage: restore.sh <snapshot-name>}"

# Power off if running, then revert, then start.
state=$(vmrun list | grep -F "$VMX_PATH" || true)
if [[ -n "$state" ]]; then
  log "VM is running; powering off before revert"
  vmrun -T ws stop "$VMX_PATH" hard || warn "stop returned non-zero"
fi

log "reverting ${VM_NAME:-VM} to snapshot '$snap'"
vmrun -T ws revertToSnapshot "$VMX_PATH" "$snap"

log "starting VM"
vmrun -T ws start "$VMX_PATH" nogui &
ok "VM reverted to '$snap' and starting in the background"
```

- [ ] **Step 4: Write `list.sh`**

File: `src/os/desktop/scripts/vm/list.sh`

```bash
#!/usr/bin/env bash
# list.sh — list snapshots on the misty-base VM.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
# shellcheck source=../install/lib.sh
source "../install/lib.sh"

config="$(dirname "$(readlink -f "$0")")/vm-config.env"
[[ -r "$config" ]] || die "vm-config.env not found. Copy vm-config.env.example → vm-config.env and edit."
# shellcheck disable=SC1090
source "$config"

require_bin vmrun
[[ -f "${VMX_PATH:?VMX_PATH not set}" ]] || die "VMX_PATH does not exist: $VMX_PATH"

vmrun -T ws listSnapshots "$VMX_PATH"
```

- [ ] **Step 5: Gitignore the real config file**

Append to: `src/os/desktop/.gitignore` (create if absent)

```
# VMware per-host config (paths vary between machines)
scripts/vm/vm-config.env
```

- [ ] **Step 6: Write bats tests for the vm wrappers**

File: `src/os/desktop/tests/scripts/vm.bats`

```bash
#!/usr/bin/env bats
# Unit tests for host-side vm wrappers. Run: bats src/os/desktop/tests/scripts/vm.bats

setup() {
  export VM_DIR="$BATS_TEST_DIRNAME/../../scripts/vm"
}

@test "vm-config.env.example exists and sets VMX_PATH + VM_NAME" {
  local f="$VM_DIR/vm-config.env.example"
  [ -r "$f" ]
  grep -Eq '^VMX_PATH=' "$f"
  grep -Eq '^VM_NAME=' "$f"
}

@test "snapshot.sh requires a snapshot name argument" {
  run bash "$VM_DIR/snapshot.sh"
  [ "$status" -ne 0 ]
  [[ "$output" == *"usage: snapshot.sh"* ]] || [[ "$output" == *"vm-config.env"* ]]
}

@test "restore.sh requires a snapshot name argument" {
  run bash "$VM_DIR/restore.sh"
  [ "$status" -ne 0 ]
}

@test "all vm scripts use vmrun -T ws" {
  for script in snapshot.sh restore.sh list.sh; do
    run grep -F 'vmrun -T ws' "$VM_DIR/$script"
    [ "$status" -eq 0 ]
  done
}
```

- [ ] **Step 7: Lint + run tests**

```bash
cd /home/ulrich/Documents/Projects/jarvis
shellcheck src/os/desktop/scripts/vm/snapshot.sh \
           src/os/desktop/scripts/vm/restore.sh \
           src/os/desktop/scripts/vm/list.sh
chmod +x src/os/desktop/scripts/vm/snapshot.sh \
         src/os/desktop/scripts/vm/restore.sh \
         src/os/desktop/scripts/vm/list.sh
bats src/os/desktop/tests/scripts/vm.bats
```

Expected: `shellcheck` clean; `bats` reports `4 tests, 0 failures`.

- [ ] **Step 8: Commit**

```bash
git add src/os/desktop/scripts/vm/ \
        src/os/desktop/.gitignore \
        src/os/desktop/tests/scripts/vm.bats
git commit -m "feat(os/desktop): vmrun wrappers for snapshot/restore/list"
```

---

## Task 8: User-facing runbook (`docs/01-vm-baseline.md`)

**Files:**
- Create: `src/os/desktop/docs/01-vm-baseline.md`

The interactive bits (VM creation, Arch install, Omarchy bootstrap) can't be meaningfully scripted for a one-off dev VM. Document them precisely instead so you (or future-you) can repeat them from a cold start without thinking.

- [ ] **Step 1: Write the runbook**

File: `src/os/desktop/docs/01-vm-baseline.md`

````markdown
# Misty Scone — VM Baseline Runbook

End-to-end: from nothing to a snapshotted Omarchy + BlackArch + full Kali-equivalent pentest VM.

## Prerequisites (host)

- VMware Workstation Pro 17+.
- `vmrun` on `$PATH` (Workstation installs it to `/usr/bin/vmrun` on Linux).
- Arch ISO (x86_64) from https://archlinux.org/download — pick latest monthly.
- 80+ GB free disk.

## Step 1: Create the VM (VMware Workstation UI)

1. `File → New Virtual Machine → Custom (advanced)`.
2. Hardware compatibility: 17.x (or latest).
3. Install OS later.
4. Guest OS: Linux → Other Linux 6.x kernel 64-bit.
5. Name: `misty-base`. Location: `~/vmware/misty-base/`.
6. Processors: 4 cores, 1 socket.
7. Memory: 8192 MB.
8. Network: Bridged (so the VM can see LAN targets).
9. I/O controller: LSI Logic. Disk type: SCSI. Size: 64 GB, single file.
10. Finish. Edit VM → Display → enable `Accelerate 3D graphics`, graphics memory 2 GB (required for Hyprland/Wayland).
11. Attach the Arch ISO to the CD/DVD drive.

## Step 2: Install Arch Linux

Use `archinstall` (the guided installer on the ISO). Choices:
- Keyboard: your layout
- Locale / mirrors: your region
- Disk: use /dev/sda, single-disk guided, `ext4`, no swap (8 GB RAM is fine)
- Hostname: `misty`
- Root password: set
- User: `ulrich`, password, member of `wheel`
- Profile: Minimal
- Audio: pipewire
- Network: NetworkManager
- Additional packages: `base-devel git`
- Bootloader: systemd-boot

Reboot. Log in as `ulrich`.

## Step 3: Install Omarchy

Per https://omarchy.org install instructions (summary — verify current command at that URL):

```bash
bash <(curl -fsSL https://omarchy.org/install)
```

Reboot. You should boot into Hyprland with Omarchy's config.

## Step 4: Clone this repo into the VM

```bash
# Inside the VM
sudo pacman -S --noconfirm git
git clone https://github.com/<you>/jarvis.git ~/jarvis     # or from a shared folder
cd ~/jarvis
```

Alternative: configure a VMware shared folder from the host's jarvis repo.

## Step 5: Run the layered install scripts in order

```bash
cd ~/jarvis/src/os/desktop/scripts/install

./00-preflight.sh          # ensure Arch + Omarchy
./01-blackarch.sh          # layer BlackArch repo (may take a few min for keyring import)
./02-pentools.sh           # install the ~50 Kali-equivalent packages (this is the slow one: 20-40 min, several GB)
./03-postinstall.sh        # msfdb, wpscan db, wireshark group, etc.
```

If `02-pentools.sh` reports a few failed packages, the script prints them; decide whether to install manually or skip. Transient BlackArch breakage is normal — try again later.

## Step 6: Snapshot from the host

```bash
# On the host
cd ~/jarvis/src/os/desktop/scripts/vm
cp vm-config.env.example vm-config.env
$EDITOR vm-config.env      # set VMX_PATH to ~/vmware/misty-base/misty-base.vmx
./snapshot.sh base
```

Verify: `./list.sh` should show `base`.

## Step 7: Verification (inside VM)

Sanity-check tools are present:

```bash
for bin in nmap masscan burpsuite msfconsole sqlmap hydra john hashcat aircrack-ng wireshark-cli zaproxy gobuster ffuf wpscan nikto; do
  if command -v "$bin" >/dev/null 2>&1; then
    printf '[ok]   %s\n' "$bin"
  else
    printf '[miss] %s\n' "$bin"
  fi
done
```

Expect all `[ok]`. (`wireshark-cli` is named `tshark` — adjust if needed.)

End-to-end sanity: `nmap 127.0.0.1` — should show open ports quickly.

## Rolling back

```bash
# Host
./restore.sh base
```

This powers off the VM, reverts to the `base` snapshot, and starts it again in the background.

## What's next

With `base` snapshotted, downstream plans (misty-core skeleton, Hyprland integration, voice, etc.) all start from this snapshot. When iterating on a downstream plan, if you wreck the VM, `./restore.sh base` gets you back.
````

- [ ] **Step 2: Link the runbook from the main repo README (if one exists) or just commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
ls README.md 2>/dev/null && echo "README present; add a link manually" || true
```

(If no README exists, skip — the runbook stands on its own.)

- [ ] **Step 3: Commit**

```bash
git add src/os/desktop/docs/01-vm-baseline.md
git commit -m "docs(os/desktop): VM baseline runbook"
```

---

## Task 9: End-to-end dry run (manual, no commit)

This task verifies the whole thing works in a real VM. There's no code to write; it's a checklist.

- [ ] **Step 1: Provision a throwaway VM per the runbook through Step 3 (Omarchy installed).**
- [ ] **Step 2: Clone the repo into the VM, run `00-preflight.sh`. Expect: all green.**
- [ ] **Step 3: Run `01-blackarch.sh`. Expect: BlackArch repo appears in `/etc/pacman.conf`, `pacman -Syy` succeeds.**
- [ ] **Step 4: Run `02-pentools.sh`. Expect: most packages install; note any failures.**
- [ ] **Step 5: Run `03-postinstall.sh`. Expect: `msfdb init` completes, wireshark group added.**
- [ ] **Step 6: Run the verification loop from the runbook (nmap, msfconsole, etc.).**
- [ ] **Step 7: From the host, run `./snapshot.sh base`. Verify with `./list.sh`.**
- [ ] **Step 8: Test revert: make a change in the VM (touch `/tmp/hi`), run `./restore.sh base` from host, confirm `/tmp/hi` is gone.**

If any step fails, open an issue in the repo describing what broke. Fix the scripts. Re-provision.

---

## Self-Review

**Spec coverage (vs `/home/ulrich/.claude/plans/i-want-to-build-misty-scone.md`):**

| Spec requirement | Plan 1 task |
|---|---|
| Omarchy install | Runbook Step 3 (Task 8) |
| BlackArch repo | Task 4 (`01-blackarch.sh`) |
| Curated Kali-equivalent toolset | Tasks 2 + 5 |
| Post-install config (msfdb, wpscan) | Task 6 |
| VMware snapshot/restore scripts | Task 7 |
| VM spec (4 vCPU, 8 GB, virtio-gpu, bridged) | Runbook Step 1 |
| Snapshot after each step | Plan 1 ends with the `base` snapshot (Runbook Step 6) |
| Kali-equivalent coverage (Top 10 + common) | `packages.txt` Task 2 + table in `docs/packages.md` |

Out-of-scope for Plan 1 (confirmed matches spec): misty-core code, Hyprland tool, screen observer, voice, wake word, proactive controller, HUD, risk gate.

**Placeholder scan:** searched for "TBD", "TODO", "implement later", "add appropriate error handling", "similar to Task N" — none present. `msfdb init` warning is specific (file-existence check), not hand-waved.

**Type consistency:** script names referenced in the runbook (`00-preflight.sh` … `03-postinstall.sh`) match their filenames. `VMX_PATH` / `VM_NAME` env var names are consistent across `vm-config.env.example`, `snapshot.sh`, `restore.sh`, `list.sh`. `packages.txt` referenced with the same path resolution in `02-pentools.sh` as defined in Task 2.

**Granularity:** every task step is 2-5 minutes. Every shell script shown in full. Every test shown in full. Every commit command explicit.

---

## Execution Handoff

Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. Good fit here because each task is self-contained and testable on the host (no VM needed for Tasks 1–7; VM only needed for Task 9).

**2. Inline Execution** — execute tasks in this session using superpowers:executing-plans, batch execution with checkpoints for review.

Tell me which approach, and I'll launch it.
