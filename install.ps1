# JARVIS one-shot installer — Windows / PowerShell.
#
# Usage (curl-pipe):
#   iwr -useb https://raw.githubusercontent.com/ulrichando/jarvis/master/install.ps1 | iex
#
# Usage (after cloning):
#   .\install.ps1 [-InstallDir <path>] [-DryRun] [-SkipCli] [-SkipVoice] [-SkipDesktop] [-SkipWeb] [-AutoInstall]
#
# Idempotent: re-running skips channels that are already installed.
#
# Phase 1 status (2026-05-23): CLI + Desktop UI fully supported on Windows.
# The voice-agent's Python deps install cleanly, but the agent itself is
# Linux-only today (PipeWire / systemd / xdotool / X11). Phase 2 of the
# Windows port will refactor those behind platform-abstraction layers so
# the voice agent can launch as a Windows user-scoped scheduled task or
# Service. Until then, the voice-agent section here installs deps + writes
# config, then DEFERS service registration with a clear log line.

#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:USERPROFILE 'Documents\Projects\jarvis'),
    [switch]$DryRun,
    [switch]$SkipCli,
    [switch]$SkipVoice,
    [switch]$SkipDesktop,
    [switch]$SkipWeb,
    [switch]$SkipCdp,
    [switch]$AutoInstall
)

$ErrorActionPreference = 'Stop'

# ── Constants ────────────────────────────────────────────────────────────
$script:RepoUrl       = 'https://github.com/ulrichando/jarvis.git'
$script:LocalBin      = Join-Path $env:LOCALAPPDATA 'jarvis\bin'
$script:ConfigDir     = Join-Path $env:USERPROFILE  '.jarvis'
$script:DataDir       = Join-Path $env:LOCALAPPDATA 'jarvis'
$script:StartMenuDir  = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'

# ── Output helpers (mirror install.sh's c_red / c_green / section / etc) ──
function Write-Section { param([string]$Msg) Write-Host ''; Write-Host "=== $Msg ===" -ForegroundColor Cyan }
function Write-Sub     { param([string]$Msg) Write-Host "  $Msg" }
function Write-Ok      { param([string]$Msg) Write-Host "  [ok] $Msg" -ForegroundColor Green }
function Write-Warn2   { param([string]$Msg) Write-Host "  [warn] $Msg" -ForegroundColor Yellow }
function Write-Err     { param([string]$Msg) Write-Host "  [err] $Msg" -ForegroundColor Red }
function Stop-WithError { param([string]$Msg) Write-Err $Msg; exit 1 }

# ── Tool presence (mirror `have`) ────────────────────────────────────────
function Test-Command {
    param([Parameter(Mandatory)][string]$Name)
    return [bool](Get-Command -Name $Name -ErrorAction SilentlyContinue)
}

# ── Dry-run wrapper ───────────────────────────────────────────────────────
function Invoke-Step {
    # Runs a scriptblock unless -DryRun is set, in which case it logs
    # what would have run and returns. Mirrors the install.sh checkpoints
    # that bail when JARVIS_DRY_RUN=1.
    param(
        [Parameter(Mandatory)][string]$Description,
        [Parameter(Mandatory)][scriptblock]$Action
    )
    if ($DryRun) {
        Write-Warn2 "DRY RUN: would have $Description"
        return
    }
    & $Action
}

# ── Detect: curl-pipe vs local checkout ───────────────────────────────────
function Get-Invocation {
    # When invoked via `iwr ... | iex`, $PSCommandPath / $MyInvocation.MyCommand.Path
    # are empty. In that case treat $script:InstallDir as the destination.
    # When run locally (`.\install.ps1`), the script lives somewhere — if
    # its sibling CLAUDE.md mentions JARVIS, take that dir as the checkout.
    $localPath = $PSCommandPath
    if (-not $localPath) { $localPath = $MyInvocation.MyCommand.Path }

    if ($localPath -and (Test-Path $localPath)) {
        $scriptDir = Split-Path -Parent $localPath
        $claudeMd = Join-Path $scriptDir 'CLAUDE.md'
        if ((Test-Path $claudeMd) -and (Select-String -Path $claudeMd -Pattern '^# JARVIS' -Quiet)) {
            $script:InstallDir = $scriptDir
            Write-Host "Detected existing checkout at: $InstallDir" -ForegroundColor Cyan
            return
        }
    }
    Write-Host "Will install JARVIS to: $InstallDir" -ForegroundColor Cyan
}

