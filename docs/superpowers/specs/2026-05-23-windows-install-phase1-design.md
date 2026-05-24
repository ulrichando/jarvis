# Windows install — Phase 1 (CLI + Desktop + voice deps)

**Date:** 2026-05-23
**Status:** shipping (this commit)
**Author:** Ulrich + Claude
**Scope:** `install.ps1` (new), `README.md` install section, this spec.

## TL;DR

Windows users can now bootstrap JARVIS with one PowerShell line:

```powershell
iwr -useb https://raw.githubusercontent.com/ulrichando/jarvis/master/install.ps1 | iex
```

This mirrors the existing Linux curl-pipe pattern. Phase 1 ships everything
that is already cross-platform: prereqs via winget, git clone, Python venv
+ voice-agent dependencies, Bun for CLI + Web, the Tauri Desktop build,
a Start Menu shortcut, and the `.env` template. The voice-agent's
runtime service registration is **explicitly deferred** to Phase 2 with
clear log lines explaining why and what to do meanwhile.

After Phase 1: CLI + Desktop UI are fully usable on Windows. Voice
operation still requires WSL2 with `install.sh`. Phase 2 closes that gap.

## Why a Phase 1 / Phase 2 split

The voice-agent has three irreducible Linux dependencies today:

1. **PipeWire / WirePlumber** — the L1 echo-cancel module
   (`module-echo-cancel` with the tuned WebRTC AEC3 args) is a PipeWire
   module. Windows has no PipeWire. The L2 (in-process WebRTC APM) and
   L3 (DTLN neural) layers of JARVIS's AEC cascade are cross-platform
   and would still work, but the agent currently assumes L1 is present.
2. **`sdnotify` + systemd notify-protocol** — `jarvis-voice-agent.service`
   is `Type=notify` with `WatchdogSec=120s`. The agent imports `sdnotify`
   unconditionally at startup (`jarvis_agent.py:5358`,
   `resilience/watchdog.py:19`) and would error before serving its first
   turn on a host without a systemd notify socket. Per CLAUDE.md
   ("Voice-agent rules"), `sdnotify` is in `requirements.txt` and the
   service is the supported run path.
3. **`xdotool` + X11** — `computer_use` ships click / type / key /
   screenshot helpers that shell out to `xdotool` + `xdpyinfo`. The
   Windows equivalent is `pyautogui` plus a `mss` / `pygetwindow` stack,
   but the call sites haven't been platform-abstracted yet.

Refactoring those three behind cross-platform shims is a Phase 2
project — it's a multi-day code change touching the audio stack, the
service-control surface, and the `computer_use` action layer. Trying to
land it inside the installer would scope-creep this commit by an order
of magnitude.

Phase 1 ships the **cross-platform pieces** that are valuable today:

- The CLI works fully on Windows — it's a Bun/TypeScript program with
  no native dependencies and no audio path.
- The Tauri Desktop UI builds cleanly on Windows once MSVC tooling is
  present (Tauri 2 has first-class Windows support via WebView2).
- The Web app (Next.js) is fully cross-platform.
- The voice-agent's *deps* install fine on Windows (livekit-agents is
  pure Python; the Linux-only call sites just sit dormant until the
  agent tries to start).

That gives Windows users a working CLI + Desktop today, plus an
already-prepared Python venv so Phase 2's flip is a code change, not
an installer change.

## What install.ps1 does — function-by-function mapping

The structure mirrors `install.sh` (649 lines, 19 functions) section
for section. Each Linux function maps to one of three Windows fates:

