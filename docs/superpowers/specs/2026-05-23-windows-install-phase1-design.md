# Windows install — Phase 1 (CLI + Desktop + voice deps)

**Date:** 2026-05-23 (rewritten 2026-05-24)
**Status:** shipping (this commit)
**Author:** Ulrich + Claude
**Scope:** `install.ps1` (rewritten), `install.cmd` (new), `install.sh` (minimal uv edit), `scripts/check-windows-footguns.py` (new), `src/voice-agent/tests/test_windows_footguns_checker.py` (new), `README.md` install section, this spec.

## TL;DR

Windows users can now bootstrap JARVIS with one line in either shell:

```powershell
# PowerShell
iex (irm https://raw.githubusercontent.com/ulrichando/jarvis/master/install.ps1)
```

```cmd
:: CMD
curl -fsSL https://raw.githubusercontent.com/ulrichando/jarvis/master/install.cmd -o install.cmd && install.cmd && del install.cmd
```

This mirrors the existing Linux curl-pipe pattern. Phase 1 ships everything that is cross-platform today: PortableGit bundling (so the bash tool works), `uv` for Python provisioning + venv (replacing `python -m venv + pip`), portable Node.js to avoid UAC, Bun for CLI + Web, the Tauri Desktop build, a Start Menu shortcut, a stage protocol for future driver UIs, and the `.env` template.

The voice-agent's runtime service registration is **explicitly deferred** to Phase 2 with clear log lines explaining why and what to do meanwhile. After Phase 1: CLI + Desktop UI are fully usable on Windows. Voice operation still requires WSL2 with `install.sh`. Phase 2 closes that gap.

A new `scripts/check-windows-footguns.py` is the regression catcher that keeps the gap from re-opening: it grep-scans for Linux-only call sites (`os.setsid`, `pw-dump`, `xdotool`, `setsid`, `systemctl --user`, `/tmp/jarvis-*`, hardcoded `~/.jarvis/...`) with an inline `# windows-footgun: ok` suppression marker for intentional platform-gated uses.

## Why a Phase 1 / Phase 2 split

The voice-agent has three irreducible Linux dependencies today:

1. **PipeWire / WirePlumber** — the L1 echo-cancel module (`module-echo-cancel` with the tuned WebRTC AEC3 args) is a PipeWire module. Windows has no PipeWire. The L2 (in-process WebRTC APM) and L3 (DTLN neural) layers of JARVIS's AEC cascade are cross-platform and would still work, but the agent currently assumes L1 is present.
2. **`sdnotify` + systemd notify-protocol** — `jarvis-voice-agent.service` is `Type=notify` with `WatchdogSec=120s`. The agent imports `sdnotify` unconditionally at startup (`jarvis_agent.py:5358`, `resilience/watchdog.py:19`) and would error before serving its first turn on a host without a systemd notify socket. Per CLAUDE.md, `sdnotify` is in `requirements.txt` and the service is the supported run path.
3. **`xdotool` + X11** — `computer_use` ships click / type / key / screenshot helpers that shell out to `xdotool` + `xdpyinfo`. The Windows equivalent is `pyautogui` plus an `mss` / `pygetwindow` stack, but the call sites haven't been platform-abstracted yet.

Refactoring those three behind cross-platform shims is a Phase 2 project — it's a multi-day code change touching the audio stack, the service-control surface, and the `computer_use` action layer. Trying to land it inside the installer would scope-creep this commit by an order of magnitude.

Phase 1 ships the **cross-platform pieces** that are valuable today:

- The CLI works fully on Windows — it's a Bun/TypeScript program with no native dependencies and no audio path.
- The Tauri Desktop UI builds cleanly on Windows once MSVC tooling is present (Tauri 2 has first-class Windows support via WebView2).
- The Web app (Next.js) is fully cross-platform.
- The voice-agent's *deps* install fine on Windows (livekit-agents is pure Python; the Linux-only call sites just sit dormant until the agent tries to start).

That gives Windows users a working CLI + Desktop today, plus an already-prepared Python venv so Phase 2's flip is a code change, not an installer change.

## 2026-05-24 rewrite — what changed and why