# ── Winget helper (best-effort auto-install of prereqs) ───────────────────
function Install-ViaWinget {
    param(
        [Parameter(Mandatory)][string]$Id,
        [Parameter(Mandatory)][string]$DisplayName
    )
    if (-not (Test-Command 'winget')) {
        Write-Warn2 "winget not available — install $DisplayName manually."
        return $false
    }
    if (-not $AutoInstall) {
        Write-Warn2 "$DisplayName missing. Re-run with -AutoInstall to install via: winget install --id $Id --silent"
        return $false
    }
    Write-Sub "Installing $DisplayName via winget (id=$Id) ..."
    try {
        & winget install --id $Id --silent --accept-source-agreements --accept-package-agreements | Out-Host
        Write-Ok "$DisplayName installed via winget"
        return $true
    } catch {
        Write-Warn2 "winget install $Id failed: $($_.Exception.Message)"
        return $false
    }
}

# ── Prerequisites ────────────────────────────────────────────────────────
function Test-Prereqs {
    Write-Section 'Checking prerequisites'

    $missing = New-Object System.Collections.Generic.List[string]

    # git
    if (Test-Command 'git') {
        Write-Ok ("git ({0})" -f (git --version))
    } else {
        Write-Err 'git not found'
        if (-not (Install-ViaWinget -Id 'Git.Git' -DisplayName 'Git')) { $missing.Add('git') }
    }

    # Python 3.11+ — Windows ships `python` and the `py` launcher
    $pythonOk = $false
    foreach ($cmd in @('python', 'python3', 'py')) {
        if (Test-Command $cmd) {
            $verRaw = & $cmd --version 2>&1
            if ($verRaw -match 'Python\s+(\d+)\.(\d+)') {
                $major = [int]$Matches[1]; $minor = [int]$Matches[2]
                if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
                    Write-Ok "$cmd (${major}.${minor})"
                    $script:PythonExe = $cmd
                    $pythonOk = $true
                    break
                }
            }
        }
    }
    if (-not $pythonOk) {
        Write-Err 'python (>=3.11) not found'
        # 3.13 matches the voice-agent CLAUDE.md baseline.
        if (-not (Install-ViaWinget -Id 'Python.Python.3.13' -DisplayName 'Python 3.13')) {
            $missing.Add('python>=3.11')
        } else {
            $script:PythonExe = 'python'
        }
    }

    # Bun — CLI + Web installer
    if (-not $SkipCli -or -not $SkipWeb) {
        if (Test-Command 'bun') {
            Write-Ok ("bun ({0})" -f (bun --version))
        } else {
            Write-Warn2 'bun not found'
            $bunInstalled = Install-ViaWinget -Id 'Oven-sh.Bun' -DisplayName 'Bun'
            if (-not $bunInstalled) {
                Write-Sub "Or install Bun manually: irm bun.sh/install.ps1 | iex"
                $missing.Add('bun')
            }
        }
    }

    # Node + npm — Tauri frontend toolchain
    if (-not $SkipDesktop -or -not $SkipWeb) {
        if (Test-Command 'node') {
            Write-Ok ("node ({0})" -f (node --version))
        } else {
            Write-Err 'node not found'
            if (-not (Install-ViaWinget -Id 'OpenJS.NodeJS.LTS' -DisplayName 'Node.js LTS')) { $missing.Add('node') }
        }
        if (Test-Command 'npm') {
            Write-Ok ("npm ({0})" -f (npm --version))
        } else {
            Write-Err 'npm not found (usually shipped with node)'
            $missing.Add('npm')
        }
    }

    # Rust / cargo — only required for desktop channel
    if (-not $SkipDesktop) {
        if (Test-Command 'cargo') {
            $cargoVer = (cargo --version) -replace '^cargo\s+(\S+).*', '$1'
            Write-Ok "cargo ($cargoVer)"
        } else {
            Write-Warn2 'cargo not found — install rustup (then run `rustup default stable`)'
            $rustInstalled = Install-ViaWinget -Id 'Rustlang.Rustup' -DisplayName 'Rustup'
            if (-not $rustInstalled) {
                Write-Sub 'Or: download https://win.rustup.rs and run rustup-init.exe'
                Write-Sub '(re-run with -SkipDesktop to skip the desktop build)'
                $missing.Add('cargo')
            } else {
                Write-Sub 'Note: a NEW shell is required for cargo to be on PATH after rustup install.'
            }
        }

        # Tauri 2 on Windows additionally needs the MSVC build tools + WebView2.
        # WebView2 is preinstalled on Win11 and recent Win10 builds via Edge;
        # MSVC is the user's responsibility. Just hint, don't fail.
        if (-not (Test-Path 'C:\Program Files (x86)\Microsoft Visual Studio')) {
            Write-Warn2 'MSVC Build Tools may be missing — Tauri''s cargo build needs the MSVC toolchain.'
            Write-Sub  'Install: winget install --id Microsoft.VisualStudio.2022.BuildTools --silent --override "--quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"'
            Write-Sub  '(Or run with -SkipDesktop to skip the desktop build.)'
        }
    }

    if ($missing.Count -gt 0) {
        Write-Err ("Missing: {0} — install them and rerun this script." -f ($missing -join ', '))
        if (-not $AutoInstall) {
            Write-Sub 'Tip: re-run with -AutoInstall to let the script `winget install` the missing prereqs.'
        }
        exit 1
    }
}

