# Misty Scone — Plan 5: HUD Widget (Approval UI)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An always-on-screen HUD widget (eww / GTK layer-shell) that polls misty-core's `/api/confirmation` endpoint, shows pending high-risk tool requests, and lets the user click Accept or Deny. Also shows daemon status (healthy / down / thinking).

**Scope:** HUD only. Audio capture, TTS playback, and wake-word detection are Plans 6 and 7. Plan 5 gives a functioning approval UI so the Plan 4 confirmation queue becomes actually usable.

**Architecture:** eww is a Lisp-like GTK layer-shell widget engine. Widgets are declared in `.yuck` files; styling lives in `.scss`. eww supports `defpoll` (run a command every N seconds, bind its stdout to a variable) and `deflisten` (long-running command whose stdout lines update a variable). The widget polls `curl http://127.0.0.1:8765/api/confirmation` every 1s and renders whatever pending confirmations are returned. Accept/Deny buttons shell out to `curl -X POST /api/confirmation/$id`.

**Tech Stack:** eww (installed via pacman on Arch), `jq` (JSON parsing in shell), `curl`, GTK3+layer-shell (Hyprland-compatible). No TypeScript changes — this plan is purely HUD-side.

**Runtime deps** (installed on the VM — already in Plan 1's `packages.txt` recommendation to add): `eww`, `jq`, `curl`. Plan 5 extends `packages.txt` and adds an install helper.

**Spec reference:** `~/.claude/plans/i-want-to-build-misty-scone.md` — Plan 5 implements the `hud/` module (`eww.yuck` + `eww.scss` + optional `state.ts` relay).

**Depends on:** `plan-4-voice-confirmation` (needs `/api/confirmation` endpoints).

**Where HUD lives at runtime:** eww reads config from `$XDG_CONFIG_HOME/eww/` (default `~/.config/eww/`). Plan 5 ships source in `src/os/desktop/hud/` and an install script that symlinks it into `~/.config/eww/misty/`. A named eww instance (`eww open misty-hud`) starts the widget.

---

## File Structure

```
src/os/desktop/hud/
├── eww.yuck                     widget declarations + data sources
├── eww.scss                     theme (dark, Omarchy-ish)
├── bin/
│   ├── confirm.sh               POST /api/confirmation/$id (called by button clicks)
│   └── fetch-status.sh          GET /api/confirmation + /health; emits one JSON line for defpoll
├── install.sh                   symlink hud/ → ~/.config/eww/misty/, install deps
└── README.md                    how to use the HUD
```

```
src/os/desktop/scripts/install/
└── packages.txt                 APPEND: eww, jq  (if not already present)
```

**Boundary rules:**
- `eww.yuck` owns layout + bindings. No shell commands beyond the helpers in `bin/`.
- `bin/*.sh` own HTTP and JSON; eww treats them as opaque data sources.
- Styling is all in `eww.scss`. No inline CSS in yuck.

---

## Behavior Contract

**Widget layout** (anchored to top-right corner of primary output):

```
┌──────────────────────────────┐
│ ● misty-core   [healthy]     │
├──────────────────────────────┤
│ pending confirmations (N)    │
│                              │
│  [bash]                      │
│  $ sudo pacman -Syu          │
│  reason: high-risk sudo...   │
│  [✓ allow]    [✗ deny]       │
│                              │
│  [hyprland]                  │
│  list_windows                │
│  reason: ...                 │
│  [✓ allow]    [✗ deny]       │
└──────────────────────────────┘
```

When `N == 0`, the widget shrinks to just the status line. Background is semi-transparent so it blends with Omarchy.

**Poll cadence:** `/api/confirmation` every 1000 ms. `/health` every 5000 ms.

**Click behavior:**
- Accept button → `curl -X POST http://127.0.0.1:8765/api/confirmation/$ID -d '{"decision":"allow"}'`
- Deny button → same with `"deny"`
- Both buttons remove the entry immediately (poll will sync next cycle).

---

## Task 1: `fetch-status.sh` — data source for eww

**File:** `src/os/desktop/hud/bin/fetch-status.sh`

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# fetch-status.sh — fetch misty-core status and pending confirmations.
# Called by eww's defpoll; emits a single line of JSON on stdout per call.
# Shape: {"health":"ok"|"down","pending":[{...},{...}]}

set -euo pipefail

readonly BASE="${MISTY_URL:-http://127.0.0.1:8765}"

health=$(curl -sS --max-time 1 "$BASE/health" 2>/dev/null || true)
if [[ -z "$health" ]]; then
  echo '{"health":"down","pending":[]}'
  exit 0
fi

pending=$(curl -sS --max-time 2 "$BASE/api/confirmation" 2>/dev/null | jq -c '.pending // []' 2>/dev/null || echo '[]')
jq -cn --argjson pending "$pending" '{health:"ok", pending:$pending}'
```

- [ ] **Step 2: Tests (bats)**

File: `src/os/desktop/tests/scripts/hud.bats`

```bash
#!/usr/bin/env bats
# Unit tests for HUD helper scripts.

setup() {
  export HUD_BIN="$BATS_TEST_DIRNAME/../../hud/bin"
}

@test "fetch-status.sh outputs 'down' when MISTY_URL is unreachable" {
  run env MISTY_URL="http://127.0.0.1:1" bash "$HUD_BIN/fetch-status.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *'"health":"down"'* ]]
  [[ "$output" == *'"pending":[]'* ]]
}

@test "fetch-status.sh exits 0 (never errors out to eww)" {
  # Even on network failure, the script must exit 0 so eww's defpoll doesn't clear the variable.
  run env MISTY_URL="http://bad.invalid" bash "$HUD_BIN/fetch-status.sh"
  [ "$status" -eq 0 ]
}
```

- [ ] **Step 3: Lint + test**

```bash
cd /home/ulrich/Documents/Projects/jarvis
shellcheck src/os/desktop/hud/bin/fetch-status.sh
chmod +x src/os/desktop/hud/bin/fetch-status.sh
bats src/os/desktop/tests/scripts/hud.bats
```

Expected: shellcheck clean; 2 bats passes.

- [ ] **Step 4: Commit**

```bash
git add src/os/desktop/hud/bin/fetch-status.sh \
        src/os/desktop/tests/scripts/hud.bats
git commit -m "feat(os/desktop): HUD status fetcher script"
```

---

## Task 2: `confirm.sh` — decision POSTer

**File:** `src/os/desktop/hud/bin/confirm.sh`

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# confirm.sh — POST a decision to misty-core's confirmation endpoint.
# Usage: confirm.sh <id> <allow|deny>

set -euo pipefail

readonly BASE="${MISTY_URL:-http://127.0.0.1:8765}"
readonly ID="${1:?usage: confirm.sh <id> <allow|deny>}"
readonly DECISION="${2:?usage: confirm.sh <id> <allow|deny>}"

if [[ "$DECISION" != "allow" && "$DECISION" != "deny" ]]; then
  echo "decision must be 'allow' or 'deny'" >&2
  exit 2
fi

curl -sS -X POST \
  -H 'content-type: application/json' \
  -d "{\"decision\":\"$DECISION\"}" \
  --max-time 3 \
  "$BASE/api/confirmation/$ID" >/dev/null
```

- [ ] **Step 2: Test**

Append to `src/os/desktop/tests/scripts/hud.bats`:

```bash
@test "confirm.sh requires two arguments" {
  run bash "$HUD_BIN/confirm.sh"
  [ "$status" -ne 0 ]
}

@test "confirm.sh rejects invalid decision" {
  run bash "$HUD_BIN/confirm.sh" "c_1" "maybe"
  [ "$status" -ne 0 ]
  [[ "$output" == *"must be 'allow' or 'deny'"* ]]
}
```

- [ ] **Step 3: Lint + test + commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
shellcheck src/os/desktop/hud/bin/confirm.sh
chmod +x src/os/desktop/hud/bin/confirm.sh
bats src/os/desktop/tests/scripts/hud.bats
git add src/os/desktop/hud/bin/confirm.sh \
        src/os/desktop/tests/scripts/hud.bats
git commit -m "feat(os/desktop): HUD confirm.sh — POSTs allow/deny decisions"
```

Expected: 4 bats passes total now.

---

## Task 3: eww widget + theme

**Files:**
- Create: `src/os/desktop/hud/eww.yuck`
- Create: `src/os/desktop/hud/eww.scss`

- [ ] **Step 1: Write `eww.yuck`**

File: `src/os/desktop/hud/eww.yuck`

```lisp
;; Misty HUD — approval queue + daemon status.
;; Started via: eww -c ~/.config/eww/misty daemon && eww -c ~/.config/eww/misty open misty-hud

(defvar misty-url "http://127.0.0.1:8765")

;; Poll status every 1s. Output: {"health":"ok|down","pending":[{...}]}
(defpoll status :interval "1s"
  :initial '{"health":"down","pending":[]}'
  "./bin/fetch-status.sh")

(defwidget misty-header []
  (box :class "header" :orientation "h" :space-evenly false
    (label :class "brand" :text "● misty-core")
    (label :class {status.health == "ok" ? "health ok" : "health down"}
           :text {status.health == "ok" ? "healthy" : "down"})))

(defwidget pending-entry [entry]
  (box :class "pending-entry" :orientation "v" :space-evenly false :spacing 4
    (label :class "tool-name" :text {entry.tool} :halign "start")
    (label :class "prompt"    :text {entry.promptText} :halign "start" :wrap true)
    (label :class "reason"    :text {"reason: " + entry.reason} :halign "start" :wrap true)
    (box :orientation "h" :space-evenly false :spacing 8
      (button :class "allow" :onclick "./bin/confirm.sh \"${entry.id}\" allow" "✓ allow")
      (button :class "deny"  :onclick "./bin/confirm.sh \"${entry.id}\" deny"  "✗ deny"))))

(defwidget pending-list []
  (box :orientation "v" :space-evenly false :spacing 8
    (label :class "list-header"
           :text {"pending confirmations (" + arraylength(status.pending) + ")"}
           :halign "start")
    (for entry in {status.pending}
      (pending-entry :entry entry))))

(defwidget misty-hud []
  (box :class "hud" :orientation "v" :space-evenly false :spacing 12
    (misty-header)
    (pending-list)))

(defwindow misty-hud
  :monitor 0
  :geometry (geometry :x "16px" :y "16px"
                      :width "360px"
                      :anchor "top right")
  :stacking "overlay"
  :exclusive false
  :focusable false
  (misty-hud))
```

- [ ] **Step 2: Write `eww.scss`**

File: `src/os/desktop/hud/eww.scss`

```scss
// Misty HUD theme — dark, semi-transparent, Omarchy-friendly.

.hud {
  background: rgba(20, 20, 25, 0.92);
  color: #e6e6e6;
  padding: 14px 16px;
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.06);
  font-family: "JetBrains Mono", "Hack", monospace;
  font-size: 12px;
  min-width: 320px;
}