The v1 installer (`install.ps1` at commit `dea50bd0`, 738 lines) was a naive port of `install.sh`. It installed to `~/Documents/Projects/jarvis` (wrong for Windows — Documents is a user-content dir, not an app-data dir), didn't bundle PortableGit (so the bash tool failed without a pre-existing Git for Windows install), missed the `$ProgressPreference` perf fix (so PowerShell 5.1 downloads were 10-100× slower than necessary), missed UTF-8 console encoding (mojibake on npm / git / playwright output), had no `install.cmd` wrapper for CMD users, and used `python -m venv` + pip instead of the much faster `uv`.

This commit replaces it with a battle-tested pattern set learned from production installer work in adjacent projects:

| Change | Why |
|---|---|
| Install root → `%LOCALAPPDATA%\jarvis\jarvis` | Proper Windows app-data dir. Documents is for user content (Word docs, photos) — apps go in LOCALAPPDATA. Matches the platform convention every Microsoft tool, npm global, and Chrome user-data dir uses. |
| Bundle PortableGit if Git absent | The terminal/bash tool needs `bash.exe`. MinGit ships only `git.exe` (no bash). PortableGit ships git + bash + sed + awk + grep + curl + ssh, all in user-scope `%LOCALAPPDATA%\jarvis\git`. No admin needed; no system Git conflict. |
| `$ProgressPreference = "SilentlyContinue"` | PowerShell 5.1's Invoke-WebRequest progress UI repaints synchronously on every received byte, pegging a CPU core. A 57 MB download takes 5 min with progress on vs. 20 s with it off. Same network. Set at script entry. |
| `[Console]::OutputEncoding = UTF8` | Without it, native commands' non-ASCII output (npm check marks, git bullets, playwright box-drawing) renders as IBM437/cp1252 mojibake. Display-only fix; underlying bytes are correct. |
| `uv` (Astral) for Python + venv + deps | 10-100× faster than `pip install`. Installs the right Python itself (no admin, no Microsoft Store stub, no winget MSI dance). Mirrored in `install.sh` so both platforms run the same package manager. |
| Portable Node.js zip > winget MSI | Winget's MSI install triggers UAC that often appears MINIMIZED in the taskbar — looks like a hang to users. Portable zip drops node.exe + npm into `%LOCALAPPDATA%\jarvis\node`, user-scoped, no UAC. |
| `install.cmd` wrapper | The PowerShell `iex (irm ...)` one-liner doesn't work from CMD (no iex/irm cmdlets). The cmd wrapper bootstraps via `powershell -ExecutionPolicy ByPass -Command "..."`. Forwards flags through. |
| `-Branch` / `-Tag` / `-Commit` pinning | Reproducible installs for the future Desktop wizard (pin to a release tag), CI bundles, and recovery flows. Precedence: Commit > Tag > Branch. |
| Stage protocol (`-Manifest` / `-Stage` / `-Json`) | Programmatic drivers (future Tauri onboarding wizard) can drive the install one step at a time and parse JSON progress frames. CLI users on the canonical iex/irm one-liner never touch these flags. |
| Curl-pipe vs local-checkout detection | When invoked via iex/irm, `$PSCommandPath` is empty; treat the configured `$InstallDir` as the destination. When run locally, check for sibling `CLAUDE.md` mentioning `^# JARVIS` and use that dir. |
| Footgun checker | New `scripts/check-windows-footguns.py` is a grep-based CI guard for Linux-only call sites with an inline `# windows-footgun: ok` suppression marker. 21 rules; 28 existing flags on the codebase today (= Phase-2 work). |

## Reference-installer pattern adoption — what we borrowed, what we chose differently

The rewrite is informed by an external reference PowerShell installer (a battle-tested ~2,370-line production installer for a sibling Python agent project, kept untouched in a sibling checkout solely as study material — none of its identifiers, paths, branding, or text content survived the port). We learned from its patterns, then translated them to JARVIS-native shape; the reference checkout itself is excluded from every JARVIS scan, build, lint, and packaging step.

**Patterns adopted directly:**