| install.sh fn | install.ps1 fn | Status | Notes |
|---|---|---|---|
| `detect_invocation` | `Get-Invocation` | Ported | curl-pipe vs local-checkout detection. Uses `$PSCommandPath` instead of `${BASH_SOURCE[0]}`. |
| `have` | `Test-Command` | Ported | One-liner wrapping `Get-Command -ErrorAction SilentlyContinue`. |
| `check_prereqs` | `Test-Prereqs` | Ported + extended | Adds winget-based auto-install via `-AutoInstall`. Probes for MSVC Build Tools (Tauri prereq on Windows). |
| `clone_or_update` | `Sync-Repo` | Ported | Same `git -C ... fetch / pull --ff-only` flow. |
| `install_cli` | `Install-Cli` | Ported + replaced | `bun install` step is identical. The Linux script symlinks `bin/jarvis` to `~/.local/bin/`. On Windows, symlinks need Developer Mode or admin, so we write `.cmd` shims to `%LOCALAPPDATA%\jarvis\bin\` and add that dir to the user PATH. |
| `install_web` | `Install-Web` | Ported | Identical: `bun install` in `src/web`. |
| `install_voice_agent` | `Install-VoiceAgent` | Ported | Creates venv at `.venv\` (Windows layout: `Scripts\python.exe`, not `bin/python`). Installs `requirements.txt`. |
| `install_playwright_chromium` | `Install-PlaywrightChromium` | Ported | Cache dir is `%LOCALAPPDATA%\ms-playwright` instead of `~/.cache/ms-playwright`. |
| `install_systemd_units` | `Install-WindowsVoiceServices` | **Deferred** | Phase 2. Logs a clear "voice-agent service not yet supported on native Windows" message + the workaround (WSL2 + install.sh). |
| `install_bubblewrap` | `Install-BashSandbox` | **Deferred** | bubblewrap is a Linux user-namespace sandbox; the Windows analogue (AppContainer / Sandbox / Job Objects) is a Phase 2 design item. The bash tool will fall back to unsandboxed cmd.exe / pwsh.exe when invoked from a Windows JARVIS process — also Phase 2. |
| `generate_bridge_token` | `New-BridgeToken` | Ported | Replaces `head -c 32 /dev/urandom \| base64` with `[System.Security.Cryptography.RandomNumberGenerator]`. NTFS ACL set to user-only via `Set-WindowsAclUserOnly` (chmod 600 equivalent). |
| `setup_livekit_keys` | `Set-LiveKitKeys` | Ported (partial) | Reads keys from `voice-agent\.env` and writes the YAML, same as Linux. **Cannot generate fresh keys** because the repo's bundled `livekit-server.bin` is a Linux ELF and won't run on Windows. Falls through to clear instructions (use WSL2 to generate, or grab `livekit-server.exe` from the LiveKit GitHub releases). |
| `install_audio_profile` | `Install-AudioProfile` | **Deferred / N/A** | The PipeWire config has no Windows analogue. Windows handles mic/speaker coexistence via WASAPI shared mode at the OS level. |
| `install_echo_cancel_aec` | `Install-EchoCancel` | **Deferred / N/A** | L1 PipeWire WebRTC AEC3 is module-based. L2 + L3 of the AEC cascade are cross-platform and will activate automatically once Phase 2 wires the voice-agent. |
| `check_computer_use_deps` | `Test-ComputerUseDeps` | Ported (replaced) | Probes for `mss` (cross-platform) + `pyautogui` (Windows equivalent of xdotool). Notes that `xdotool` / `xdpyinfo` / `python3-pyatspi` are X11-only and N/A on Windows. |
| `install_desktop` | `Install-Desktop` | Ported | Same `npm install` + `npm run build` + `cargo build --release` flow. The binary is `jarvis-desktop.exe` on Windows. |
| `install_desktop_entry` | `Install-StartMenuShortcut` | Ported (replaced) | Creates a `.lnk` in the user's Start Menu via the `WScript.Shell` COM object instead of a freedesktop `.desktop` file. |
| `setup_env_template` | `Set-EnvTemplate` | Ported | Same template, written with UTF-8 encoding. `chmod 600` becomes a user-only NTFS ACL. |
| `print_summary` | `Write-Summary` | Ported | Tailored to surface the Phase 1 / Phase 2 status table. |
| `main` | `Invoke-Main` | Ported | Same orchestration order; honors `-DryRun` and `-Skip*` switches. |

## User-facing contract

After running `iwr -useb https://...install.ps1 | iex`, the user should
end up with:

1. The repo cloned to `%USERPROFILE%\Documents\Projects\jarvis`
   (override with `-InstallDir <path>`).
2. A `.env` template at the repo root, with user-only ACL, ready for
   API-key fill-in.