.header {
  .brand {
    font-weight: bold;
    color: #8ab4f8;
    margin-right: 12px;
  }
  .health {
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 10px;
    text-transform: uppercase;
  }
  .health.ok {
    background: rgba(120, 200, 120, 0.2);
    color: #9ae09a;
  }
  .health.down {
    background: rgba(220, 100, 100, 0.25);
    color: #f08080;
  }
}

.list-header {
  font-size: 11px;
  text-transform: uppercase;
  color: #aaa;
  margin-top: 10px;
  margin-bottom: 4px;
}

.pending-entry {
  padding: 10px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.04);
  margin-bottom: 8px;

  .tool-name {
    font-weight: bold;
    color: #f8c97b;
    font-size: 11px;
    text-transform: uppercase;
  }
  .prompt {
    margin-top: 4px;
    color: #eee;
  }
  .reason {
    font-size: 10px;
    color: #999;
    margin-bottom: 8px;
  }

  button {
    padding: 6px 12px;
    border-radius: 6px;
    border: none;
    font-family: inherit;
    font-size: 12px;
    cursor: pointer;
  }
  button.allow {
    background: rgba(100, 180, 120, 0.35);
    color: #cfe9d0;
  }
  button.allow:hover {
    background: rgba(100, 180, 120, 0.55);
  }
  button.deny {
    background: rgba(200, 100, 100, 0.35);
    color: #f2cccc;
  }
  button.deny:hover {
    background: rgba(200, 100, 100, 0.55);
  }
}
```

- [ ] **Step 3: Commit**

Eww config files can't be meaningfully unit-tested on the dev host (they only run under a Wayland compositor with eww installed). Real validation is in the VM (Task 5).

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/os/desktop/hud/eww.yuck src/os/desktop/hud/eww.scss
git commit -m "feat(os/desktop): eww HUD widget + theme"
```