1. **`$env:LOCALAPPDATA\<app>` install root** with separate dirs for code, portable Git, portable Node, and data — proper Windows app-data layout. We use `%LOCALAPPDATA%\jarvis`.
2. **PortableGit bundling** via the pinned `git-for-windows` v2.54.0 release. JARVIS needs `bash.exe` for the terminal/bash tool — MinGit ships only `git.exe`, so PortableGit (full distribution, no installer UI) is the right choice.
3. **`$ProgressPreference = "SilentlyContinue"`** at script entry — the PowerShell 5.1 progress-bar perf bug.
4. **UTF-8 console encoding** via `[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()` with a try/catch for constrained hosts.
5. **`uv` (Astral) for Python provisioning** — separately tracking `$script:UvCmd` and re-discovering it across stage boundaries via `Resolve-UvCmd` so cross-process stage drivers work. Worked into both `install.ps1` AND `install.sh` so both platforms match.
6. **Portable Node.js zip** over winget MSI to avoid the UAC-minimised-in-taskbar trap.
7. **EAP=Continue-around-2>&1** workaround for native commands that write progress to stderr (uv, npm, playwright). Without it, `$ErrorActionPreference="Stop"` causes 2>&1 captures to wrap stderr lines as ErrorRecord objects and throw on the first one even when the command exits 0.
8. **Stage protocol** (manifest + `-Stage <name>` + JSON exit codes) for programmatic drivers. Same JSON shape (`{stage, ok, skipped, reason, duration_ms}`), same exit codes (0/1/2), same stage-driver-aware error frame deduplication in the outer try/catch.
9. **CMD wrapper** (`install.cmd`) — invoke `powershell -ExecutionPolicy ByPass -NoProfile -Command "iex (irm ...)"`.
10. **Footgun checker** (`scripts/check-windows-footguns.py`) — same dataclass shape, same `# windows-footgun: ok` inline suppression marker, same triple-quoted-string state machine for skipping docstring matches.

**Patterns deliberately changed:**

1. **No tiered `pyproject.toml [all]` extras cascade** — JARVIS uses a flat `requirements.txt`, not `pyproject.toml`. We do a single `uv pip install -r requirements.txt` and let it fail loudly. The reference installer's tiered cascade (extras-all → extras-all-minus-broken → bare `.`) is for projects where a transitive on PyPI breaks; JARVIS hasn't hit that yet and adding the complexity preemptively wouldn't pay back.
2. **No `Install-PlatformSdks` post-pass** — JARVIS doesn't have a `[messaging]` extras keyed off `TELEGRAM_BOT_TOKEN` / `DISCORD_BOT_TOKEN` / etc. that need conditional re-install. Skipped.
3. **JARVIS-native naming throughout** — every identifier, env var, env file, comment, URL uses JARVIS terms. `JARVIS_HOME` env var, `JARVIS_GIT_BASH_PATH`, `.jarvis/` config dir. Verified by a grep gate against the reference-project token.
4. **No `-Ensure` / `-PostInstall` modes** — those drove an alternate browser-automation toolchain. JARVIS uses Playwright Chromium for the browser CDP fallback (gated under `-SkipCdp`), so the surface is simpler.
5. **No SOUL.md seeding from the installer** — JARVIS's persona lives in `src/voice-agent/prompts/soul.md` (committed) with an optional `~/.jarvis/SOUL.md` runtime override. The installer doesn't create the override file; the user opts in by writing it themselves.
6. **No skills-sync from the installer** — JARVIS's skills layer is being rebuilt (CLAUDE.md: capability rebuild after the 2026-05-20 teardown); seeding nothing keeps the rebuild simple.
7. **No setup wizard call at end of install** — JARVIS configures via direct `.env` editing today. A wizard would be a Phase 2 add.
8. **No messaging-gateway autostart prompt** — JARVIS has no Telegram/Discord/Slack gateway today. Skipped.

## What install.ps1 does — stage-by-stage mapping

The installer is organised into 21 stages. Each is a thin wrapper around a worker function so users can `. install.ps1; Install-Repository` for one-off recovery.