3. A `jarvis.cmd` launcher in `%LOCALAPPDATA%\jarvis\bin\`, on the user
   PATH after a shell restart.
4. A `JARVIS.lnk` Start Menu shortcut launching the Tauri desktop binary.
5. A bridge auth token at `%USERPROFILE%\.jarvis\local-api-token.env`
   plus an `.env.local` plumb-through for the web app.
6. Python venv at `src\voice-agent\.venv\` with all deps installed.

Things the user CANNOT do until Phase 2:

- Start the voice-agent as a service. (No Task Scheduler entry created.
  Manual `python jarvis_agent.py` will fail at import on `sdnotify` /
  PipeWire / xdotool call sites.)
- Use voice barge-in, AEC, computer_use's mouse/keyboard helpers, or
  the `terminal` tool's xdotool path.

The summary block at end-of-install spells this out so users don't expect
voice to "just work."

## Phase 2 roadmap (separate project)

The Phase 1 installer is the easy half. Phase 2 will:

1. **Audio platform-abstraction** — wrap the L1 PipeWire module load in
   a Linux-only branch, surface the L2 + L3 layers as the default on
   Windows (they're already cross-platform).
2. **Service-control shim** — replace direct `sdnotify` imports with a
   platform-aware "notify" helper (no-op on Windows, sdnotify on Linux).
   Register a Task Scheduler entry on Windows for the voice-agent.
3. **Input automation shim** — abstract `xdotool` calls behind a
   `desktop_input` module that resolves to `pyautogui` on Windows,
   `xdotool` on Linux X11, and `ydotool` / `wlrctl` on Wayland.
4. **Screen capture** — `mss` is already cross-platform; the
   `computer_use` Linux-only branches just need detection.
5. **bash tool sandbox** — design and ship an AppContainer-equivalent
   for the bash tool on Windows (or document the unsandboxed fallback
   loudly and gate the dangerous-command surface accordingly).
6. **LiveKit server** — bundle `livekit-server.exe` alongside the Linux
   binary or document the Windows download path.

Each of these is independent of the others and the installer. The
installer just needs to flip `Install-WindowsVoiceServices` from
"deferred" to a real Task Scheduler register call once Phase 2 ships.

## Open questions for review

Three things I flagged during the port that warrant a second look before
the next phase:

1. **Bridge token timing** — the Linux installer plumbs the token into
   `src/web/.env.local` only if that file already exists. On a fresh
   Windows install (no Web dev session has happened yet), the file
   may not exist when `New-BridgeToken` runs. The token gets written to
   `~/.jarvis/local-api-token.env` regardless, and the Web app will
   read from there on first boot — but worth a smoke test on a fresh
   VM to confirm there's no auth-skew on the user's first `bun dev`.
2. **Tauri MSI signing** — `cargo build --release` produces an
   unsigned `jarvis-desktop.exe`. The Tauri bundler can produce a
   signed `.msi` via `cargo tauri build` if `bundle.active: true` and
   `windows.signCommand` are set in `tauri.conf.json`. Phase 1 ships
   the unsigned `.exe` to match the Linux installer's behaviour (which
   also doesn't sign). Windows SmartScreen will warn on first launch.
   Decision needed: do we add a signing pipeline for Windows now, or
   defer until we have a code-signing cert?
3. **MSVC vs GNU toolchain** — Tauri 2 on Windows is documented as
   MSVC-only (the bundled `webview2-com` crate depends on MSVC ABI
   for COM interop). The installer asserts MSVC Build Tools are
   present via a `Test-Path 'C:\Program Files (x86)\Microsoft Visual Studio'`
   check, but that's heuristic. A clean fail-fast would be to actually
   probe `link.exe` or `cl.exe` on PATH. Worth tightening if we hit
   a "cargo build succeeds with stub rustc-link errors" support load.

## Verification

- `pwsh -Command [System.Management.Automation.Language.Parser]::ParseFile('install.ps1', [ref]$null, [ref]$null)` — passes.
- 29 functions defined, matches the 19 install.sh functions + helpers
  (`Test-Command`, `Invoke-Step`, `Install-ViaWinget`, output helpers,
  `Set-WindowsAclUserOnly`, `Install-StartMenuShortcut`,
  `Install-WindowsVoiceServices`, `Install-BashSandbox`).
- README install section updated with side-by-side Linux + Windows
  one-liners and a Phase-1 status note.
- No Linux-only deps attempted (no bubblewrap install, no systemd
  unit copy, no PipeWire config touch).
- No admin-elevation paths in Phase 1 (user-scoped PATH change,
  user-scoped Start Menu, user-scoped venv, user-scoped .jarvis dir).
- No edits to `src/cli/**`, `src/voice-agent/**`, `src/desktop-tauri/**`,
  or `src/web/**`.

## Out of scope (deliberately)

- Refactoring the voice-agent's Linux-only imports — Phase 2.
- A Windows-native LiveKit server binary — bundle later or document.
- An MSI / Squirrel installer for the Tauri desktop — separate decision.
- Auto-installing MSVC Build Tools (heavy, license-gated, +5 GB) —
  print install command instead, let the user opt in.
- WSL2 detection + redirect — keeping install.ps1 strictly native; users
  who want WSL2 install via WSL2 + install.sh, which is unchanged.