# ── Clone (or update) ────────────────────────────────────────────────────
function Sync-Repo {
    $gitDir = Join-Path $InstallDir '.git'
    if (Test-Path $gitDir) {
        Write-Section 'Updating existing checkout'
        Invoke-Step -Description "git fetch + pull in $InstallDir" -Action {
            & git -C $InstallDir fetch --quiet origin master
            try {
                & git -C $InstallDir pull --ff-only origin master | Out-Null
            } catch {
                Write-Warn2 'pull --ff-only failed (local changes?); leaving checkout as-is'
            }
            $sha = (& git -C $InstallDir rev-parse --short HEAD).Trim()
            Write-Ok "checkout at $sha"
        }
    } else {
        Write-Section 'Cloning JARVIS'
        Invoke-Step -Description "git clone $RepoUrl into $InstallDir" -Action {
            $parent = Split-Path -Parent $InstallDir
            if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
            & git clone --quiet $RepoUrl $InstallDir
            Write-Ok "cloned to $InstallDir"
        }
    }
}

# ── Channel: CLI ─────────────────────────────────────────────────────────
function Install-Cli {
    if ($SkipCli) { Write-Warn2 'skipping CLI (-SkipCli)'; return }
    Write-Section 'Installing CLI'
    $cliDir = Join-Path $InstallDir 'src\cli'
    Invoke-Step -Description "bun install in $cliDir" -Action {
        Push-Location $cliDir
        try { & bun install --silent } finally { Pop-Location }
        Write-Ok 'deps installed'
    }

    # Wire a Windows-friendly launcher. The repo's bin/jarvis is a bash
    # script; instead of symlinking (requires Developer Mode or admin),
    # write a small .cmd shim that delegates to `bun run` inside src/cli.
    Invoke-Step -Description "writing CLI launcher shims to $LocalBin" -Action {
        if (-not (Test-Path $LocalBin)) { New-Item -ItemType Directory -Force -Path $LocalBin | Out-Null }
        $jarvisCmd = Join-Path $LocalBin 'jarvis.cmd'
        $jarvisShim = @"
@echo off
REM JARVIS CLI launcher (auto-generated by install.ps1).
REM Delegates to the Bun-driven CLI in the cloned repo.
pushd "$cliDir"
bun run start %*
set EC=%ERRORLEVEL%
popd
exit /b %EC%
"@
        Set-Content -Path $jarvisCmd -Value $jarvisShim -Encoding ASCII
        Write-Ok "wrote $jarvisCmd"

        $desktopCmd = Join-Path $LocalBin 'jarvis-desktop.cmd'
        $desktopBin = Join-Path $InstallDir 'src\desktop-tauri\src-tauri\target\release\jarvis-desktop.exe'
        $desktopShim = @"
@echo off
REM JARVIS Desktop launcher (auto-generated by install.ps1).
if not exist "$desktopBin" (
  echo Tauri binary not built yet. Build with: cd "$InstallDir\src\desktop-tauri" ^&^& cargo build --release
  exit /b 1
)
start "" "$desktopBin" %*
"@
        Set-Content -Path $desktopCmd -Value $desktopShim -Encoding ASCII
        Write-Ok "wrote $desktopCmd"
    }

    # PATH plumbing — user-scope only (no admin needed).
    Invoke-Step -Description "adding $LocalBin to the user PATH if missing" -Action {
        $userPath = [Environment]::GetEnvironmentVariable('PATH', 'User')
        if (-not $userPath) { $userPath = '' }
        $segments = $userPath -split ';' | Where-Object { $_ -ne '' }
        if ($segments -notcontains $LocalBin) {
            $newPath = if ($userPath) { "$userPath;$LocalBin" } else { $LocalBin }
            [Environment]::SetEnvironmentVariable('PATH', $newPath, 'User')
            Write-Ok "added $LocalBin to user PATH (open a NEW shell to pick it up)"
        } else {
            Write-Ok "$LocalBin already on user PATH"
        }
    }
}