| Stage | Worker | Status | Notes |
|---|---|---|---|
| `uv` | `Install-Uv` | New | Bootstrap `uv` via `astral.sh/uv/install.ps1`. |
| `python` | `Test-Python` | New | `uv python find` / `uv python install 3.13`. Fallback to 3.12 / 3.11, then system. |
| `git` | `Install-Git` | New | Detect; if missing, download PortableGit v2.54.0.windows.1 (64-bit, ARM64, or 32-bit MinGit fallback) to `%LOCALAPPDATA%\jarvis\git`. Persist `JARVIS_GIT_BASH_PATH`. |
| `node` | `Test-Node` | Ported + extended | Detect; portable zip preferred over winget MSI to avoid UAC. |
| `bun` | `Test-Bun` | New | Detect; install via `irm bun.sh/install.ps1 \| iex` if missing. |
| `cargo` | `Test-Cargo` | Ported | Detect; with `-AutoInstall`, winget install `Rustlang.Rustup`. |
| `msvc-check` | `Test-MsvcBuildTools` | Ported | Heuristic Test-Path on the Visual Studio install root; hint only. |
| `system-packages` | `Install-SystemPackages` | New | winget / choco / scoop fallback chain for ripgrep + ffmpeg. Optional. |
| `data-dirs` | `Set-DataDirectories` | New | Pre-create `%LOCALAPPDATA%\jarvis\{bin,data,data\logs}` + `~/.jarvis`. Persist `JARVIS_HOME` env var. |
| `repository` | `Install-Repository` | Replaced | Detect valid existing repo via `git rev-parse + status`; update in place. Else: SSH clone → HTTPS clone → ZIP download fallback chain. Honours `-Branch` / `-Tag` / `-Commit` with Commit > Tag > Branch precedence. |
| `voice-agent` | `Install-VoiceAgent` | Replaced | `uv venv .venv --python 3.13` + `uv pip install -r requirements.txt`. Playwright Chromium gated under `-SkipCdp`. Service registration deferred (`Install-WindowsVoiceServices` is a no-op with a clear log). |
| `cli` | `Install-Cli` | Ported | `bun install` in src/cli. Write `.cmd` shims for `jarvis` + `jarvis-desktop` to `%LOCALAPPDATA%\jarvis\bin`, add to user PATH. |
| `web` | `Install-Web` | Ported | `bun install` in src/web. |
| `desktop` | `Install-Desktop` | Ported | `npm install` + `npm run build` + `cargo build --release` in src/voice-agent/desktop-tauri. Start Menu shortcut via WScript.Shell COM. |
| `bash-sandbox` | `Install-BashSandbox` | Deferred | Linux user-namespace sandbox (bwrap) has no Windows analogue today. Phase 2 will design + ship an AppContainer / Job Object equivalent. |
| `bridge-token` | `New-BridgeToken` | Ported | Crypto-random 43 url-safe chars to `~/.jarvis/local-api-token.env` with user-only NTFS ACL. Plumb into `src/web/.env.local` when it exists. |
| `livekit-keys` | `Set-LiveKitKeys` | Ported (partial) | Reads `LIVEKIT_API_KEY/SECRET` from `voice-agent/.env`; writes the YAML. Cannot generate fresh keys (bundled `livekit-server.bin` is a Linux ELF) — falls through to instructions. |
| `computer-use` | `Test-ComputerUseDeps` | Ported (Windows-style) | Probes `mss` + `pyautogui`; notes that xdotool / xdpyinfo / python3-pyatspi are X11-only. |
| `audio-profile` | `Install-AudioProfile` | Deferred / N/A | PipeWire / WirePlumber config has no Windows analogue. WASAPI handles mic/speaker coexistence. |
| `echo-cancel` | `Install-EchoCancel` | Deferred / N/A | L1 PipeWire WebRTC AEC3 is module-based. L2 + L3 of the cascade are cross-platform and activate automatically once Phase 2 wires the voice-agent. |
| `env-template` | `Set-EnvTemplate` | Ported | Same template, written UTF-8 no-BOM via `.NET UTF8Encoding($false)`. User-only NTFS ACL. |

## User-facing contract

After running the curl-pipe one-liner, the user should end up with:

1. The repo cloned to `%LOCALAPPDATA%\jarvis\jarvis` (override with `-InstallDir <path>`).
2. PortableGit at `%LOCALAPPDATA%\jarvis\git` if no system Git was present, with `JARVIS_GIT_BASH_PATH` env var set so the terminal/bash tool finds `bash.exe`.
3. A `.env` template at the repo root, with user-only ACL, ready for API-key fill-in.
4. A `jarvis.cmd` launcher in `%LOCALAPPDATA%\jarvis\bin\`, on the user PATH after a shell restart.
5. A `JARVIS.lnk` Start Menu shortcut launching the Tauri desktop binary.
6. A bridge auth token at `%USERPROFILE%\.jarvis\local-api-token.env` plus an `.env.local` plumb-through for the web app.
7. Python venv at `src\voice-agent\.venv\` with all deps installed via `uv`.
8. `JARVIS_HOME` env var pointing at `%USERPROFILE%\.jarvis` so `tools.runtime.get_jarvis_home()` resolves correctly.

Things the user CANNOT do until Phase 2:

- Start the voice-agent as a service. (No Task Scheduler entry created. Manual `python jarvis_agent.py` will fail at import on `sdnotify` / PipeWire / xdotool call sites.)
- Use voice barge-in, AEC, computer_use's mouse/keyboard helpers, or the `terminal` tool's xdotool path.

The summary block at end-of-install spells this out so users don't expect voice to "just work."

## Footgun checker — what it catches

`scripts/check-windows-footguns.py` is a grep-based regression catcher. Run before PRs; runs in CI to keep the codebase Windows-clean as Phase 2 lands.

Rules (21 total):

**Stdlib AttributeError-at-import patterns (8):**
- `os.setsid` / `os.killpg` / `os.fork` / `os.getuid` / `os.geteuid` / `os.getgid`
- `os.kill(pid, 0)` (NOT a no-op on Windows — hard-kills the target's process group)
- `signal.SIGKILL` / `signal.SIGHUP` / `SIGUSR1` / `SIGUSR2` / `SIGALRM` / `SIGCHLD` / `SIGPIPE` / `SIGQUIT`
- `loop.add_signal_handler(...)` without try/except (raises NotImplementedError on Windows)

**Text I/O (1):**
- `open(path)` text mode without `encoding=` (UTF-8 on POSIX, cp1252 on Windows — round-trip mojibake)

**Subprocess (2):**
- `subprocess.run(['./script.sh'])` (shebang lines aren't honoured by CreateProcessW)
- `wmic` invocation without `shutil.which('wmic')` guard (removed in Win10 21H1+)

**JARVIS-specific Linux-only call sites (10):**
- `xdotool` subprocess invocations (X11-only; needs pyautogui on Windows)
- `pw-dump` (PipeWire-only)
- `wpctl` (WirePlumber-only)
- `pactl` (PulseAudio-only; JARVIS already prefers pw-dump)
- `setsid` invocation (Linux util-linux; use `CREATE_NEW_PROCESS_GROUP` on Windows)
- `systemctl --user` (Linux systemd; use Task Scheduler on Windows)
- Hardcoded `/tmp/jarvis-*` paths (Windows has no `/tmp`)
- Hardcoded `~/.jarvis/...` literals (bypasses `tools.runtime.get_jarvis_home()`)
- Hardcoded `~/.local/share/jarvis/...` literals (Linux XDG_DATA_HOME default)
- Hardcoded `~/Desktop` (OneDrive trap)

Suppression: `# windows-footgun: ok` on the same line silences detection for intentional platform-gated uses.

Same-line guards that auto-suppress: `hasattr(os, 'X')`, `getattr(signal, 'X', ...)`, `shutil.which('X')`, `if platform.system() != 'Windows':`, `if sys.platform == 'win32':`, `IS_WINDOWS` / `IS_LINUX` sentinels.

Initial run against the JARVIS tree flags 28 existing call sites — that's the Phase-2 backlog. None are regressions introduced by this commit (the checker excludes itself + the test file + this spec).

## Phase 2 roadmap (separate project)

The Phase 1 installer is the easy half. Phase 2 will:

1. **Audio platform-abstraction** — wrap the L1 PipeWire module load in a Linux-only branch, surface the L2 + L3 layers as the default on Windows.
2. **Service-control shim** — replace direct `sdnotify` imports with a platform-aware "notify" helper (no-op on Windows, sdnotify on Linux). Register a Task Scheduler entry on Windows for the voice-agent.
3. **Input automation shim** — abstract `xdotool` calls behind a `desktop_input` module that resolves to `pyautogui` on Windows, `xdotool` on Linux X11, `ydotool` / `wlrctl` on Wayland.
4. **Screen capture** — `mss` is already cross-platform; the `computer_use` Linux-only branches just need detection.
5. **bash tool sandbox** — design + ship an AppContainer / Job Object equivalent for the bash tool on Windows (or document the unsandboxed fallback loudly and gate the dangerous-command surface accordingly).
6. **LiveKit server** — bundle `livekit-server.exe` alongside the Linux binary or document the Windows download path.
7. **Path-helper sweep** — fix all 28 footgun-checker hits via `tools.runtime.get_jarvis_home()` + a new `get_jarvis_data_home()` helper.