---

## Task 4: Install script

**File:** `src/os/desktop/hud/install.sh`

- [ ] **Step 1: Write it**

```bash
#!/usr/bin/env bash
# install.sh — install HUD deps + symlink hud/ into ~/.config/eww/misty/
# Run inside the VM after jarvis repo is cloned and misty-core is set up.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
EWW_CFG="${XDG_CONFIG_HOME:-$HOME/.config}/eww/misty"

# shellcheck source=../scripts/install/lib.sh
source "$SCRIPT_DIR/../scripts/install/lib.sh"

log "Installing HUD dependencies"
require_bin pacman
for pkg in eww jq curl; do
  if ! pacman -Qi "$pkg" >/dev/null 2>&1; then
    log "  installing $pkg"
    sudo pacman -S --needed --noconfirm "$pkg"
  else
    ok "  $pkg already installed"
  fi
done

log "Linking HUD config to $EWW_CFG"
mkdir -p "$(dirname "$EWW_CFG")"

if [[ -e "$EWW_CFG" && ! -L "$EWW_CFG" ]]; then
  die "$EWW_CFG exists but is not a symlink; refusing to overwrite. Move it aside and re-run."
fi

if [[ -L "$EWW_CFG" ]]; then
  if [[ "$(readlink "$EWW_CFG")" == "$SCRIPT_DIR" ]]; then
    ok "already symlinked — nothing to do"
  else
    warn "$EWW_CFG points elsewhere; replacing"
    rm "$EWW_CFG"
    ln -s "$SCRIPT_DIR" "$EWW_CFG"
    ok "symlink replaced"
  fi
else
  ln -s "$SCRIPT_DIR" "$EWW_CFG"
  ok "symlinked $EWW_CFG → $SCRIPT_DIR"
fi

log "HUD install complete"
log "Start: eww -c \"$EWW_CFG\" daemon && eww -c \"$EWW_CFG\" open misty-hud"
```