# ── Channel: Web (Next.js) ───────────────────────────────────────────────
function Install-Web {
    if ($SkipWeb) { Write-Warn2 'skipping Web (-SkipWeb)'; return }
    Write-Section 'Installing Web (Next.js)'
    $webDir = Join-Path $InstallDir 'src\web'
    Invoke-Step -Description "bun install in $webDir" -Action {
        Push-Location $webDir
        try { & bun install --silent } finally { Pop-Location }
        Write-Ok "deps installed - run 'cd $webDir; bun dev' to start dev server"
    }
}

# ── Channel: Voice agent ─────────────────────────────────────────────────
function Install-VoiceAgent {
    if ($SkipVoice) { Write-Warn2 'skipping Voice Agent (-SkipVoice)'; return }
    Write-Section 'Installing Voice Agent (~2-3 min; livekit-agents is heavy)'

    $va     = Join-Path $InstallDir 'src\voice-agent'
    $venv   = Join-Path $va '.venv'
    $vPy    = Join-Path $venv 'Scripts\python.exe'
    $vPip   = Join-Path $venv 'Scripts\pip.exe'
    $reqTxt = Join-Path $va 'requirements.txt'

    Invoke-Step -Description "create venv at $venv (if absent)" -Action {
        if (-not (Test-Path $venv)) {
            & $script:PythonExe -m venv $venv
            Write-Ok "created venv at $venv"
        } else {
            Write-Ok 'venv exists; reusing'
        }
    }

    if (-not $DryRun) {
        & $vPip install --quiet --upgrade pip
        & $vPip install --quiet -r $reqTxt
        Write-Ok 'deps installed'
    }

    Install-PlaywrightChromium -VenvPython $vPy
    Install-WindowsVoiceServices
}

# ── Playwright Chromium (gated; ~200MB) ──────────────────────────────────
function Install-PlaywrightChromium {
    param([Parameter(Mandatory)][string]$VenvPython)
    if ($SkipCdp) {
        Write-Warn2 'skipping Playwright Chromium (-SkipCdp) - CDP fallback wont work'
        return
    }
    # Playwright's Windows cache lives under %USERPROFILE%\AppData\Local\ms-playwright.
    $cacheDir = Join-Path $env:LOCALAPPDATA 'ms-playwright'
    if ((Test-Path $cacheDir) -and ((Get-ChildItem -Path $cacheDir -Filter 'chromium-*' -ErrorAction SilentlyContinue).Count -gt 0)) {
        Write-Ok 'Playwright Chromium already cached'
        return
    }

    Write-Sub 'About to download ~200MB of Chromium for browser CDP fallback'
    Write-Sub '(re-run with -SkipCdp to skip the download)'

    # Curl-pipe install (no TTY) auto-yes; interactive install asks.
    $interactive = [Environment]::UserInteractive
    if ($interactive) {
        $reply = Read-Host '  Download Playwright Chromium now? [Y/n]'
        if ($reply -and $reply -notmatch '^[Yy]') {
            Write-Warn2 "skipped - run '$VenvPython -m playwright install chromium' later"
            return
        }
    } else {
        Write-Sub 'non-interactive shell - proceeding with download'
    }

    Invoke-Step -Description "$VenvPython -m playwright install chromium" -Action {
        & $VenvPython -m playwright install chromium
        Write-Ok 'Playwright Chromium installed'
    }
}