Each of these is independent of the others and the installer. The installer just needs to flip `Install-WindowsVoiceServices` from "deferred" to a real Task Scheduler register call once Phase 2 ships.

## Open questions for review

Five things flagged during the port that warrant a second look before Phase 2:

1. **Bridge token timing** — the Linux installer plumbs the token into `src/web/.env.local` only if that file already exists. On a fresh Windows install (no Web dev session has happened yet), the file may not exist when `New-BridgeToken` runs. The token gets written to `~/.jarvis/local-api-token.env` regardless, and the Web app will read from there on first boot — but worth a smoke test on a fresh VM to confirm there's no auth-skew on the user's first `bun dev`.
2. **Tauri MSI signing** — `cargo build --release` produces an unsigned `jarvis-desktop.exe`. Tauri can produce a signed `.msi` via `cargo tauri build` if `bundle.active: true` and `windows.signCommand` are set in `tauri.conf.json`. Phase 1 ships the unsigned `.exe` to match the Linux installer's behaviour (which also doesn't sign). Windows SmartScreen will warn on first launch. Decision needed: do we add a signing pipeline for Windows now, or defer until we have a code-signing cert?
3. **MSVC vs GNU toolchain** — Tauri 2 on Windows is MSVC-only. The installer asserts MSVC Build Tools via a `Test-Path 'C:\Program Files (x86)\Microsoft Visual Studio'` check, but that's heuristic. A clean fail-fast would probe `link.exe` or `cl.exe` on PATH (but those are only in the Developer Command Prompt). Worth tightening if we hit "cargo build succeeds with stub rustc-link errors" support load.
4. **PortableGit pin (v2.54.0.windows.1)** — current stable Git for Windows release as of 2026-05. Bump deliberately when Git for Windows ships a security fix; static github.com archive URLs are not rate-limited (unlike `api.github.com/.../releases/latest` which caps at 60 req/hr/IP and breaks behind CGNAT).
5. **`-AutoInstall` semantics** — currently it only kicks in for `cargo` (winget install Rustlang.Rustup). uv / Python / Git / Node / Bun auto-install unconditionally. Worth aligning either way: maybe make those uv/python/git/node/bun installs respect `-AutoInstall=false` as a "diagnose only" mode? Today the user has no way to say "tell me what's missing without installing anything" except `-DryRun`, which is destructive-prereq-aware-only.

## Verification

- `pwsh -NoProfile -Command [Parser]::ParseFile('install.ps1', [ref]$null, [ref]$null)` → parses cleanly (8080 tokens).
- `install.ps1 -ProtocolVersion` → emits `1`.
- `install.ps1 -Manifest` → emits 21-stage JSON manifest.
- `install.ps1 -Stage nonexistent` → exit 2 with structured JSON error frame.
- `bash -n install.sh` → parses cleanly after the uv edit.
- `python3 scripts/check-windows-footguns.py --help` → prints usage.
- `python3 scripts/check-windows-footguns.py --list` → prints 21 rules.
- `pytest src/voice-agent/tests/test_windows_footguns_checker.py` → 21 passed.
- Reference-project token grep across all shipping files → 0 hits.
- README install section updated with PowerShell + CMD + bash one-liners side-by-side and a Phase-1 status note.
- No edits to `src/cli/**`, `src/voice-agent/**` (except the new test file), `src/voice-agent/desktop-tauri/**`, `src/web/**`, or the reference checkout.

## Out of scope (deliberately)

- Refactoring the voice-agent's Linux-only imports — Phase 2.
- A Windows-native LiveKit server binary — bundle later or document.
- An MSI / Squirrel installer for the Tauri desktop — separate decision.
- Auto-installing MSVC Build Tools (heavy, license-gated, +5 GB) — print install command, let the user opt in.
- WSL2 detection + redirect — keeping install.ps1 strictly native; users who want WSL2 install via WSL2 + install.sh, which is unchanged.