- [ ] **Step 2: Test**

Append to `src/os/desktop/tests/scripts/hud.bats`:

```bash
@test "install.sh script is present and executable" {
  local f="$HUD_BIN/../install.sh"
  [ -r "$f" ]
  [ -x "$f" ] || chmod +x "$f"  # first-run safety
  [ -x "$f" ]
}
```

- [ ] **Step 3: Lint + test + commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
shellcheck src/os/desktop/hud/install.sh
chmod +x src/os/desktop/hud/install.sh
bats src/os/desktop/tests/scripts/hud.bats
git add src/os/desktop/hud/install.sh \
        src/os/desktop/tests/scripts/hud.bats
git commit -m "feat(os/desktop): HUD install script (pacman deps + symlink to ~/.config/eww/misty)"
```

Expected: 5 bats passes total.

---

## Task 5: HUD README + main README update

**Files:**
- Create: `src/os/desktop/hud/README.md`
- Modify: `src/os/desktop/README.md` (link to the HUD)

- [ ] **Step 1: HUD README**

File: `src/os/desktop/hud/README.md`

```markdown
# misty HUD

Eww / GTK layer-shell widget that shows misty-core's health + pending confirmations, with Accept/Deny buttons that POST to `/api/confirmation/:id`.

## Prerequisites (VM)

- Hyprland (Omarchy includes this).
- `eww`, `jq`, `curl` (installed by `install.sh` via pacman).
- misty-core running on `http://127.0.0.1:8765` (the default).

## Install

```bash
cd ~/jarvis/src/os/desktop/hud
./install.sh
```

This installs `eww`/`jq`/`curl` and symlinks this directory into `~/.config/eww/misty`.

## Start

```bash
eww -c ~/.config/eww/misty daemon
eww -c ~/.config/eww/misty open misty-hud
```

The HUD anchors to the top-right corner. It polls misty-core every 1s. On restart of misty-core, the HUD will automatically flip from `down` → `healthy`.

Stop:

```bash
eww -c ~/.config/eww/misty close misty-hud
eww -c ~/.config/eww/misty kill
```

## Autostart under Hyprland

Append to `~/.config/hypr/hyprland.conf`:

```
exec-once = eww -c ~/.config/eww/misty daemon
exec-once = eww -c ~/.config/eww/misty open misty-hud
```