# ── Windows voice-service install — DEFERRED to Phase 2 ──────────────────
function Install-WindowsVoiceServices {
    # The Linux installer registers user systemd units here (voice-agent,
    # voice-client, livekit-server, plus three maintenance timers). On
    # Windows the equivalents would be Task Scheduler entries or a user-
    # scoped service — but the voice-agent itself imports Linux-only deps
    # (sdnotify for systemd notify-protocol, plus AEC paths that hit
    # PipeWire / xdotool / X11). Those imports succeed under WSL2 but not
    # native Windows. Until Phase 2 of the Windows port refactors those
    # behind platform-abstraction layers, registering a Windows service
    # would just produce a service that crash-loops on startup.
    Write-Sub 'Voice-agent service registration: DEFERRED to Phase 2'
    Write-Sub '  Why: the voice-agent currently imports Linux-only modules'
    Write-Sub '  (PipeWire echo-cancel, systemd sdnotify, xdotool / X11).'
    Write-Sub '  Python deps installed; the agent will NOT start on native Windows yet.'
    Write-Sub '  Phase 2 will land platform-abstraction shims for audio + service-control.'
    Write-Sub '  Workaround today: run the voice-agent under WSL2 with the Linux installer.'
}

# ── Bubblewrap — Linux-only sandbox; SKIPPED on Windows ──────────────────
function Install-BashSandbox {
    Write-Sub 'bubblewrap (bash-tool user-namespace sandbox): SKIPPED on Windows'
    Write-Sub '  The bash tool relies on Linux user namespaces. On Windows the equivalent'
    Write-Sub '  story (AppContainer / Sandbox / Win32 Job Objects) is a Phase 2 design item.'
    Write-Sub '  Until then, when the bash tool is invoked from a Windows JARVIS process'
    Write-Sub '  it should fall back to an unsandboxed cmd.exe / pwsh.exe (Phase 2).'
}

# ── Bridge auth token (pre-generated for first-run UX) ───────────────────
function New-BridgeToken {
    $tokenFile = Join-Path $ConfigDir 'local-api-token.env'
    if (Test-Path $tokenFile) {
        Write-Ok "bridge token already exists at $tokenFile"
        return
    }
    Invoke-Step -Description "generate bridge auth token at $tokenFile" -Action {
        if (-not (Test-Path $ConfigDir)) { New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null }

        # 32 bytes of crypto-random → base64 → 43 url-safe chars (no padding).
        # Matches the Linux installer's `head -c 32 /dev/urandom | base64 | tr -d '+/=' | head -c 43`.
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        $bytes = New-Object byte[] 32
        $rng.GetBytes($bytes)
        $rng.Dispose()
        $b64 = [Convert]::ToBase64String($bytes)
        $token = ($b64 -replace '[+/=]', '').Substring(0, 43)

        Set-Content -Path $tokenFile -Value "JARVIS_LOCAL_API_TOKEN=$token" -Encoding ASCII
        # NTFS-equivalent of chmod 600: restrict to current user.
        Set-WindowsAclUserOnly -Path $tokenFile
        Write-Ok "generated bridge auth token at $tokenFile (user-only ACL)"

        # Plumb into src/web/.env.local so the Next.js middleware has it
        # without depending on the desktop launcher having run.
        $webEnv = Join-Path $InstallDir 'src\web\.env.local'
        if (Test-Path $webEnv) {
            $existing = Get-Content $webEnv -ErrorAction SilentlyContinue
            if ($existing -notmatch '^JARVIS_LOCAL_API_TOKEN=') {
                Add-Content -Path $webEnv -Value ''
                Add-Content -Path $webEnv -Value '# Bearer token for /api/* middleware (matches the bridge token).'
                Add-Content -Path $webEnv -Value "JARVIS_LOCAL_API_TOKEN=$token"
                Add-Content -Path $webEnv -Value 'JARVIS_REQUIRE_LOCAL_AUTH=1'
                Set-WindowsAclUserOnly -Path $webEnv
                Write-Ok "appended JARVIS_LOCAL_API_TOKEN + REQUIRE_LOCAL_AUTH=1 to $webEnv"
            }
        }
    }
}

# ── User-only ACL helper (chmod 600 equivalent for secrets) ──────────────
function Set-WindowsAclUserOnly {
    param([Parameter(Mandatory)][string]$Path)
    try {
        $acl = Get-Acl $Path
        $acl.SetAccessRuleProtection($true, $false)  # disable inheritance, drop inherited entries
        $acl.Access | ForEach-Object { [void]$acl.RemoveAccessRule($_) }
        $user = "$env:USERDOMAIN\$env:USERNAME"
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $user, 'FullControl', 'Allow'
        )
        $acl.AddAccessRule($rule)
        Set-Acl -Path $Path -AclObject $acl
    } catch {
        Write-Warn2 "couldnt tighten ACL on $Path : $($_.Exception.Message)"
    }
}

# ── LiveKit keys ─────────────────────────────────────────────────────────
function Set-LiveKitKeys {
    $keys  = Join-Path $ConfigDir 'livekit-keys.yaml'
    $vaEnv = Join-Path $InstallDir 'src\voice-agent\.env'

    # If keys file exists and looks like valid YAML, leave it alone.
    if ((Test-Path $keys) -and ((Get-Item $keys).Length -gt 0)) {
        $firstReal = Get-Content $keys | Where-Object { $_ -notmatch '^\s*#' -and $_ -match '\S' } | Select-Object -First 1
        if ($firstReal -match '^[A-Za-z0-9]+:\s+') {
            Write-Ok "LiveKit keys already at $keys"
            return
        }
    }
    if (-not (Test-Path $ConfigDir)) { New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null }

    # Prefer the LIVEKIT_API_KEY / LIVEKIT_API_SECRET already in
    # voice-agent/.env (matches the Linux installer's behaviour).
    $lkKey = $null; $lkSecret = $null
    if (Test-Path $vaEnv) {
        $envLines = Get-Content $vaEnv
        $keyLine    = $envLines | Where-Object { $_ -match '^LIVEKIT_API_KEY=' }    | Select-Object -First 1
        $secretLine = $envLines | Where-Object { $_ -match '^LIVEKIT_API_SECRET=' } | Select-Object -First 1
        if ($keyLine)    { $lkKey    = ($keyLine    -replace '^LIVEKIT_API_KEY=', '')    -replace '["'']', '' }
        if ($secretLine) { $lkSecret = ($secretLine -replace '^LIVEKIT_API_SECRET=', '') -replace '["'']', '' }
    }

    if ($lkKey -and $lkSecret) {
        if (Test-Path $keys) {
            $stamp = [int][double]::Parse((Get-Date -UFormat '%s'))
            Move-Item -Path $keys -Destination "$keys.bak-$stamp" -Force
        }
        Set-Content -Path $keys -Value "${lkKey}: ${lkSecret}" -Encoding ASCII
        Set-WindowsAclUserOnly -Path $keys
        Write-Ok "wrote $keys from voice-agent/.env LIVEKIT_API_KEY/SECRET"
        return
    }

    # No keys yet — on Linux the installer would shell out to the bundled
    # livekit-server.bin to generate a fresh pair. On Windows the repo's
    # bundled binary is the Linux ELF, so we can't run it. Emit instructions.
    Write-Warn2 "no LIVEKIT_API_KEY/SECRET found in $vaEnv"
    Write-Sub  'Generate a pair manually and add them to voice-agent/.env, then re-run this step.'
    Write-Sub  '  - Easiest: under WSL2, run "src/voice-agent/livekit-server.bin generate-keys"'
    Write-Sub  '  - Or grab the Windows livekit-server.exe from https://github.com/livekit/livekit/releases'
    Write-Sub  "  - Then write '<key>: <secret>' to $keys (one line) and re-run install.ps1"
}

# ── Audio profile (PipeWire) — Linux-only ────────────────────────────────
function Install-AudioProfile {
    Write-Sub 'PipeWire / WirePlumber auto-profile config: SKIPPED on Windows'
    Write-Sub '  Windows handles mic/speaker coexistence at the OS level (WASAPI shared mode)'
    Write-Sub '  - no userland config needed. If you hit mic-exclusivity issues, check the'
    Write-Sub '  Sound Control Panel > Recording > Properties > Advanced "Exclusive Mode" toggle.'
}

# ── Echo cancel (L1 PipeWire WebRTC AEC3) — Linux-only ───────────────────
function Install-EchoCancel {
    Write-Sub 'L1 PipeWire WebRTC AEC3 echo-cancel: SKIPPED on Windows'
    Write-Sub '  L1 is a PipeWire module. On Windows the AEC story is:'
    Write-Sub '    L2 = WebRTC APM in-process (cross-platform - works today)'
    Write-Sub '    L3 = DTLN neural cancellation (cross-platform - works today)'
    Write-Sub '  Both L2 + L3 will activate automatically once Phase 2 wires the voice-agent'
    Write-Sub '  service. No installer-time action needed.'
}