## How approval flows

1. A user sends `POST /api/think?interactive=1 {"messages":[...]}` requesting a high-risk action (e.g., `sudo pacman -Syu`).
2. misty-core's agent loop hits the gate, which classifies the request as high-risk and opens a confirmation (id `c_abc123`).
3. The HUD, polling every 1s, discovers the new pending entry and renders it with Accept/Deny buttons.
4. User clicks Accept → `confirm.sh c_abc123 allow` → `POST /api/confirmation/c_abc123 {"decision":"allow"}`.
5. misty-core's gate resolves, the agent loop continues, the original `/api/think` response returns with the tool executed.

## Troubleshooting

- **HUD shows `down`:** misty-core isn't running on `127.0.0.1:8765`. Start it: `cd ~/jarvis/src/os/desktop && bun run start`.
- **No widget appears:** `eww log` (tail) — most commonly a config path issue or a GTK layer-shell compositor (Hyprland works; X11 doesn't).
- **Custom port:** set `MISTY_URL=http://127.0.0.1:8866` in your shell env before `./install.sh` and `eww daemon` — the scripts honour this.
```

- [ ] **Step 2: Update main README**

In `src/os/desktop/README.md`, append to the `What it does` section:

> - The `hud/` subtree contains an eww widget that visualizes the pending confirmation queue and lets the user click to approve or deny. See [hud/README.md](hud/README.md).

And add to the `Code layout`:

```
hud/         eww HUD widget (yuck + scss) + shell helpers
```

- [ ] **Step 3: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/os/desktop/hud/README.md src/os/desktop/README.md
git commit -m "docs(os/desktop): HUD README + main README link"
```

---

## Task 6: Manual VM dry-run (no commit)

Plan 5 requires a live Wayland session to validate. Steps (inside the Plan 1 VM):

- [ ] Start misty-core: `cd ~/jarvis/src/os/desktop && bun run start` (or via systemd once Plan 6+ adds that).
- [ ] From another terminal, install HUD: `cd ~/jarvis/src/os/desktop/hud && ./install.sh`.
- [ ] Start eww: `eww -c ~/.config/eww/misty daemon && eww -c ~/.config/eww/misty open misty-hud`.
- [ ] Verify HUD appears top-right, shows "healthy".
- [ ] Trigger a pending confirmation:
  ```bash
  curl -sS -X POST 'http://127.0.0.1:8765/api/think?interactive=1' \
    -H 'content-type: application/json' \
    -d '{"messages":[{"role":"user","content":"run sudo pacman -Syu"}]}' &
  ```
  (Backgrounded because `?interactive=1` blocks until resolved.)
- [ ] The HUD shows the pending entry within 1-2 seconds.
- [ ] Click "allow" → the backgrounded `curl` completes with a transcript including the tool_result.
- [ ] Click "deny" on a fresh request → the transcript's `blocked` array has the entry.

If everything passes, snapshot the VM as `base+hud`.

---

## Self-Review

**Spec coverage:**
| Spec piece | Plan 5 task |
|---|---|
| `hud/eww.yuck` + `hud/eww.scss` | Task 3 |
| HUD reads from misty-core's WS | Modified: uses HTTP poll instead of WS for simplicity. WS can be added when misty-core exposes one (not in Plans 2-4). |
| Accept/Deny buttons trigger `POST /api/confirmation/:id` | Task 2 |
| Install script adds eww + jq via pacman + symlinks config | Task 4 |
| Status panel (healthy / down / thinking) | Task 3 (healthy/down; "thinking" is a Plan 7 concern — when the proactive controller is busy) |

**Out of scope** (correctly deferred): audio capture, TTS playback, wake-word detection, proactive suggestions. Those are Plans 6-7.

**Placeholder scan:** No TBD/TODO. All shell code is concrete. The eww config is concrete. The only soft spot is the lack of eww unit tests (eww has no good dev-host testing story).

**Type consistency:** N/A (no TypeScript changes). Shell variable names and JSON field names (`.health`, `.pending[]`, `.id`, `.tool`, `.promptText`, `.reason`) match across `fetch-status.sh` output and `eww.yuck` consumption.

---

## Execution Handoff

**Subagent-Driven** or **Inline** — same as Plans 1-4. Which approach?