# ── Computer-use deps probe ──────────────────────────────────────────────
function Test-ComputerUseDeps {
    $vaPy = Join-Path $InstallDir 'src\voice-agent\.venv\Scripts\python.exe'
    if (-not (Test-Path $vaPy)) { return }

    Write-Host ''
    Write-Sub 'Checking computer_use deps (optional, Windows-style) ...'

    # mss is cross-platform; useful for screen capture.
    & $vaPy -c 'import mss' 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 'mss not installed in voice-agent venv. To enable screen capture:'
        Write-Sub  "  $vaPy -m pip install mss"
    }

    # pyautogui is the Windows-friendly stand-in for xdotool.
    & $vaPy -c 'import pyautogui' 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 'pyautogui not installed - Windows equivalent of xdotool for click/type/key.'
        Write-Sub  "  $vaPy -m pip install pyautogui"
        Write-Sub  '  (Note: computer_use itself is Linux-only today; this is a Phase 2 hook.)'
    }

    Write-Sub 'xdotool / xdpyinfo / python3-pyatspi: N/A on Windows (X11-only tools).'
}

# ── Channel: Desktop (Tauri) ─────────────────────────────────────────────
function Install-Desktop {
    if ($SkipDesktop) { Write-Warn2 'skipping Desktop (-SkipDesktop)'; return }
    Write-Section 'Installing Desktop (Tauri) - first build takes 5-10 min'

    $dt = Join-Path $InstallDir 'src\desktop-tauri'
    Invoke-Step -Description "npm install in $dt" -Action {
        Push-Location $dt
        try { & npm install --silent } finally { Pop-Location }
        Write-Ok 'frontend deps installed'
    }

    # CLAUDE.md rule: BOTH `npm run build` and `cargo build --release` are
    # required - npm run build alone doesnt ship JS changes because Tauri
    # embeds dist/ into the Rust binary at compile time.
    Invoke-Step -Description "npm run build in $dt" -Action {
        Push-Location $dt
        try { & npm run build --silent } finally { Pop-Location }
        Write-Ok 'frontend built (dist/)'
    }

    Invoke-Step -Description "cargo build --release in $dt\src-tauri" -Action {
        Push-Location (Join-Path $dt 'src-tauri')
        try { & cargo build --release } finally { Pop-Location }
    }

    # The Cargo package name is jarvis-desktop (Cargo.toml [package] name).
    # On Windows the binary is jarvis-desktop.exe.
    $bin = Join-Path $dt 'src-tauri\target\release\jarvis-desktop.exe'
    if (Test-Path $bin) {
        $sizeMb = [math]::Round((Get-Item $bin).Length / 1MB, 1)
        Write-Ok "desktop binary at $bin (${sizeMb}MB)"
    } else {
        Write-Warn2 "expected $bin not found - check $dt\src-tauri\target\release\ for the binary name"
    }

    Install-StartMenuShortcut
}

# ── Start Menu shortcut (Windows equivalent of .desktop entry) ───────────
function Install-StartMenuShortcut {
    $exec = Join-Path $InstallDir 'src\desktop-tauri\src-tauri\target\release\jarvis-desktop.exe'
    $iconIco = Join-Path $InstallDir 'src\desktop-tauri\src-tauri\icons\icon.ico'
    $iconPng = Join-Path $InstallDir 'src\desktop-tauri\src-tauri\icons\jarvis-rings-128.png'
    $shortcut = Join-Path $StartMenuDir 'JARVIS.lnk'

    Invoke-Step -Description "create Start Menu shortcut at $shortcut" -Action {
        if (-not (Test-Path $StartMenuDir)) { New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null }
        $wsh = New-Object -ComObject WScript.Shell
        $lnk = $wsh.CreateShortcut($shortcut)
        $lnk.TargetPath = $exec
        $lnk.WorkingDirectory = $InstallDir
        # Prefer .ico if Tauri produced one (it usually does for Windows builds);
        # fall back to the rings PNG. Windows Start Menu accepts PNG via .lnk
        # but renders cleaner with .ico.
        if (Test-Path $iconIco) {
            $lnk.IconLocation = $iconIco
        } elseif (Test-Path $iconPng) {
            $lnk.IconLocation = $iconPng
        }
        $lnk.Description = 'Voice-first AI assistant (Tauri desktop UI)'
        $lnk.Save()
        Write-Ok "installed Start Menu shortcut: $shortcut"

        if (-not (Test-Path $exec)) {
            Write-Warn2 'Tauri binary not yet built - launcher will fail until cargo build --release completes'
            Write-Sub  "  Build now: Push-Location '$InstallDir\src\desktop-tauri\src-tauri'; cargo build --release; Pop-Location"
        }
    }
}

# ── .env template ────────────────────────────────────────────────────────
function Set-EnvTemplate {
    Write-Section 'API key template'
    $envFile = Join-Path $InstallDir '.env'
    if (Test-Path $envFile) {
        Write-Ok '.env already exists; not overwriting'
        return
    }
    Invoke-Step -Description "create $envFile" -Action {
        $template = @'
# JARVIS - centralized API keys.
# Each subproject's .env.local (or src/voice-agent/.env, etc.) holds
# subproject-specific vars and overrides these on collision.
# %USERPROFILE%\.jarvis\keys.env overrides everything (Tray UI writes here).

# LLM providers (fill these in with real keys)
GROQ_API_KEY=
DEEPSEEK_API_KEY=
GOOGLE_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
KIMI_API_KEY=

# Optional knobs (uncomment + set if you use these)
# JARVIS_PROVIDER=deepseek
# OLLAMA_HOST=http://127.0.0.1:11434
# LANGCHAIN_TRACING_V2=true
# LANGCHAIN_API_KEY=
# LANGCHAIN_PROJECT=jarvis

# Sandbox / safety knobs (uncomment to override defaults)
# JARVIS_BASH_BWRAP=0           # disable bash-tool sandbox (Linux-only effect)
# JARVIS_REQUIRE_LOCAL_AUTH=1   # require Bearer token on bridge + web /api/*
# JARVIS_DAILY_COST_CEILING_USD=5
'@
        Set-Content -Path $envFile -Value $template -Encoding UTF8
        Set-WindowsAclUserOnly -Path $envFile
        Write-Ok "created $envFile (user-only ACL - fill in your real keys before starting the voice agent)"
    }
}

# ── Final summary ────────────────────────────────────────────────────────
function Write-Summary {
    Write-Section 'Done'
    Write-Host @"
  Install location:  $InstallDir
  CLI launcher:      $LocalBin\jarvis.cmd  (also jarvis-desktop.cmd)
  Config dir:        $ConfigDir
  Data dir:          $DataDir   (Phase 2 will wire logs / telemetry here)
  Start Menu:        $StartMenuDir\JARVIS.lnk

  Phase 1 status (Windows port):
    - CLI:      INSTALLED and runnable today
    - Web:      INSTALLED ('cd $InstallDir\src\web; bun dev')
    - Desktop:  BUILT - launch from Start Menu, or run jarvis-desktop.cmd
    - Voice:    DEPS INSTALLED, service registration DEFERRED to Phase 2
                (voice-agent imports Linux-only modules - PipeWire / sdnotify
                / xdotool. Run under WSL2 with install.sh until Phase 2 lands.)

  Next steps:
    1. Edit $InstallDir\.env and fill in real API keys.
    2. Open a NEW PowerShell window so PATH picks up $LocalBin.
    3. Try the CLI:
         jarvis
    4. Start the web app (optional):
         cd $InstallDir\src\web ; bun dev
    5. Run the desktop app (Tauri):
         Click 'JARVIS' in the Start Menu, or:
         $InstallDir\src\desktop-tauri\src-tauri\target\release\jarvis-desktop.exe

  Re-run this script anytime to update a channel.
  Skip channels with -SkipCli / -SkipVoice / -SkipDesktop / -SkipWeb.
"@
}

# ── Main ─────────────────────────────────────────────────────────────────
function Invoke-Main {
    Write-Host 'JARVIS installer (Windows / PowerShell)' -ForegroundColor Cyan
    Get-Invocation
    Test-Prereqs

    if ($DryRun) {
        Write-Section 'Dry-run complete'
        Write-Sub "Detected/chosen install dir: $InstallDir"
        Write-Sub 'All prereqs present. Re-run without -DryRun to actually install.'
        exit 0
    }

    Sync-Repo
    Install-Cli
    Install-Web
    Install-VoiceAgent
    Install-Desktop
    Install-BashSandbox       # bash-tool sandbox - deferred on Windows
    New-BridgeToken           # ~/.jarvis/local-api-token.env + web .env.local
    Set-LiveKitKeys
    Test-ComputerUseDeps      # optional probes (Windows-style)
    Install-AudioProfile      # deferred on Windows
    Install-EchoCancel        # deferred on Windows
    Set-EnvTemplate
    Write-Summary
}

Invoke-Main
