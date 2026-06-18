# ============================================================================
# JARVIS one-shot installer for Windows (PowerShell).
# ============================================================================
# Voice-first AI assistant by Ulrich Ando. Real-time speech in / out, with
# direct tools for desktop, browser, and multi-step coding work.
#
# Usage (PowerShell, curl-pipe):
#   iex (irm https://raw.githubusercontent.com/ulrichando/jarvis/master/install.ps1)
#
# Usage (CMD, two-line bootstrap):
#   curl -fsSL https://raw.githubusercontent.com/ulrichando/jarvis/master/install.cmd -o install.cmd && install.cmd && del install.cmd
#
# Or after cloning the repo:
#   .\install.ps1 [-SkipCli] [-SkipVoice] [-SkipDesktop] [-SkipWeb] [-NoVenv] [-SkipSetup]
#
# Phase 3 status (2026-05-24): CLI + Desktop UI + voice-agent fully
# supported on Windows. The voice-agent's Python platform-abstraction
# shims landed in Phase 2 (audio/service-control/desktop-control), and
# install.ps1 now downloads nssm 2.24 (SHA256-verified) and registers
# both voice services -- mirroring install.sh's systemd-unit install.
# Service registration requires elevation; without it, the dep install
# still completes and the script prints a clear "re-run elevated" hint.
# Use -StartServices to launch the services immediately after install
# (default off -- the user is expected to edit .env first).
#
# Idempotent: re-running skips channels that are already in good shape.
# ============================================================================

#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$NoVenv,
    [switch]$SkipSetup,
    [string]$Branch = "master",

    # Higher-precedence variants of -Branch for reproducible installs
    # (release-tag pin from the Desktop wizard, CI bundles, etc.).
    # Precedence: Commit > Tag > Branch.
    [string]$Commit = "",
    [string]$Tag = "",

    # Channel skip switches — match Linux's JARVIS_SKIP_* env vars.
    [switch]$SkipCli,
    [switch]$SkipVoice,
    [switch]$SkipDesktop,
    [switch]$SkipWeb,
    [switch]$SkipCdp,

    # Auto-install missing prereqs via winget. Off by default — the
    # default invocation surfaces missing prereqs as actionable hints.
    [switch]$AutoInstall,

    # Auto-start the registered voice services after Install-WindowsVoiceServices.
    # Off by default so users can edit .env (fill in API keys) before the
    # voice-agent first launches. Mirrors install.sh's "units installed but
    # NOT enabled / NOT started" stance.
    [switch]$StartServices,

    # Custom install root override. Default = %LOCALAPPDATA%\jarvis.
    # On non-Windows hosts (PSCore on Linux, for syntax-check/CI runs)
    # $env:LOCALAPPDATA is null; fall back to .NET's temp path so param
    # binding still works -- the script's actual install flow is Windows-only.
    [string]$JarvisHome = $(if ($env:LOCALAPPDATA) { "$env:LOCALAPPDATA\jarvis" } else { Join-Path ([System.IO.Path]::GetTempPath()) 'jarvis' }),
    [string]$InstallDir = $(if ($env:LOCALAPPDATA) { "$env:LOCALAPPDATA\jarvis\jarvis" } else { Join-Path ([System.IO.Path]::GetTempPath()) 'jarvis\jarvis' }),

    # --- Stage protocol (additive; default invocation behaves as before) ---
    # Programmatic drivers (future Tauri onboarding wizard, CI, install.sh
    # parity) can drive the installer one stage at a time and parse JSON
    # progress frames. CLI users on the canonical iex/irm one-liner never
    # touch these flags.
    [switch]$Manifest,
    [string]$Stage,
    [switch]$ProtocolVersion,
    [switch]$NonInteractive,
    [switch]$Json,

    # --- Dry run ---
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# Suppress Invoke-WebRequest's per-chunk progress bar. Windows PowerShell
# 5.1's progress UI repaints synchronously on every received byte, pegging
# a CPU core and slowing downloads 10-100x (a 57MB PortableGit grab takes
# 5 minutes with progress on vs 20 seconds with progress off, same network).
# Every IWR call below is fire-and-forget — we never need the bar. The
# preference is process-scoped and restored automatically on script exit.
$ProgressPreference = "SilentlyContinue"

# Force the console to UTF-8 so non-ASCII output from native tools (npm
# check marks, git bullets, playwright box-drawing progress bars) renders
# correctly instead of as IBM437/cp1252 mojibake. This is a DISPLAY-only
# fix; underlying bytes are already correct. The file itself stays pure
# ASCII (PS 5.1 parser-safe — see the curly-quote / em-dash avoidance
# throughout). Console encoding reverts on script exit.
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
} catch {
    # Some constrained PowerShell hosts disallow encoding mutation.
    # Mojibake on output is cosmetic-only; install still works.
}

# ============================================================================
# Constants
# ============================================================================

$RepoUrlHttps = "https://github.com/ulrichando/jarvis.git"
$RepoUrlSsh   = "git@github.com:ulrichando/jarvis.git"

# Python pin. 3.13 matches voice-agent CLAUDE.md baseline. uv will install
# this if absent — no admin needed.
$PythonVersion = "3.13"

# Stage-protocol version. Bumped only for breaking changes to the manifest
# schema or stdout JSON shape. Adding a new stage does NOT bump this —
# drivers iterate the manifest dynamically.
$InstallStageProtocolVersion = 1

# Paths derived from $JarvisHome.
$script:LocalBin     = Join-Path $JarvisHome 'bin'
$script:GitPortable  = Join-Path $JarvisHome 'git'
$script:NodePortable = Join-Path $JarvisHome 'node'
$script:DataDir      = Join-Path $JarvisHome 'data'
$script:LogsDir      = Join-Path $JarvisHome 'data\logs'

# User-data dir. Matches Linux's ~/.jarvis layout so memory/conversation
# DBs, livekit-keys, and the bridge token live in the same logical home
# on both platforms. Voice-agent reads it via tools.runtime.get_jarvis_home,
# which honours the JARVIS_HOME env var.
$script:ConfigDir    = if ($env:USERPROFILE) {
    Join-Path $env:USERPROFILE '.jarvis'
} elseif ($env:HOME) {
    Join-Path $env:HOME '.jarvis'
} else {
    Join-Path ([System.IO.Path]::GetTempPath()) '.jarvis'
}

$script:StartMenuDir = if ($env:APPDATA) {
    Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
} else {
    # Same Linux/CI fallback as the $JarvisHome default -- the Start Menu
    # path is only consumed inside Install-StartMenuShortcut, which is
    # Windows-only in practice.
    Join-Path ([System.IO.Path]::GetTempPath()) 'StartMenu'
}

# Pinned Git for Windows release. Static github.com archive URLs are NOT
# rate-limited (unlike api.github.com/repos/.../releases/latest, which
# caps at 60 req/hr/IP and breaks installers behind CGNAT / corporate NAT).
# Bump deliberately; PortableGit ships bash.exe + sh + sed + awk + grep
# in bin\ + usr\bin\ which the JARVIS terminal/bash tool needs on Windows.
$script:GitVersion       = "2.54.0"
$script:GitReleaseTag    = "v2.54.0.windows.1"

# ============================================================================
# Output helpers
# ============================================================================

function Write-Banner {
    Write-Host ""
    Write-Host "+---------------------------------------------------------+" -ForegroundColor Cyan
    Write-Host "|             JARVIS Voice Assistant Installer            |" -ForegroundColor Cyan
    Write-Host "+---------------------------------------------------------+" -ForegroundColor Cyan
    Write-Host "|  Voice-first AI assistant by Ulrich Ando.               |" -ForegroundColor Cyan
    Write-Host "+---------------------------------------------------------+" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Section { param([string]$Msg) Write-Host ''; Write-Host "=== $Msg ===" -ForegroundColor Cyan }
function Write-Info    { param([string]$Msg) Write-Host "-> $Msg" -ForegroundColor Cyan }
function Write-Sub     { param([string]$Msg) Write-Host "   $Msg" }
function Write-Ok      { param([string]$Msg) Write-Host "[OK] $Msg" -ForegroundColor Green }
function Write-Warn2   { param([string]$Msg) Write-Host "[!]  $Msg" -ForegroundColor Yellow }
function Write-Err     { param([string]$Msg) Write-Host "[X]  $Msg" -ForegroundColor Red }
function Stop-WithError { param([string]$Msg) Write-Err $Msg; exit 1 }

# ============================================================================
# Helpers
# ============================================================================

function Test-Command {
    param([Parameter(Mandatory)][string]$Name)
    return [bool](Get-Command -Name $Name -ErrorAction SilentlyContinue)
}

function Invoke-Step {
    # Runs a scriptblock unless -DryRun is set, in which case it logs
    # what would have run and returns. Matches install.sh's
    # JARVIS_DRY_RUN=1 bail-out semantics.
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

# Refresh $env:Path from User + Machine registry hives. Stage drivers
# invoke each stage in a fresh PowerShell child process; those children
# inherit env from the parent driver shell, NOT from the registry. When
# an earlier stage (Stage-Git, Stage-Node, Stage-Uv) installs a binary
# and pushes its dir into User PATH, the next child's $env:Path is stale
# and the binary appears missing. This helper re-reads PATH so every
# Invoke-Stage starts with a fresh view. Cheap and idempotent.
function Sync-EnvPath {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
}

function Add-ToUserPath {
    param([Parameter(Mandatory)][string]$NewEntry)
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $items = if ($userPath) { $userPath -split ";" } else { @() }
    if ($items -notcontains $NewEntry) {
        $items += $NewEntry
        [Environment]::SetEnvironmentVariable("Path", ($items -join ";"), "User")
        $env:Path = "$NewEntry;$env:Path"
        return $true
    }
    # Make sure the current session sees it even when User already has it.
    if (($env:Path -split ";") -notcontains $NewEntry) {
        $env:Path = "$NewEntry;$env:Path"
    }
    return $false
}

function Resolve-NpmCmd {
    # Node on Windows ships BOTH npm.cmd and npm.ps1. Get-Command's default
    # ordering picks whichever lands first in PATHEXT — often .ps1 — but
    # .ps1 requires PowerShell's execution policy to allow unsigned scripts,
    # which the default Restricted / RemoteSigned profile blocks. .cmd has
    # no such restriction. Prefer .cmd when the sibling exists.
    $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npmCmd) { return $null }
    $npmExe = $npmCmd.Source
    if ($npmExe -like "*.ps1") {
        $npmCmdSibling = Join-Path (Split-Path $npmExe -Parent) "npm.cmd"
        if (Test-Path $npmCmdSibling) { return $npmCmdSibling }
    }
    return $npmExe
}

function Set-WindowsAclUserOnly {
    # NTFS chmod-600 equivalent: strip inheritance, drop all access
    # rules, grant the current user FullControl. Use for secrets only
    # (.env, bridge token, LiveKit keys YAML).
    param([Parameter(Mandatory)][string]$Path)
    try {
        $acl = Get-Acl $Path
        $acl.SetAccessRuleProtection($true, $false)
        $acl.Access | ForEach-Object { [void]$acl.RemoveAccessRule($_) }
        $user = "$env:USERDOMAIN\$env:USERNAME"
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $user, 'FullControl', 'Allow'
        )
        $acl.AddAccessRule($rule)
        Set-Acl -Path $Path -AclObject $acl
    } catch {
        Write-Warn2 "couldn't tighten ACL on $Path : $($_.Exception.Message)"
    }
}

# Detect: curl-pipe vs local checkout. When invoked via iex/irm,
# $PSCommandPath / $MyInvocation.MyCommand.Path are empty — fall through
# to $InstallDir. When run locally (.\install.ps1), if its sibling
# CLAUDE.md mentions JARVIS, treat that dir as the checkout root.
function Get-Invocation {
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

# ============================================================================
# Prereq install helpers — uv, Python, Git, Node, system tools
# ============================================================================

function Install-Uv {
    # uv (Astral) handles both Python install + venv + dependency sync.
    # It is one binary, user-scoped (no admin), and 10-100x faster than
    # pip for cold cache resolves. Used for the voice-agent's venv.
    Write-Info "Checking for uv package manager..."

    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $script:UvCmd = "uv"
        Write-Ok "uv found ($(uv --version))"
        return $true
    }

    # Check the well-known install locations the astral.sh installer
    # drops uv into.
    foreach ($uvPath in @(
        "$env:USERPROFILE\.local\bin\uv.exe",
        "$env:USERPROFILE\.cargo\bin\uv.exe"
    )) {
        if (Test-Path $uvPath) {
            $script:UvCmd = $uvPath
            Write-Ok "uv found at $uvPath ($(& $uvPath --version))"
            return $true
        }
    }

    Write-Info "Installing uv (fast Python package manager)..."
    # Capture EAP outside the try block so the catch's restore call always
    # has a meaningful value to use if the body throws before the assignment.
    $prevEAP = $ErrorActionPreference
    try {
        # Relax EAP=Stop around the astral installer. It writes download
        # progress to stderr; under EAP=Stop, PowerShell wraps stderr lines
        # captured via 2>&1 as ErrorRecord objects and throws on the first
        # one even though uv installs successfully. Same workaround we use
        # for `uv python install`, npm install, playwright, etc. — check
        # success via Test-Path on the expected binary afterwards.
        $ErrorActionPreference = "Continue"
        powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP

        $uvExe = "$env:USERPROFILE\.local\bin\uv.exe"
        if (-not (Test-Path $uvExe)) {
            $uvExe = "$env:USERPROFILE\.cargo\bin\uv.exe"
        }
        if (-not (Test-Path $uvExe)) {
            Sync-EnvPath
            if (Get-Command uv -ErrorAction SilentlyContinue) {
                $uvExe = (Get-Command uv).Source
            }
        }

        if (Test-Path $uvExe) {
            $script:UvCmd = $uvExe
            Write-Ok "uv installed ($(& $uvExe --version))"
            return $true
        }

        Write-Err "uv installed but not found on PATH"
        Write-Info "Try restarting your terminal and re-running"
        return $false
    } catch {
        if ($prevEAP) { $ErrorActionPreference = $prevEAP }
        Write-Err "Failed to install uv: $_"
        Write-Info "Install manually: https://docs.astral.sh/uv/getting-started/installation/"
        return $false
    }
}

# Re-discover uv without re-installing it. Stage drivers invoke each stage
# in a fresh PowerShell process, so $script:UvCmd set by Install-Uv in a
# prior process is invisible here. Stages that depend on uv call this at
# entry to populate $script:UvCmd from PATH / known install paths. Fast
# no-op when $script:UvCmd is already set in this process.
function Resolve-UvCmd {
    if ($script:UvCmd) {
        if ($script:UvCmd -eq "uv") {
            if (Get-Command uv -ErrorAction SilentlyContinue) { return }
        } elseif (Test-Path $script:UvCmd) {
            return
        }
    }
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $script:UvCmd = "uv"
        return
    }
    Sync-EnvPath
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $script:UvCmd = "uv"
        return
    }
    foreach ($uvPath in @("$env:USERPROFILE\.local\bin\uv.exe", "$env:USERPROFILE\.cargo\bin\uv.exe")) {
        if (Test-Path $uvPath) {
            $script:UvCmd = $uvPath
            return
        }
    }
    throw "uv is not installed or not on PATH. Run install.ps1 -Stage uv first."
}

function Test-Python {
    # Resolve a Python interpreter via uv. uv will download + install the
    # pinned interpreter to %LOCALAPPDATA%\uv\python\ if absent — no admin,
    # no Microsoft Store, no system Python conflict.
    Write-Info "Checking Python $PythonVersion..."

    try {
        $pythonPath = & $UvCmd python find $PythonVersion 2>$null
        if ($pythonPath) {
            $ver = & $pythonPath --version 2>$null
            Write-Ok "Python found: $ver"
            $script:PythonExe = $pythonPath
            return $true
        }
    } catch {}

    Write-Info "Python $PythonVersion not found, installing via uv..."
    $prevEAP = $ErrorActionPreference
    try {
        # uv writes download progress to stderr; under EAP=Stop those wrap
        # as ErrorRecords and throw even when the install succeeds. Relax
        # then verify by re-finding the interpreter.
        $ErrorActionPreference = "Continue"
        $uvOutput = & $UvCmd python install $PythonVersion 2>&1
        $uvExitCode = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP

        $pythonPath = & $UvCmd python find $PythonVersion 2>$null
        if ($pythonPath) {
            $ver = & $pythonPath --version 2>$null
            Write-Ok "Python installed: $ver"
            $script:PythonExe = $pythonPath
            return $true
        }
        if ($uvExitCode -ne 0) {
            Write-Warn2 "uv python install output:"
            Write-Host $uvOutput -ForegroundColor DarkGray
        }
    } catch {
        if ($prevEAP) { $ErrorActionPreference = $prevEAP }
        Write-Warn2 "uv python install error: $_"
    }

    # Try any Python 3.11+ as a fallback (voice-agent CLAUDE.md baseline).
    Write-Info "Trying to find any existing Python 3.11+..."
    foreach ($fallbackVer in @("3.13", "3.12", "3.11")) {
        try {
            $pythonPath = & $UvCmd python find $fallbackVer 2>$null
            if ($pythonPath) {
                $ver = & $pythonPath --version 2>$null
                Write-Ok "Found fallback: $ver"
                $script:PythonVersion = $fallbackVer
                $script:PythonExe = $pythonPath
                return $true
            }
        } catch {}
    }

    # Final fallback: system python — but skip the Microsoft Store stub.
    # %LOCALAPPDATA%\Microsoft\WindowsApps\python.exe is a 0-byte reparse
    # point that prints "Python was not found; run without arguments to
    # install from the Microsoft Store..." and exits non-zero. Get-Command
    # finds it; invoking it looks like our installer crashing.
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        $isStoreStub = $false
        try {
            $pythonSource = $pythonCmd.Source
            if ($pythonSource -and $pythonSource -like "*\WindowsApps\*") {
                $isStoreStub = $true
            } else {
                $item = Get-Item $pythonSource -ErrorAction SilentlyContinue
                if ($item -and $item.Length -eq 0) { $isStoreStub = $true }
            }
        } catch {}

        if (-not $isStoreStub) {
            try {
                $prevEAP2 = $ErrorActionPreference
                $ErrorActionPreference = "Continue"
                $sysVer = & python --version 2>&1
                $ErrorActionPreference = $prevEAP2
                if ($sysVer -match "Python 3\.(1[1-9]|[2-9][0-9])") {
                    Write-Ok "Using system Python: $sysVer"
                    $script:PythonExe = "python"
                    return $true
                }
            } catch {
                if ($prevEAP2) { $ErrorActionPreference = $prevEAP2 }
            }
        }
    }

    Write-Err "Failed to install Python $PythonVersion"
    Write-Info "Install Python 3.13 manually, then re-run this script:"
    Write-Info "  https://www.python.org/downloads/"
    Write-Info "  Or: winget install Python.Python.3.13"
    return $false
}

function Install-Git {
    <#
    .SYNOPSIS
    Ensure Git (and Git Bash) are installed. Git for Windows bundles bash.exe
    which the JARVIS terminal/bash tool needs to run shell commands.

    Priority order (deliberately simple — no winget, no registry, no system
    package manager):
      1. Existing `git` on PATH — use it.
      2. Download PortableGit from the official git-for-windows GitHub
         release (self-extracting 7z.exe) and unpack to
         $JarvisHome\git — no admin, works on locked-down enterprise
         machines and machines with a broken system Git install.

    Why PortableGit, not MinGit: MinGit is the minimal-automation distribution
    and ships ONLY git.exe. JARVIS needs bash.exe to run shell commands.
    PortableGit is the full Git for Windows distribution without the installer
    UI; it ships git.exe + bash.exe + sh + awk + sed + grep + curl + ssh
    in bin\ + usr\bin\.

    We deliberately skip winget because it fails badly when the system Git
    install is half-installed (partially registered or uninstall-blocked).
    Owning the JARVIS copy of Git is predictable and recoverable: if it
    breaks, Remove-Item $JarvisHome\git and re-running fully recovers.

    After install we locate bash.exe and persist the path in
    JARVIS_GIT_BASH_PATH (User scope) so JARVIS can find it in a fresh
    shell without waiting for PATH propagation.
    #>
    Write-Info "Checking Git..."

    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-Ok "Git found ($(git --version))"
        Set-GitBashEnvVar
        return $true
    }

    Write-Info "Git not found -- downloading PortableGit to $GitPortable\ ..."
    Write-Info "(no admin rights required; isolated from any system Git install)"

    try {
        $arch = if ([Environment]::Is64BitOperatingSystem) {
            if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64" -or $env:PROCESSOR_ARCHITEW6432 -eq "ARM64") {
                "arm64"
            } else {
                "64-bit"
            }
        } else {
            # PortableGit does not ship 32-bit; fall back to MinGit 32-bit
            # with a loud warning — bash-based features will be unavailable.
            "32-bit-mingit"
        }

        $gitVer    = $script:GitVersion
        $gitTag    = $script:GitReleaseTag

        if ($arch -eq "32-bit-mingit") {
            Write-Warn2 "32-bit Windows detected -- PortableGit is 64-bit only. Installing MinGit 32-bit as a last resort; bash-dependent JARVIS features (terminal tool, browser sandbox) will not work on this machine."
            $assetName     = "MinGit-$gitVer-32-bit.zip"
            $downloadIsZip = $true
        } elseif ($arch -eq "arm64") {
            $assetName     = "PortableGit-$gitVer-arm64.7z.exe"
            $downloadIsZip = $false
        } else {
            $assetName     = "PortableGit-$gitVer-64-bit.7z.exe"
            $downloadIsZip = $false
        }

        $downloadUrl = "https://github.com/git-for-windows/git/releases/download/$gitTag/$assetName"
        $tmpFile = "$env:TEMP\$assetName"
        $gitDir = $script:GitPortable

        Write-Info "Downloading $assetName (Git for Windows $gitVer)..."
        New-Item -ItemType Directory -Force -Path (Split-Path $tmpFile -Parent) | Out-Null
        Invoke-WebRequest -Uri $downloadUrl -OutFile $tmpFile -UseBasicParsing

        if (Test-Path $gitDir) {
            Write-Info "Removing previous Git install at $gitDir ..."
            Remove-Item -Recurse -Force $gitDir
        }
        New-Item -ItemType Directory -Path $gitDir -Force | Out-Null

        if ($downloadIsZip) {
            Expand-Archive -Path $tmpFile -DestinationPath $gitDir -Force
        } else {
            # PortableGit is a self-extracting 7z archive. Invoke it with
            # -o<target> -y (silent) to extract. No 7z install required;
            # it is fully self-contained.
            Write-Info "Extracting PortableGit to $gitDir ..."
            $extractProc = Start-Process -FilePath $tmpFile `
                -ArgumentList "-o`"$gitDir`"", "-y" `
                -NoNewWindow -Wait -PassThru
            if ($extractProc.ExitCode -ne 0) {
                throw "PortableGit extraction failed (exit code $($extractProc.ExitCode))"
            }
        }
        Remove-Item -Force $tmpFile -ErrorAction SilentlyContinue

        # PortableGit layout: cmd\git.exe + bin\bash.exe + usr\bin\ (coreutils)
        # MinGit layout:      cmd\git.exe + usr\bin\bash.exe (if present)
        $gitExe = "$gitDir\cmd\git.exe"
        if (-not (Test-Path $gitExe)) {
            throw "Git extraction did not produce git.exe at $gitExe"
        }

        # Session PATH so the rest of this install run can use git.
        $env:Path = "$gitDir\cmd;$env:Path"

        # Persist to User PATH so fresh shells see it. PortableGit needs:
        # cmd\     for git.exe
        # bin\     for bash.exe + core tools
        # usr\bin\ for perl, ssh, curl, and other POSIX coreutils.
        foreach ($entry in @("$gitDir\cmd", "$gitDir\bin", "$gitDir\usr\bin")) {
            [void](Add-ToUserPath -NewEntry $entry)
        }

        Write-Ok "Git $(& $gitExe --version) installed to $gitDir (portable, user-scoped)"
        Set-GitBashEnvVar
        return $true
    } catch {
        Write-Err "Could not install portable Git: $_"
        Write-Info ""
        Write-Info "Fallback: install Git manually from https://git-scm.com/download/win"
        Write-Info "then re-run this installer. JARVIS needs Git Bash on Windows to run"
        Write-Info "shell commands (same as Claude Code and other coding agents)."
        return $false
    }
}

function Set-GitBashEnvVar {
    <#
    .SYNOPSIS
    Locate bash.exe from an already-installed Git and persist the path in
    JARVIS_GIT_BASH_PATH (User env scope) so JARVIS can find it before
    PATH propagation completes in a newly-spawned shell.
    #>
    $candidates = @()

    # Our own portable Git install is always checked first so a broken
    # system Git doesn't hijack us. If the user had working system Git
    # we'd have returned early from Install-Git's fast path; we only
    # reach here when bash needs to be located.
    #
    # Layouts:
    #   PortableGit (our default): $GitPortable\bin\bash.exe
    #   MinGit (32-bit fallback):  $GitPortable\usr\bin\bash.exe
    $candidates += "$script:GitPortable\bin\bash.exe"
    $candidates += "$script:GitPortable\usr\bin\bash.exe"

    # git.exe on PATH can tell us where the install root is.
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if ($gitCmd) {
        $gitExe = $gitCmd.Source
        # Full Git for Windows: <root>\cmd\git.exe + <root>\bin\bash.exe
        # MinGit:               <root>\cmd\git.exe + <root>\usr\bin\bash.exe
        $gitRoot = Split-Path (Split-Path $gitExe -Parent) -Parent
        $candidates += "$gitRoot\bin\bash.exe"
        $candidates += "$gitRoot\usr\bin\bash.exe"
    }

    # Standard system install locations as a final fallback.
    $candidates += "${env:ProgramFiles}\Git\bin\bash.exe"
    $pf86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    if ($pf86) { $candidates += "$pf86\Git\bin\bash.exe" }
    $candidates += "${env:LocalAppData}\Programs\Git\bin\bash.exe"

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            [Environment]::SetEnvironmentVariable("JARVIS_GIT_BASH_PATH", $candidate, "User")
            $env:JARVIS_GIT_BASH_PATH = $candidate
            Write-Info "Set JARVIS_GIT_BASH_PATH=$candidate"
            return
        }
    }

    Write-Warn2 "Could not locate bash.exe -- JARVIS may not find Git Bash."
    Write-Info "If needed, set JARVIS_GIT_BASH_PATH manually to your bash.exe path."
}

function Test-Node {
    # Node.js is required for the Desktop (Tauri frontend), Web (Next.js),
    # and CLI (Bun -- although Bun is its own runtime, npm is still used
    # by the Tauri build). Test for existing install; download the portable
    # zip if absent (no admin, no UAC, no winget MSI dance).
    Write-Info "Checking Node.js..."

    if (Get-Command node -ErrorAction SilentlyContinue) {
        Write-Ok "Node.js $(node --version) found"
        $script:HasNode = $true
        return $true
    }

    # Check our own managed install from a previous run.
    $managedNode = "$script:NodePortable\node.exe"
    if (Test-Path $managedNode) {
        $env:Path = "$script:NodePortable;$env:Path"
        Write-Ok "Node.js $(& $managedNode --version) found (JARVIS-managed)"
        $script:HasNode = $true
        return $true
    }

    Write-Info "Node.js not found -- installing portable Node.js LTS..."

    # Portable-zip path: no UAC, no admin, no winget MSI. winget install
    # OpenJS.NodeJS.LTS triggers a system-wide MSI that prompts UAC; that
    # dialog often appears MINIMIZED in the taskbar and the install silently
    # waits for consent — looks like a hang. The portable zip drops node.exe
    # + npm into $JarvisHome\node\ which is user-scoped and identical in
    # spirit to PortableGit.
    Write-Info "Downloading portable Node.js to $script:NodePortable\ ..."
    Write-Info "(no admin rights required; isolated from any system Node install)"
    try {
        $arch = if ([Environment]::Is64BitOperatingSystem) { "x64" } else { "x86" }
        $nodeMajor = "22"  # Current LTS as of 2026-05; matches Tauri 2's tested matrix.
        $indexUrl = "https://nodejs.org/dist/latest-v${nodeMajor}.x/"
        $indexPage = Invoke-WebRequest -Uri $indexUrl -UseBasicParsing
        $zipName = ($indexPage.Content | Select-String -Pattern "node-v${nodeMajor}\.\d+\.\d+-win-${arch}\.zip" -AllMatches).Matches[0].Value

        if ($zipName) {
            $downloadUrl = "${indexUrl}${zipName}"
            $tmpZip = "$env:TEMP\$zipName"
            $tmpDir = "$env:TEMP\jarvis-node-extract"

            Invoke-WebRequest -Uri $downloadUrl -OutFile $tmpZip -UseBasicParsing
            if (Test-Path $tmpDir) { Remove-Item -Recurse -Force $tmpDir }
            Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force

            $extractedDir = Get-ChildItem $tmpDir -Directory | Select-Object -First 1
            if ($extractedDir) {
                if (Test-Path $script:NodePortable) { Remove-Item -Recurse -Force $script:NodePortable }
                Move-Item $extractedDir.FullName $script:NodePortable

                $env:Path = "$script:NodePortable;$env:Path"
                [void](Add-ToUserPath -NewEntry $script:NodePortable)

                Write-Ok "Node.js $(& "$script:NodePortable\node.exe" --version) installed to $script:NodePortable\ (portable, user-scoped)"
                $script:HasNode = $true

                Remove-Item -Force $tmpZip -ErrorAction SilentlyContinue
                Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
                return $true
            }
        }
    } catch {
        Write-Warn2 "Portable Node.js download failed: $_"
    }

    # Fallback: winget (demoted because the MSI install triggers UAC that
    # frequently appears minimized — looks like a hang to users).
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Info "Falling back to winget (may prompt UAC -- check your taskbar for a flashing icon)..."
        $prevEAP = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            winget install OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
            $ErrorActionPreference = $prevEAP
            Sync-EnvPath
            if (Get-Command node -ErrorAction SilentlyContinue) {
                Write-Ok "Node.js $(node --version) installed via winget"
                $script:HasNode = $true
                return $true
            }
        } catch {
            if ($prevEAP) { $ErrorActionPreference = $prevEAP }
        }
    }

    Write-Info "Install manually: https://nodejs.org/en/download/"
    $script:HasNode = $false
    return $true
}

function Test-Bun {
    # Bun is used by the CLI (src/cli/) and Web (src/web/) for dep install +
    # the CLI runtime. Install via the official Windows installer if absent.
    Write-Info "Checking Bun..."
    if (Get-Command bun -ErrorAction SilentlyContinue) {
        Write-Ok "Bun $(bun --version) found"
        $script:HasBun = $true
        return $true
    }
    Write-Info "Bun not found -- installing via the official PowerShell script..."
    $prevEAP = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        powershell -ExecutionPolicy ByPass -c "irm bun.sh/install.ps1 | iex" 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP
        Sync-EnvPath
        # Default install location for Bun on Windows.
        $bunExe = "$env:USERPROFILE\.bun\bin\bun.exe"
        if (Test-Path $bunExe) {
            [void](Add-ToUserPath -NewEntry (Split-Path $bunExe -Parent))
            Write-Ok "Bun $(& $bunExe --version) installed"
            $script:HasBun = $true
            return $true
        }
        if (Get-Command bun -ErrorAction SilentlyContinue) {
            Write-Ok "Bun $(bun --version) installed"
            $script:HasBun = $true
            return $true
        }
    } catch {
        if ($prevEAP) { $ErrorActionPreference = $prevEAP }
    }
    Write-Warn2 "Bun install failed -- CLI and Web channels will not work"
    Write-Info "Install manually: https://bun.sh/docs/installation"
    $script:HasBun = $false
    return $true
}

function Test-Cargo {
    # Cargo is required only for the Desktop channel (Tauri 2 backend on
    # Windows is MSVC-only). If absent and -AutoInstall is set, install
    # rustup; otherwise warn.
    Write-Info "Checking cargo (Rust toolchain)..."
    if (Get-Command cargo -ErrorAction SilentlyContinue) {
        Write-Ok "cargo $((cargo --version) -replace '^cargo\s+', '')"
        $script:HasCargo = $true
        return $true
    }
    if ($AutoInstall -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Info "Installing rustup via winget..."
        try {
            winget install --id Rustlang.Rustup --silent --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
            Sync-EnvPath
            if (Get-Command cargo -ErrorAction SilentlyContinue) {
                Write-Ok "cargo $((cargo --version) -replace '^cargo\s+', '')"
                $script:HasCargo = $true
                return $true
            }
        } catch {}
    }
    Write-Warn2 "cargo not found -- Desktop build will be skipped."
    Write-Info "Install rustup: winget install Rustlang.Rustup"
    Write-Info "Or download from https://win.rustup.rs"
    Write-Info "(re-run with -SkipDesktop to silence this warning)"
    $script:HasCargo = $false
    return $true
}

function Test-MsvcBuildTools {
    # Tauri 2 on Windows is MSVC-only -- the bundled webview2-com crate
    # depends on the MSVC ABI for COM interop. Heuristic check: look for
    # the Visual Studio install root. A clean fail-fast would probe link.exe
    # or cl.exe on PATH, but those are only on PATH inside a Developer
    # Command Prompt, not the user's regular shell.
    $vsDir = "C:\Program Files (x86)\Microsoft Visual Studio"
    if (-not (Test-Path $vsDir)) {
        Write-Warn2 "MSVC Build Tools may be missing -- Tauri's cargo build needs the MSVC toolchain."
        Write-Sub  'Install: winget install --id Microsoft.VisualStudio.2022.BuildTools --silent --override "--quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"'
        Write-Sub  "(Or run with -SkipDesktop to skip the desktop build.)"
    }
}

function Install-SystemPackages {
    # Optional speed-ups: ripgrep (fast file search via the code_search tool)
    # and ffmpeg (TTS audio decoding fallback). Probe + best-effort install
    # via winget / choco / scoop. Never block install on these.
    $script:HasRipgrep = $false
    $script:HasFfmpeg = $false
    $needRipgrep = $false
    $needFfmpeg = $false

    Write-Info "Checking ripgrep (fast file search)..."
    if (Get-Command rg -ErrorAction SilentlyContinue) {
        $version = rg --version | Select-Object -First 1
        Write-Ok "$version found"
        $script:HasRipgrep = $true
    } else {
        $needRipgrep = $true
    }

    Write-Info "Checking ffmpeg (audio decode helper)..."
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        Write-Ok "ffmpeg found"
        $script:HasFfmpeg = $true
    } else {
        $needFfmpeg = $true
    }

    if (-not $needRipgrep -and -not $needFfmpeg) { return }

    $wingetPkgs = @()
    $chocoPkgs = @()
    $scoopPkgs = @()

    if ($needRipgrep) {
        $wingetPkgs += "BurntSushi.ripgrep.MSVC"
        $chocoPkgs += "ripgrep"
        $scoopPkgs += "ripgrep"
    }
    if ($needFfmpeg) {
        $wingetPkgs += "Gyan.FFmpeg"
        $chocoPkgs += "ffmpeg"
        $scoopPkgs += "ffmpeg"
    }

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Info "Installing optional packages via winget..."
        foreach ($pkg in $wingetPkgs) {
            try {
                winget install $pkg --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
            } catch {}
        }
        Sync-EnvPath
        if ($needRipgrep -and (Get-Command rg -ErrorAction SilentlyContinue)) {
            Write-Ok "ripgrep installed"
            $script:HasRipgrep = $true
            $needRipgrep = $false
        }
        if ($needFfmpeg -and (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
            Write-Ok "ffmpeg installed"
            $script:HasFfmpeg = $true
            $needFfmpeg = $false
        }
        if (-not $needRipgrep -and -not $needFfmpeg) { return }
    }

    if ((Get-Command choco -ErrorAction SilentlyContinue) -and ($needRipgrep -or $needFfmpeg)) {
        Write-Info "Trying Chocolatey..."
        foreach ($pkg in $chocoPkgs) {
            try { choco install $pkg -y 2>&1 | Out-Null } catch {}
        }
        if ($needRipgrep -and (Get-Command rg -ErrorAction SilentlyContinue)) {
            Write-Ok "ripgrep installed via chocolatey"
            $script:HasRipgrep = $true
            $needRipgrep = $false
        }
        if ($needFfmpeg -and (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
            Write-Ok "ffmpeg installed via chocolatey"
            $script:HasFfmpeg = $true
            $needFfmpeg = $false
        }
    }

    if ((Get-Command scoop -ErrorAction SilentlyContinue) -and ($needRipgrep -or $needFfmpeg)) {
        Write-Info "Trying Scoop..."
        foreach ($pkg in $scoopPkgs) {
            try { scoop install $pkg 2>&1 | Out-Null } catch {}
        }
        if ($needRipgrep -and (Get-Command rg -ErrorAction SilentlyContinue)) {
            Write-Ok "ripgrep installed via scoop"
            $script:HasRipgrep = $true
            $needRipgrep = $false
        }
        if ($needFfmpeg -and (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
            Write-Ok "ffmpeg installed via scoop"
            $script:HasFfmpeg = $true
            $needFfmpeg = $false
        }
    }

    if ($needRipgrep) {
        Write-Warn2 "ripgrep not installed (code_search will use a slower fallback)"
        Write-Info "  winget install BurntSushi.ripgrep.MSVC"
    }
    if ($needFfmpeg) {
        Write-Warn2 "ffmpeg not installed (some audio decode paths will be limited)"
        Write-Info "  winget install Gyan.FFmpeg"
    }
}

# ============================================================================
# Repository — clone or update with branch/tag/commit pinning
# ============================================================================

function Install-Repository {
    Write-Info "Installing JARVIS to $InstallDir..."

    $didUpdate = $false

    if (Test-Path $InstallDir) {
        # Existing dir: validate it's a usable git repo (cheap belt + braces
        # — partial Remove-Item from a previous failed install can leave a
        # half-baked .git that wedges every later operation). If valid,
        # update in place; otherwise wipe and clone fresh.
        $repoValid = $false
        if (Test-Path "$InstallDir\.git") {
            Push-Location $InstallDir
            try {
                $global:LASTEXITCODE = 0
                $revParseOut = & git -c windows.appendAtomically=false rev-parse --is-inside-work-tree 2>&1
                $revParseOk = ($LASTEXITCODE -eq 0) -and ($revParseOut -match "true")

                $global:LASTEXITCODE = 0
                $null = & git -c windows.appendAtomically=false status --short 2>&1
                $statusOk = ($LASTEXITCODE -eq 0)

                if ($revParseOk -and $statusOk) {
                    $repoValid = $true
                }
            } catch {}
            Pop-Location
        }

        if ($repoValid) {
            Write-Info "Existing installation found, updating..."
            Push-Location $InstallDir
            # Wrap fetch+checkout in EAP=Continue so git's stderr info
            # lines (e.g. 'From <url>' from git fetch) don't terminate the
            # script under EAP=Stop. We check $LASTEXITCODE for real errors.
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                git -c windows.appendAtomically=false fetch origin
                if ($LASTEXITCODE -ne 0) { throw "git fetch failed (exit $LASTEXITCODE)" }
                # Precedence: Commit > Tag > Branch.
                if ($Commit) {
                    git -c windows.appendAtomically=false fetch origin $Commit
                    git -c windows.appendAtomically=false checkout --detach $Commit
                    if ($LASTEXITCODE -ne 0) { throw "git checkout $Commit failed (exit $LASTEXITCODE)" }
                } elseif ($Tag) {
                    git -c windows.appendAtomically=false fetch origin "refs/tags/${Tag}:refs/tags/${Tag}"
                    git -c windows.appendAtomically=false checkout --detach "refs/tags/$Tag"
                    if ($LASTEXITCODE -ne 0) { throw "git checkout tag $Tag failed (exit $LASTEXITCODE)" }
                } else {
                    git -c windows.appendAtomically=false checkout $Branch
                    if ($LASTEXITCODE -ne 0) { throw "git checkout $Branch failed (exit $LASTEXITCODE)" }
                    git -c windows.appendAtomically=false pull origin $Branch
                    if ($LASTEXITCODE -ne 0) { throw "git pull failed (exit $LASTEXITCODE)" }
                }
            } finally {
                $ErrorActionPreference = $prevEAP
                Pop-Location
            }
            $didUpdate = $true
        } else {
            Write-Warn2 "Existing directory at $InstallDir is not a valid git repo -- replacing it."
            try {
                Remove-Item -Recurse -Force $InstallDir -ErrorAction Stop
            } catch {
                Write-Err "Could not remove $InstallDir : $_"
                Write-Info "Close any programs that might be using files in $InstallDir (editors,"
                Write-Info "terminals, running JARVIS processes) and try again."
                throw
            }
        }
    }

    if (-not $didUpdate) {
        $cloneSuccess = $false

        # Fix Windows git "copy-fd: write returned: Invalid argument" errors.
        # Git for Windows can fail on atomic file ops (hook templates, config
        # lock files) due to antivirus, OneDrive, or NTFS filter drivers.
        # The -c flag injects config before any file I/O occurs.
        Write-Info "Configuring git for Windows compatibility..."
        $env:GIT_CONFIG_COUNT = "1"
        $env:GIT_CONFIG_KEY_0 = "windows.appendAtomically"
        $env:GIT_CONFIG_VALUE_0 = "false"
        git config --global windows.appendAtomically false 2>$null

        Write-Info "Trying SSH clone..."
        $env:GIT_SSH_COMMAND = "ssh -o BatchMode=yes -o ConnectTimeout=5"
        try {
            git -c windows.appendAtomically=false clone --branch $Branch $RepoUrlSsh $InstallDir
            if ($LASTEXITCODE -eq 0) { $cloneSuccess = $true }
        } catch {}
        $env:GIT_SSH_COMMAND = $null

        if (-not $cloneSuccess) {
            if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue }
            Write-Info "SSH failed, trying HTTPS..."
            try {
                git -c windows.appendAtomically=false clone --branch $Branch $RepoUrlHttps $InstallDir
                if ($LASTEXITCODE -eq 0) { $cloneSuccess = $true }
            } catch {}
        }

        # Fallback: download ZIP archive (bypasses git file I/O issues entirely).
        if (-not $cloneSuccess) {
            if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue }
            Write-Warn2 "Git clone failed -- downloading ZIP archive instead..."
            try {
                if ($Commit) {
                    $zipUrl = "https://github.com/ulrichando/jarvis/archive/$Commit.zip"
                    $zipLabel = $Commit
                } elseif ($Tag) {
                    $zipUrl = "https://github.com/ulrichando/jarvis/archive/refs/tags/$Tag.zip"
                    $zipLabel = $Tag
                } else {
                    $zipUrl = "https://github.com/ulrichando/jarvis/archive/refs/heads/$Branch.zip"
                    $zipLabel = $Branch
                }
                $zipPath = "$env:TEMP\jarvis-$zipLabel.zip"
                $extractPath = "$env:TEMP\jarvis-extract"

                Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
                if (Test-Path $extractPath) { Remove-Item -Recurse -Force $extractPath }
                Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force

                # GitHub ZIPs extract to repo-branch/ subdirectory.
                $extractedDir = Get-ChildItem $extractPath -Directory | Select-Object -First 1
                if ($extractedDir) {
                    New-Item -ItemType Directory -Force -Path (Split-Path $InstallDir) -ErrorAction SilentlyContinue | Out-Null
                    Move-Item $extractedDir.FullName $InstallDir -Force
                    Write-Ok "Downloaded and extracted"

                    Push-Location $InstallDir
                    git -c windows.appendAtomically=false init 2>$null
                    git -c windows.appendAtomically=false config windows.appendAtomically false 2>$null
                    git remote add origin $RepoUrlHttps 2>$null
                    Pop-Location
                    Write-Ok "Git repo initialized for future updates"

                    $cloneSuccess = $true
                }

                Remove-Item -Force $zipPath -ErrorAction SilentlyContinue
                Remove-Item -Recurse -Force $extractPath -ErrorAction SilentlyContinue
            } catch {
                Write-Err "ZIP download also failed: $_"
            }
        }

        if (-not $cloneSuccess) {
            throw "Failed to download repository (tried git clone SSH, HTTPS, and ZIP)"
        }
    }

    Push-Location $InstallDir
    git -c windows.appendAtomically=false config windows.appendAtomically false 2>$null

    # Post-clone pin: when a fresh clone landed us on $Branch's tip, honour
    # the higher-precedence -Commit / -Tag as a detached HEAD.
    if (-not $didUpdate) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            if ($Commit) {
                Write-Info "Pinning to commit $Commit..."
                git -c windows.appendAtomically=false fetch origin $Commit
                git -c windows.appendAtomically=false checkout --detach $Commit
                if ($LASTEXITCODE -ne 0) { throw "git checkout $Commit failed (exit $LASTEXITCODE)" }
            } elseif ($Tag) {
                Write-Info "Pinning to tag $Tag..."
                git -c windows.appendAtomically=false fetch origin "refs/tags/${Tag}:refs/tags/${Tag}"
                git -c windows.appendAtomically=false checkout --detach "refs/tags/$Tag"
                if ($LASTEXITCODE -ne 0) { throw "git checkout tag $Tag failed (exit $LASTEXITCODE)" }
            }
        } finally {
            $ErrorActionPreference = $prevEAP
        }
    }
    Pop-Location

    Write-Ok "Repository ready at $InstallDir"
}

# ============================================================================
# Voice agent — Python venv + deps via uv
# ============================================================================

function Install-VoiceAgent {
    if ($SkipVoice) { Write-Warn2 "skipping Voice Agent (-SkipVoice)"; return }
    Write-Section "Installing Voice Agent (~2-3 min; livekit-agents is heavy)"

    $va     = Join-Path $InstallDir 'src\voice-agent'
    $venv   = Join-Path $va '.venv'
    $reqTxt = Join-Path $va 'requirements.txt'

    if (-not (Test-Path $reqTxt)) {
        Write-Warn2 "$reqTxt not found -- skipping voice-agent deps"
        return
    }

    if (-not $NoVenv) {
        Resolve-UvCmd
        Invoke-Step -Description "create venv at $venv via uv" -Action {
            if (Test-Path $venv) {
                Write-Info "Virtual environment already exists -- reusing"
            } else {
                # uv creates the venv and pins the Python version in one step.
                # No ensurepip dance, no separate `pip install --upgrade pip`.
                & $UvCmd venv $venv --python $PythonVersion
                Write-Ok "created venv at $venv (Python $PythonVersion)"
            }
        }
    } else {
        Write-Info "Skipping venv creation (-NoVenv)"
    }

    if (-not $DryRun) {
        $prevEAP = $ErrorActionPreference
        try {
            # Relax EAP=Stop while running uv pip install. uv writes
            # download + resolve progress to stderr; under EAP=Stop the
            # 2>&1 merge wraps those as ErrorRecord objects and throws
            # even when the install exits 0. Check $LASTEXITCODE instead.
            $ErrorActionPreference = "Continue"
            if (-not $NoVenv) {
                # Tell uv to install into our venv (no activation needed).
                $env:VIRTUAL_ENV = $venv
                $env:UV_PROJECT_ENVIRONMENT = $venv
            }
            Write-Info "Installing voice-agent dependencies via uv..."
            & $UvCmd pip install --requirement $reqTxt
            $exitCode = $LASTEXITCODE
            $ErrorActionPreference = $prevEAP
            if ($exitCode -ne 0) {
                throw "uv pip install -r requirements.txt failed (exit $exitCode)"
            }
            Write-Ok "voice-agent deps installed"
        } catch {
            if ($prevEAP) { $ErrorActionPreference = $prevEAP }
            throw
        }
    }

    Install-PlaywrightChromium -VenvDir $venv
    # nssm + service registration are independent stages in the install
    # protocol (Stage-Nssm + Stage-VoiceServices). The legacy direct call
    # path also invokes them here so `Install-VoiceAgent` remains a
    # complete, self-contained one-shot (e.g. for recovery via
    # `. install.ps1; Install-VoiceAgent`). Both `Install-Nssm` and
    # `Install-WindowsVoiceServices` are idempotent -- running them twice
    # via the stage protocol re-applies parameters but doesn't break
    # anything.
    Install-Nssm | Out-Null
    Install-WindowsVoiceServices
}

function Install-PlaywrightChromium {
    # Playwright Chromium is needed for the browser_task tool's CDP
    # fallback. ~200MB. Skip with -SkipCdp.
    param([Parameter(Mandatory)][string]$VenvDir)
    if ($SkipCdp) {
        Write-Warn2 "skipping Playwright Chromium (-SkipCdp) -- browser CDP fallback won't work"
        return
    }
    # Playwright's Windows cache lives under %LOCALAPPDATA%\ms-playwright.
    $cacheDir = Join-Path $env:LOCALAPPDATA 'ms-playwright'
    if ((Test-Path $cacheDir) -and ((Get-ChildItem -Path $cacheDir -Filter 'chromium-*' -ErrorAction SilentlyContinue).Count -gt 0)) {
        Write-Ok "Playwright Chromium already cached"
        return
    }

    Write-Sub "About to download ~200MB of Chromium for browser CDP fallback"
    Write-Sub "(re-run with -SkipCdp to skip the download)"

    $interactive = [Environment]::UserInteractive -and -not $NonInteractive
    if ($interactive) {
        $reply = Read-Host "  Download Playwright Chromium now? [Y/n]"
        if ($reply -and $reply -notmatch '^[Yy]') {
            Write-Warn2 "skipped -- run 'playwright install chromium' inside the venv later"
            return
        }
    } else {
        Write-Sub "non-interactive shell -- proceeding with download"
    }

    $venvPython = Join-Path $VenvDir 'Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        Write-Warn2 "$venvPython not found -- skipping Playwright install"
        return
    }
    Invoke-Step -Description "$venvPython -m playwright install chromium" -Action {
        & $venvPython -m playwright install chromium
        Write-Ok "Playwright Chromium installed"
    }
}

# ────────────────────────────────────────────────────────────────────────────
# Windows voice-service install (Phase 3.3, 2026-05-24).
# ────────────────────────────────────────────────────────────────────────────
# Mirror of install.sh's install_systemd_units: register the voice-agent +
# voice-client to start on login (here, as Windows services via nssm). Phase
# 3.1 added pipeline/service_control.py's Windows backend that calls into nssm
# at runtime — this stage downloads + pins nssm.exe at the path that backend
# expects ($env:LOCALAPPDATA\jarvis\bin\nssm.exe) and registers the two
# services with the same env flags the Linux systemd drop-in carries.
#
# Admin elevation is required to register a Windows service. We check up
# front and surface a clear "re-run elevated" hint if not — CLI + Desktop UI
# install continues either way (the dep install above already succeeded).

# Pinned nssm 2.24 (canonical Aug-2014 release from nssm.cc, 351,793 bytes).
# SHA256 is verified after download — supply-chain tamper-evident. Hash
# cross-checked against the archived copy at web.archive.org (snapshot
# 2024-01-17). Mirror URL uses the `if_` (identity) flag to bypass the
# wayback rewriting layer, otherwise Invoke-WebRequest gets HTML.
$script:NssmZipUrl       = "https://nssm.cc/release/nssm-2.24.zip"
$script:NssmZipUrlMirror = "https://web.archive.org/web/2024if_/https://nssm.cc/release/nssm-2.24.zip"
$script:NssmZipSha256    = "727d1e42275c605e0f04aba98095c38a8e1e46def453cdffce42869428aa6743"

function Install-Nssm {
    # Idempotent: returns the existing nssm.exe path if it's already on disk
    # at $env:LOCALAPPDATA\jarvis\bin\nssm.exe — the same path Phase 3.1's
    # pipeline/service_control.py::_locate_nssm probes first.
    $nssmDir  = Join-Path $JarvisHome 'bin'
    $nssmPath = Join-Path $nssmDir 'nssm.exe'

    if (Test-Path $nssmPath) {
        Write-Ok "nssm.exe already present at $nssmPath -- skipping download"
        return $nssmPath
    }

    if (-not (Test-Path $nssmDir)) {
        New-Item -ItemType Directory -Force -Path $nssmDir | Out-Null
    }

    # $env:TEMP is always set on Windows; fall back to .NET's temp path on
    # non-Windows pwsh (Linux/CI syntax-check) so the function stays callable
    # for static probes even though real install only happens on Windows.
    $tempRoot = if ($env:TEMP) { $env:TEMP } else { [System.IO.Path]::GetTempPath() }
    $tempZip = Join-Path $tempRoot 'jarvis-nssm-2.24.zip'
    Write-Info "Downloading nssm 2.24 from nssm.cc..."

    # Try the canonical URL first; fall back to the web.archive.org mirror.
    # nssm.cc occasionally 503s (under-provisioned origin); the archive copy
    # is byte-identical (SHA verified below either way).
    $downloaded = $false
    foreach ($url in @($NssmZipUrl, $NssmZipUrlMirror)) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $tempZip -ErrorAction Stop
            $downloaded = $true
            if ($url -ne $NssmZipUrl) {
                Write-Warn2 "nssm.cc unreachable; fetched from archive mirror"
            }
            break
        } catch {
            Write-Warn2 "Download from $url failed: $($_.Exception.Message)"
        }
    }
    if (-not $downloaded) {
        throw "nssm.exe download failed from all mirrors. Install manually from https://nssm.cc/download into $nssmPath, then re-run."
    }

    $actualSha = (Get-FileHash -Path $tempZip -Algorithm SHA256).Hash.ToLower()
    if ($actualSha -ne $NssmZipSha256) {
        Remove-Item $tempZip -Force -ErrorAction SilentlyContinue
        throw "nssm.exe SHA256 mismatch -- expected $NssmZipSha256, got $actualSha. Refusing to install."
    }
    Write-Ok "nssm-2.24.zip SHA256 verified"

    $tempExtract = Join-Path $tempRoot 'jarvis-nssm-2.24-extracted'
    if (Test-Path $tempExtract) { Remove-Item $tempExtract -Recurse -Force }
    Expand-Archive -Path $tempZip -DestinationPath $tempExtract -Force

    # nssm-2.24.zip layout: nssm-2.24\win64\nssm.exe + nssm-2.24\win32\nssm.exe
    $arch = if ([Environment]::Is64BitOperatingSystem) { 'win64' } else { 'win32' }
    $extractedExe = Join-Path $tempExtract "nssm-2.24\$arch\nssm.exe"
    if (-not (Test-Path $extractedExe)) {
        throw "nssm.exe not found in archive at expected path $extractedExe"
    }

    Copy-Item -Path $extractedExe -Destination $nssmPath -Force

    Remove-Item $tempZip -Force -ErrorAction SilentlyContinue
    Remove-Item $tempExtract -Recurse -Force -ErrorAction SilentlyContinue

    Write-Ok "nssm.exe installed at $nssmPath"
    return $nssmPath
}

function Test-IsAdministrator {
    # PS 5.1-compatible elevation check. Returns $true when running in an
    # elevated PowerShell, $false otherwise. Non-Windows hosts (Linux pwsh
    # for syntax-check / CI) return $false safely.
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object Security.Principal.WindowsPrincipal($id)
        return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

function Register-NssmService {
    # Idempotent nssm-based service registration. If the service exists,
    # parameters are re-applied (no install command). Mirrors the
    # WorkingDirectory / Environment / Restart / StandardOutput directives
    # from setup/systemd/jarvis-voice-*.service.
    param(
        [Parameter(Mandatory=$true)][string]$Nssm,
        [Parameter(Mandatory=$true)][string]$Name,
        [Parameter(Mandatory=$true)][string]$Exe,
        [Parameter(Mandatory=$true)][string]$Arguments,
        [Parameter(Mandatory=$true)][string]$WorkingDir,
        [Parameter(Mandatory=$true)][string]$StdoutLog,
        [Parameter(Mandatory=$true)][string]$StderrLog,
        [hashtable]$EnvVars = @{}
    )

    # `nssm status` exit code is non-zero when the service doesn't exist.
    # Probe + capture both pipelines so the user-facing log stays clean.
    & $Nssm status $Name 2>&1 | Out-Null
    $alreadyInstalled = ($LASTEXITCODE -eq 0)

    if ($alreadyInstalled) {
        Write-Info "Service '$Name' already exists -- re-applying parameters"
        & $Nssm set $Name Application $Exe       | Out-Null
        & $Nssm set $Name AppParameters $Arguments | Out-Null
    } else {
        Write-Info "Installing service '$Name'"
        & $Nssm install $Name $Exe $Arguments | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "nssm install $Name failed (exit $LASTEXITCODE)"
        }
    }

    & $Nssm set $Name AppDirectory $WorkingDir | Out-Null
    & $Nssm set $Name AppStdout $StdoutLog | Out-Null
    & $Nssm set $Name AppStderr $StderrLog | Out-Null
    # 4 = OPEN_ALWAYS: create-if-missing, append otherwise.
    & $Nssm set $Name AppStdoutCreationDisposition 4 | Out-Null
    & $Nssm set $Name AppStderrCreationDisposition 4 | Out-Null
    & $Nssm set $Name AppRotateFiles 1 | Out-Null
    & $Nssm set $Name AppRotateBytes 10485760 | Out-Null   # 10 MB
    & $Nssm set $Name Start SERVICE_AUTO_START | Out-Null
    & $Nssm set $Name AppExit Default Restart | Out-Null
    & $Nssm set $Name AppRestartDelay 5000 | Out-Null      # 5 s, matches RestartSec=5

    if ($EnvVars.Count -gt 0) {
        # nssm accepts AppEnvironmentExtra as a single multi-line string of
        # KEY=VALUE entries. Build it once and apply atomically -- repeated
        # `set AppEnvironmentExtra` overwrites rather than appends.
        $envLines = foreach ($key in $EnvVars.Keys) { "$key=$($EnvVars[$key])" }
        & $Nssm set $Name AppEnvironmentExtra ($envLines -join "`n") | Out-Null
    }

    Write-Ok "Service '$Name' configured"
}

function Install-WindowsVoiceServices {
    # Mirrors install.sh's install_systemd_units. Idempotent: re-running
    # updates parameters in place (Register-NssmService probes nssm status
    # before deciding install vs set).

    if (-not (Test-IsAdministrator)) {
        Write-Warn2 "Voice-service registration requires administrator privileges."
        Write-Warn2 "  CLI + Desktop UI install continues; voice services are NOT registered."
        Write-Warn2 "  To register: open an elevated PowerShell (Run as Administrator) and re-run:"
        Write-Warn2 "    iex (irm https://raw.githubusercontent.com/ulrichando/jarvis/master/install.ps1)"
        Write-Warn2 "  Or, from a local checkout:"
        Write-Warn2 "    Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-File','$PSCommandPath'"
        # Soft-skip signal: surfaces as {ok:true, skipped:true, reason:"..."} in
        # the stage-protocol JSON frame so programmatic drivers can decide to
        # re-launch elevated, surface a prompt, or continue without voice.
        $script:_StageSkippedReason = "elevation required (not running as administrator)"
        return
    }

    $nssm = Install-Nssm

    $venvPython       = Join-Path $InstallDir 'src\voice-agent\.venv\Scripts\python.exe'
    $voiceAgentScript = Join-Path $InstallDir 'src\voice-agent\jarvis_agent.py'
    $voiceClientScript= Join-Path $InstallDir 'src\voice-agent\jarvis_voice_client.py'
    $voiceAgentDir    = Join-Path $InstallDir 'src\voice-agent'

    foreach ($p in @($venvPython, $voiceAgentScript, $voiceClientScript)) {
        if (-not (Test-Path $p)) {
            Write-Warn2 "Required path missing: $p -- skipping service registration"
            return
        }
    }

    # Log destination matches the Linux unit's StandardOutput= location
    # (under the user data dir, separate per-service file).
    $voiceLogDir = Join-Path $ConfigDir 'logs'
    if (-not (Test-Path $voiceLogDir)) {
        # Mirrors Linux: $HOME/.local/share/jarvis/logs. On Windows we keep
        # logs under $ConfigDir (= $env:USERPROFILE\.jarvis\logs) so they
        # live alongside memory + conversation DBs, not under %LOCALAPPDATA%.
        New-Item -ItemType Directory -Force -Path $voiceLogDir | Out-Null
    }

    Register-NssmService -Nssm $nssm -Name 'jarvis-voice-agent' `
        -Exe $venvPython -Arguments "`"$voiceAgentScript`" start" `
        -WorkingDir $voiceAgentDir `
        -StdoutLog (Join-Path $voiceLogDir 'voice-agent.log') `
        -StderrLog (Join-Path $voiceLogDir 'voice-agent.log') `
        -EnvVars @{
            'JARVIS_HOME'      = $ConfigDir
            'PYTHONUNBUFFERED' = '1'
        }

    # voice-client carries the AEC env flags from
    # ~/.config/systemd/user/jarvis-voice-client.service.d/override.conf
    # (NEURAL_AEC=1, MIC_DURING_SPEAK=1, latency budget 15 ms).
    Register-NssmService -Nssm $nssm -Name 'jarvis-voice-client' `
        -Exe $venvPython -Arguments "`"$voiceClientScript`"" `
        -WorkingDir $voiceAgentDir `
        -StdoutLog (Join-Path $voiceLogDir 'voice-client.log') `
        -StderrLog (Join-Path $voiceLogDir 'voice-client.log') `
        -EnvVars @{
            'JARVIS_HOME'                        = $ConfigDir
            'JARVIS_NEURAL_AEC'                  = '1'
            'JARVIS_MIC_DURING_SPEAK'            = '1'
            'JARVIS_NEURAL_AEC_LATENCY_BUDGET_MS'= '15'
            'PYTHONUNBUFFERED'                   = '1'
        }

    Write-Ok "Voice services registered (jarvis-voice-agent + jarvis-voice-client)."
    Write-Sub "Both services are configured Auto-Start; they will launch on next boot."
    if ($StartServices) {
        Write-Info "Starting voice services now (-StartServices)..."
        Start-Service jarvis-voice-agent -ErrorAction SilentlyContinue
        Start-Service jarvis-voice-client -ErrorAction SilentlyContinue
        Write-Sub "  Check status: Get-Service jarvis-voice-*"
    } else {
        Write-Sub "Configure $InstallDir\.env first, then start with:"
        Write-Sub "  Start-Service jarvis-voice-agent"
        Write-Sub "  Start-Service jarvis-voice-client"
        Write-Sub "(or pass -StartServices on the next install run to auto-start)"
    }
}

# Bubblewrap — Linux-only user-namespace sandbox; SKIPPED on Windows.
# The Windows equivalent (AppContainer / Sandbox / Win32 Job Objects)
# is a Phase 2 design item. Until then, when JARVIS's bash tool is
# invoked from a Windows process it should fall back to unsandboxed
# cmd.exe / pwsh.exe — also Phase 2 (cross-platform bash shim).
function Install-BashSandbox {
    Write-Sub "bubblewrap (bash-tool user-namespace sandbox): SKIPPED on Windows"
    Write-Sub "  The bash tool relies on Linux user namespaces. On Windows the equivalent"
    Write-Sub "  story (AppContainer / Sandbox / Win32 Job Objects) is a Phase 2 design item."
    Write-Sub "  Until then, when the bash tool is invoked from a Windows JARVIS process"
    Write-Sub "  it should fall back to an unsandboxed cmd.exe / pwsh.exe (Phase 2)."
}

# ============================================================================
# CLI + Web (Bun) + Desktop (Tauri)
# ============================================================================

function Install-Cli {
    if ($SkipCli) { Write-Warn2 "skipping CLI (-SkipCli)"; return }
    if (-not $script:HasBun) { Write-Warn2 "skipping CLI (Bun not available)"; return }
    Write-Section "Installing CLI"

    $cliDir = Join-Path $InstallDir 'src\cli'
    if (-not (Test-Path $cliDir)) { Write-Warn2 "$cliDir not found -- skipping CLI"; return }

    Invoke-Step -Description "bun install in $cliDir" -Action {
        Push-Location $cliDir
        try { & bun install --silent } finally { Pop-Location }
        Write-Ok "CLI dependencies installed"
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

    Invoke-Step -Description "adding $LocalBin to the user PATH if missing" -Action {
        if (Add-ToUserPath -NewEntry $LocalBin) {
            Write-Ok "added $LocalBin to user PATH (open a NEW shell to pick it up)"
        } else {
            Write-Ok "$LocalBin already on user PATH"
        }
    }
}

function Install-Web {
    if ($SkipWeb) { Write-Warn2 "skipping Web (-SkipWeb)"; return }
    if (-not $script:HasBun) { Write-Warn2 "skipping Web (Bun not available)"; return }
    Write-Section "Installing Web (Next.js)"
    $webDir = Join-Path $InstallDir 'src\web'
    if (-not (Test-Path $webDir)) { Write-Warn2 "$webDir not found -- skipping Web"; return }
    Invoke-Step -Description "bun install in $webDir" -Action {
        Push-Location $webDir
        try { & bun install --silent } finally { Pop-Location }
        Write-Ok "Web dependencies installed -- run 'cd $webDir; bun dev' to start dev server"
    }
}

function Install-Desktop {
    if ($SkipDesktop) { Write-Warn2 "skipping Desktop (-SkipDesktop)"; return }
    if (-not $script:HasNode)  { Write-Warn2 "skipping Desktop (Node.js not available)";  return }
    if (-not $script:HasCargo) { Write-Warn2 "skipping Desktop (cargo not available)";    return }
    Write-Section "Installing Desktop (Tauri) -- first build takes 5-10 min"

    $dt = Join-Path $InstallDir 'src\desktop-tauri'
    if (-not (Test-Path $dt)) { Write-Warn2 "$dt not found -- skipping Desktop"; return }

    Invoke-Step -Description "npm install in $dt" -Action {
        Push-Location $dt
        try { & npm install --silent } finally { Pop-Location }
        Write-Ok "frontend deps installed"
    }

    # CLAUDE.md rule: BOTH `npm run build` and `cargo build --release` are
    # required -- npm run build alone doesn't ship JS changes because
    # Tauri embeds dist/ into the Rust binary at compile time.
    Invoke-Step -Description "npm run build in $dt" -Action {
        Push-Location $dt
        try { & npm run build --silent } finally { Pop-Location }
        Write-Ok "frontend built (dist/)"
    }

    Invoke-Step -Description "cargo build --release in $dt\src-tauri" -Action {
        Push-Location (Join-Path $dt 'src-tauri')
        try { & cargo build --release } finally { Pop-Location }
    }

    # The Cargo package name is jarvis-desktop ([package].name in Cargo.toml).
    # On Windows the binary is jarvis-desktop.exe.
    $bin = Join-Path $dt 'src-tauri\target\release\jarvis-desktop.exe'
    if (Test-Path $bin) {
        $sizeMb = [math]::Round((Get-Item $bin).Length / 1MB, 1)
        Write-Ok "desktop binary at $bin (${sizeMb}MB)"
    } else {
        Write-Warn2 "expected $bin not found -- check $dt\src-tauri\target\release\ for the binary name"
    }

    Install-StartMenuShortcut
}

function Install-StartMenuShortcut {
    $exec    = Join-Path $InstallDir 'src\desktop-tauri\src-tauri\target\release\jarvis-desktop.exe'
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
        # fall back to the rings PNG. Start Menu accepts PNG via .lnk but
        # renders cleaner with .ico.
        if (Test-Path $iconIco) {
            $lnk.IconLocation = $iconIco
        } elseif (Test-Path $iconPng) {
            $lnk.IconLocation = $iconPng
        }
        $lnk.Description = "Voice-first AI assistant (Tauri desktop UI)"
        $lnk.Save()
        Write-Ok "installed Start Menu shortcut: $shortcut"

        if (-not (Test-Path $exec)) {
            Write-Warn2 "Tauri binary not yet built -- launcher will fail until cargo build --release completes"
            Write-Sub  "  Build now: Push-Location '$InstallDir\src\desktop-tauri\src-tauri'; cargo build --release; Pop-Location"
        }
    }
}

# ============================================================================
# Config + secrets — bridge token, .env templates, LiveKit keys
# ============================================================================

function New-BridgeToken {
    # Pre-generate the bridge auth token for first-run UX. The bridge
    # (src/cli/src/bridge/) requires JARVIS_LOCAL_API_TOKEN when
    # JARVIS_REQUIRE_LOCAL_AUTH=1 -- which the desktop launcher sets
    # by default per the 2026-05-16 security review (P0-1).
    $tokenFile = Join-Path $ConfigDir 'local-api-token.env'
    if (Test-Path $tokenFile) {
        Write-Ok "bridge token already exists at $tokenFile"
        return
    }
    Invoke-Step -Description "generate bridge auth token at $tokenFile" -Action {
        if (-not (Test-Path $ConfigDir)) { New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null }

        # Crypto-random URL-safe token. Mirrors the Linux installer's
        # `head -c 32 /dev/urandom | base64 | tr -d '+/=' | head -c 43`.
        # Convert.ToBase64String(32 bytes) yields 44 chars incl. '=' padding;
        # after stripping +/= the result can dip below 43 chars, so the old
        # fixed .Substring(0, 43) threw "Index and length must refer to a
        # location within the string" whenever the random bytes happened to
        # contain enough +/=. Generate extra entropy and accumulate until we
        # have at least 43 url-safe chars, then take exactly 43 (Linux's
        # `head -c 43` likewise never fails on a short stream).
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        $clean = ''
        try {
            while ($clean.Length -lt 43) {
                $bytes = New-Object byte[] 48
                $rng.GetBytes($bytes)
                $clean += ([Convert]::ToBase64String($bytes) -replace '[+/=]', '')
            }
        } finally {
            $rng.Dispose()
        }
        $token = $clean.Substring(0, 43)

        Set-Content -Path $tokenFile -Value "JARVIS_LOCAL_API_TOKEN=$token" -Encoding ASCII
        Set-WindowsAclUserOnly -Path $tokenFile
        Write-Ok "generated bridge auth token at $tokenFile (user-only ACL)"

        # Plumb the token into src/web/.env.local so the Next.js
        # middleware (src/web/src/middleware.ts) bearer check has it
        # at `next start` time even when the desktop launcher hasn't run.
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
    # voice-agent/.env. The agent reads them via systemd EnvironmentFile=
    # on Linux; on Windows they'll be loaded by tools.runtime's env loader.
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

    # No keys yet -- on Linux the installer would shell out to the bundled
    # livekit-server.bin to generate a fresh pair. On Windows the repo's
    # bundled binary is the Linux ELF, so we can't run it. Emit instructions.
    Write-Warn2 "no LIVEKIT_API_KEY/SECRET found in $vaEnv"
    Write-Sub  "Generate a pair manually and add them to voice-agent/.env, then re-run this step."
    Write-Sub  "  - Easiest: under WSL2, run 'src/voice-agent/livekit-server.bin generate-keys'"
    Write-Sub  "  - Or grab livekit-server.exe from https://github.com/livekit/livekit/releases"
    Write-Sub  "  - Then write '<key>: <secret>' to $keys (one line) and re-run install.ps1"
}

# PipeWire / WirePlumber audio profile -- N/A on Windows.
# Windows handles mic/speaker coexistence at the OS level via WASAPI
# shared mode. No userland config needed.
function Install-AudioProfile {
    Write-Sub "PipeWire / WirePlumber auto-profile config: SKIPPED on Windows"
    Write-Sub "  Windows handles mic/speaker coexistence at the OS level (WASAPI shared mode)"
    Write-Sub "  -- no userland config needed. If you hit mic-exclusivity issues, check"
    Write-Sub "  Sound Control Panel > Recording > Properties > Advanced 'Exclusive Mode' toggle."
}

# Echo cancel L1 (PipeWire WebRTC AEC3) -- N/A on Windows.
# L1 is a PipeWire module. On Windows the cascade is:
#   L2 = WebRTC APM in-process (cross-platform -- works today)
#   L3 = DTLN neural cancellation (cross-platform -- works today)
# Both L2 + L3 are exercised by the voice services registered above
# (Stage-VoiceServices). No installer-time action needed.
function Install-EchoCancel {
    Write-Sub "L1 PipeWire WebRTC AEC3 echo-cancel: SKIPPED on Windows"
    Write-Sub "  L1 is a PipeWire module. On Windows the AEC story is:"
    Write-Sub "    L2 = WebRTC APM in-process (cross-platform -- works today)"
    Write-Sub "    L3 = DTLN neural cancellation (cross-platform -- works today)"
    Write-Sub "  Both layers run inside the registered voice services."
    Write-Sub "  No installer-time action needed."
}

function Test-ComputerUseDeps {
    # The computer_use tool currently shells out to xdotool / xdpyinfo /
    # python3-pyatspi on Linux. On Windows the cross-platform stand-ins
    # are mss (screen capture) + pyautogui (click/type/key). Probe + hint;
    # don't fail the install.
    $vaPy = Join-Path $InstallDir 'src\voice-agent\.venv\Scripts\python.exe'
    if (-not (Test-Path $vaPy)) { return }

    Write-Host ''
    Write-Sub "Checking computer_use deps (optional, Windows-style) ..."

    & $vaPy -c 'import mss' 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "mss not installed in voice-agent venv. To enable screen capture:"
        Write-Sub  "  $vaPy -m pip install mss"
    }

    & $vaPy -c 'import pyautogui' 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "pyautogui not installed -- Windows equivalent of xdotool for click/type/key."
        Write-Sub  "  $vaPy -m pip install pyautogui"
        Write-Sub  "  (computer_use's Windows desktop-control backend landed in Phase 3.2;"
        Write-Sub  "  pyautogui + mss complete the picture for screen+click+type on Windows.)"
    }

    Write-Sub "xdotool / xdpyinfo / python3-pyatspi: N/A on Windows (X11-only tools)."
}

function Set-EnvTemplate {
    Write-Section "API key template"
    $envFile = Join-Path $InstallDir '.env'
    if (Test-Path $envFile) {
        Write-Ok ".env already exists; not overwriting"
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
        # Write UTF-8 without BOM. PS5's default Set-Content -Encoding UTF8
        # writes WITH a BOM, which can confuse dotenv parsers on the first
        # line. Use .NET directly with an explicit UTF8Encoding($false) --
        # BOM-free on every PowerShell version.
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($envFile, $template, $utf8NoBom)
        Set-WindowsAclUserOnly -Path $envFile
        Write-Ok "created $envFile (user-only ACL -- fill in your real keys before starting the voice agent)"
    }
}

function Set-JarvisHomeEnv {
    # Persist JARVIS_HOME so tools.runtime's path resolver finds the same
    # config + data dirs that the installer wrote to. The voice-agent
    # already honours this env var on every platform (see
    # src/voice-agent/tools/runtime.py).
    $currentHome = [Environment]::GetEnvironmentVariable("JARVIS_HOME", "User")
    if (-not $currentHome -or $currentHome -ne $ConfigDir) {
        [Environment]::SetEnvironmentVariable("JARVIS_HOME", $ConfigDir, "User")
        $env:JARVIS_HOME = $ConfigDir
        Write-Info "Set JARVIS_HOME=$ConfigDir"
    }
}

function Set-DataDirectories {
    # Pre-create data + logs dirs so the voice-agent and CLI don't have to
    # do a `mkdir -p` dance on first run. Matches the Linux installer's
    # `mkdir -p $HOME/.local/share/jarvis/logs` pattern.
    foreach ($d in @($DataDir, $LogsDir, $ConfigDir, $LocalBin)) {
        if (-not (Test-Path $d)) {
            New-Item -ItemType Directory -Force -Path $d | Out-Null
        }
    }
    Set-JarvisHomeEnv
}

# ============================================================================
# Summary
# ============================================================================

function Write-Summary {
    Write-Section "Done"

    Write-Host ""
    Write-Host "  Install location:  $InstallDir"
    Write-Host "  CLI launcher:      $LocalBin\jarvis.cmd  (also jarvis-desktop.cmd)"
    Write-Host "  Config dir:        $ConfigDir"
    Write-Host "  Data dir:          $DataDir"
    Write-Host "  Logs dir:          $LogsDir"
    Write-Host "  Start Menu:        $StartMenuDir\JARVIS.lnk"
    Write-Host ""
    Write-Host "  Phase 3 status (Windows port):"
    Write-Host "    - CLI:      INSTALLED and runnable today"
    Write-Host "    - Web:      INSTALLED ('cd $InstallDir\src\web; bun dev')"
    Write-Host "    - Desktop:  BUILT -- launch from Start Menu, or run jarvis-desktop.cmd"
    if (Test-IsAdministrator) {
        Write-Host "    - Voice:    REGISTERED (jarvis-voice-agent + jarvis-voice-client via nssm)"
        if ($StartServices) {
            Write-Host "                Services started; check 'Get-Service jarvis-voice-*'."
        } else {
            Write-Host "                Configure .env, then 'Start-Service jarvis-voice-agent'"
            Write-Host "                + 'Start-Service jarvis-voice-client' (or pass -StartServices)."
        }
    } else {
        Write-Host "    - Voice:    DEPS INSTALLED; SERVICES NOT REGISTERED (admin required)"
        Write-Host "                Re-run this installer from an elevated PowerShell to register"
        Write-Host "                jarvis-voice-agent + jarvis-voice-client via nssm."
    }
    Write-Host ""
    Write-Host "  Next steps:"
    Write-Host "    1. Edit $InstallDir\.env and fill in real API keys."
    Write-Host "    2. Open a NEW PowerShell window so PATH picks up $LocalBin."
    Write-Host "    3. Try the CLI:"
    Write-Host "         jarvis"
    Write-Host "    4. Start the web app (optional):"
    Write-Host "         cd $InstallDir\src\web ; bun dev"
    Write-Host "    5. Run the desktop app (Tauri):"
    Write-Host "         Click 'JARVIS' in the Start Menu, or:"
    Write-Host "         $InstallDir\src\desktop-tauri\src-tauri\target\release\jarvis-desktop.exe"
    Write-Host ""
    Write-Host "  Re-run this script anytime to update a channel."
    Write-Host "  Skip channels with -SkipCli / -SkipVoice / -SkipDesktop / -SkipWeb."
    Write-Host ""

    if (-not $script:HasNode) {
        Write-Host "Note: Node.js could not be installed automatically." -ForegroundColor Yellow
        Write-Host "Desktop + Web channels need Node.js. Install manually:" -ForegroundColor Yellow
        Write-Host "  https://nodejs.org/en/download/" -ForegroundColor Yellow
        Write-Host ""
    }
    if (-not $script:HasBun) {
        Write-Host "Note: Bun could not be installed automatically." -ForegroundColor Yellow
        Write-Host "CLI + Web channels need Bun. Install manually:" -ForegroundColor Yellow
        Write-Host "  irm bun.sh/install.ps1 | iex" -ForegroundColor Yellow
        Write-Host ""
    }
    if (-not $script:HasRipgrep) {
        Write-Host "Note: ripgrep (rg) was not installed. For faster file search:" -ForegroundColor Yellow
        Write-Host "  winget install BurntSushi.ripgrep.MSVC" -ForegroundColor Yellow
        Write-Host ""
    }
}

# ============================================================================
# Stage protocol
# ============================================================================
#
# install.ps1 supports a small, stable stage protocol that lets programmatic
# callers (the future Tauri onboarding wizard, CI, install.sh parity) drive
# the install one step at a time and surface progress/errors with their own
# UI. CLI users running the canonical iex/irm one-liner never encounter this
# -- default invocation behaves as before.
#
# Entry points:
#
#   install.ps1                       Interactive install (today's behaviour).
#   install.ps1 -ProtocolVersion      Emit the protocol version integer.
#   install.ps1 -Manifest             Emit the stage manifest as JSON.
#   install.ps1 -Stage <name>         Run one stage and emit its result.
#   install.ps1 -NonInteractive       Disable all Read-Host prompts.
#   install.ps1 -Json                 Emit machine-readable JSON instead of
#                                     the human banner at end of full install.
#
# Manifest schema (-Manifest output):
#
#   {
#     "protocol_version": 1,
#     "stages": [
#       {"name": "uv", "title": "...", "category": "prereqs", "needs_user_input": false},
#       ...
#     ]
#   }
#
# Stage result (-Stage <name> output):
#
#   {"stage": "uv", "ok": true, "skipped": false, "reason": null, "duration_ms": 1234}
#
# Exit codes:
#   0 -- success (stage ran, or stage was deliberately skipped).
#   1 -- generic failure; the stage threw.
#   2 -- unknown stage name passed to -Stage.
#
# Adding a stage:
#   1. Append an entry to $InstallStages below.
#   2. Make sure the worker function is idempotent and respects
#      $NonInteractive when it has prompts.
#   3. Do NOT bump $InstallStageProtocolVersion -- adding stages is additive.
# ============================================================================

$InstallStages = @(
    @{ Name = "uv";               Title = "Installing uv package manager";        Category = "prereqs";      NeedsUserInput = $false; Worker = "Stage-Uv" }
    @{ Name = "python";           Title = "Verifying Python $PythonVersion";      Category = "prereqs";      NeedsUserInput = $false; Worker = "Stage-Python" }
    @{ Name = "git";              Title = "Installing Git";                       Category = "prereqs";      NeedsUserInput = $false; Worker = "Stage-Git" }
    @{ Name = "node";             Title = "Detecting Node.js";                    Category = "prereqs";      NeedsUserInput = $false; Worker = "Stage-Node" }
    @{ Name = "bun";              Title = "Detecting Bun";                        Category = "prereqs";      NeedsUserInput = $false; Worker = "Stage-Bun" }
    @{ Name = "cargo";            Title = "Detecting cargo (Rust)";               Category = "prereqs";      NeedsUserInput = $false; Worker = "Stage-Cargo" }
    @{ Name = "msvc-check";       Title = "Probing MSVC Build Tools";             Category = "prereqs";      NeedsUserInput = $false; Worker = "Stage-MsvcCheck" }
    @{ Name = "system-packages";  Title = "Installing ripgrep and ffmpeg";        Category = "prereqs";      NeedsUserInput = $false; Worker = "Stage-SystemPackages" }
    @{ Name = "data-dirs";        Title = "Creating JARVIS data directories";     Category = "install";      NeedsUserInput = $false; Worker = "Stage-DataDirectories" }
    @{ Name = "repository";       Title = "Cloning JARVIS repository";            Category = "install";      NeedsUserInput = $false; Worker = "Stage-Repository" }
    @{ Name = "voice-agent";      Title = "Installing voice-agent Python deps";   Category = "install";      NeedsUserInput = $false; Worker = "Stage-VoiceAgent" }
    @{ Name = "nssm";             Title = "Downloading nssm.exe (service manager)"; Category = "install";    NeedsUserInput = $false; Worker = "Stage-Nssm" }
    @{ Name = "voice-services";   Title = "Registering voice services via nssm";  Category = "install";      NeedsUserInput = $false; Worker = "Stage-VoiceServices" }
    @{ Name = "cli";              Title = "Installing CLI (Bun)";                 Category = "install";      NeedsUserInput = $false; Worker = "Stage-Cli" }
    @{ Name = "web";              Title = "Installing Web (Next.js)";             Category = "install";      NeedsUserInput = $false; Worker = "Stage-Web" }
    @{ Name = "desktop";          Title = "Building Desktop (Tauri)";             Category = "install";      NeedsUserInput = $false; Worker = "Stage-Desktop" }
    @{ Name = "bash-sandbox";     Title = "bash sandbox (deferred on Windows)";   Category = "finalize";     NeedsUserInput = $false; Worker = "Stage-BashSandbox" }
    @{ Name = "bridge-token";     Title = "Generating bridge auth token";         Category = "finalize";     NeedsUserInput = $false; Worker = "Stage-BridgeToken" }
    @{ Name = "livekit-keys";     Title = "Setting up LiveKit keys";              Category = "finalize";     NeedsUserInput = $false; Worker = "Stage-LiveKitKeys" }
    @{ Name = "computer-use";     Title = "Probing computer_use deps";            Category = "finalize";     NeedsUserInput = $false; Worker = "Stage-ComputerUse" }
    @{ Name = "audio-profile";    Title = "Audio profile (N/A on Windows)";       Category = "finalize";     NeedsUserInput = $false; Worker = "Stage-AudioProfile" }
    @{ Name = "echo-cancel";      Title = "L1 echo-cancel (N/A on Windows)";      Category = "finalize";     NeedsUserInput = $false; Worker = "Stage-EchoCancel" }
    @{ Name = "env-template";     Title = "Writing .env template";                Category = "finalize";     NeedsUserInput = $false; Worker = "Stage-EnvTemplate" }
)

# Stage workers -- thin wrappers around the Install-* / Test-* functions.
# Kept as a separate layer so the existing functions remain callable
# directly (helpful for one-off recovery: ``. install.ps1; Install-VoiceAgent``).
#
# Stages that depend on uv (anything after Stage-Uv) call Resolve-UvCmd
# first so they work in cross-process driver mode where $script:UvCmd
# set by Stage-Uv in a sibling powershell process isn't visible here.
function Stage-Uv               { if (-not (Install-Uv))    { throw "uv installation failed" } }
function Stage-Python           { Resolve-UvCmd; if (-not (Test-Python)) { throw "Python $PythonVersion not available" } }
function Stage-Git              { if (-not (Install-Git))   { throw "Git not available and auto-install failed" } }
function Stage-Node {
    if (-not (Test-Node)) {
        $script:_StageSkippedReason = "Node.js not available; Desktop + Web channels will be unavailable"
    } elseif (-not $script:HasNode) {
        $script:_StageSkippedReason = "Node.js not detected after install attempt"
    }
}
function Stage-Bun {
    [void](Test-Bun)
    if (-not $script:HasBun) {
        $script:_StageSkippedReason = "Bun not available; CLI + Web channels will be unavailable"
    }
}
function Stage-Cargo {
    [void](Test-Cargo)
    if (-not $script:HasCargo) {
        $script:_StageSkippedReason = "cargo not available; Desktop build will be skipped"
    }
}
function Stage-MsvcCheck        { Test-MsvcBuildTools }
function Stage-SystemPackages   { Install-SystemPackages }
function Stage-DataDirectories  { Set-DataDirectories }
function Stage-Repository       { Install-Repository }
function Stage-VoiceAgent       { Resolve-UvCmd; Install-VoiceAgent }
# Stage-Nssm runs as its own stage so programmatic drivers can re-fetch
# nssm without re-running the (~2 min) voice-agent dep install. Idempotent
# -- noop if nssm.exe is already present at the canonical path.
function Stage-Nssm             { Install-Nssm | Out-Null }
# Stage-VoiceServices runs the nssm `install` + `set` calls for both
# voice-agent and voice-client. Re-runnable for parameter updates after
# editing .env / changing the venv path; admin elevation is required at
# call time (clear error printed otherwise -- the stage still succeeds).
function Stage-VoiceServices    { Install-WindowsVoiceServices }
function Stage-Cli              { Install-Cli }
function Stage-Web              { Install-Web }
function Stage-Desktop          { Install-Desktop }
function Stage-BashSandbox      { Install-BashSandbox }
function Stage-BridgeToken      { New-BridgeToken }
function Stage-LiveKitKeys      { Set-LiveKitKeys }
function Stage-ComputerUse      { Test-ComputerUseDeps }
function Stage-AudioProfile     { Install-AudioProfile }
function Stage-EchoCancel       { Install-EchoCancel }
function Stage-EnvTemplate      { Set-EnvTemplate }

function Get-InstallStage {
    param([string]$Name)
    foreach ($s in $InstallStages) {
        if ($s.Name -eq $Name) { return $s }
    }
    return $null
}

function Step-OutOfInstallDir {
    # Windows refuses to delete a directory any shell is currently cd'd
    # inside -- and silently leaves orphan files behind, which then wedge
    # "is this a valid git repo" probes on re-install. Harmless when the
    # caller ran the installer from somewhere else.
    try {
        $currentResolved = (Get-Location).ProviderPath
        $installResolved = $null
        if (Test-Path $InstallDir) {
            $installResolved = (Resolve-Path $InstallDir -ErrorAction SilentlyContinue).ProviderPath
        }
        if ($installResolved -and $currentResolved.ToLower().StartsWith($installResolved.ToLower())) {
            Write-Info "Stepping out of $InstallDir so Windows can replace files there if needed..."
            Set-Location $env:USERPROFILE
        }
    } catch {}
}

function Invoke-Stage {
    param(
        [Parameter(Mandatory=$true)] [hashtable]$StageDef
    )

    # Refresh PATH so this stage sees binaries installed by prior stages,
    # even when each stage runs in its own powershell process.
    Sync-EnvPath

    # Per-stage soft-skip channel. A worker can populate
    # $script:_StageSkippedReason to surface "ran, but the thing it was
    # supposed to set up is not available" as skipped=true in the JSON
    # frame, without throwing. Used by Stage-Node / Stage-Bun / Stage-Cargo
    # so the install flow doesn't abort when an optional capability is
    # missing while still being honest in the protocol contract.
    $script:_StageSkippedReason = $null

    $start = [DateTime]::UtcNow
    $result = @{
        stage        = $StageDef.Name
        ok           = $false
        skipped      = $false
        reason       = $null
        duration_ms  = 0
    }

    try {
        & $StageDef.Worker
        $result.ok = $true
        if ($script:_StageSkippedReason) {
            $result.skipped = $true
            $result.reason  = $script:_StageSkippedReason
        }
    } catch {
        $result.ok = $false
        $result.reason = "$_"
        throw
    } finally {
        $result.duration_ms = [int]([DateTime]::UtcNow - $start).TotalMilliseconds
        if ($Json -or $Stage) {
            $result | ConvertTo-Json -Compress | Write-Output
            if (-not $result.ok) {
                $script:_StageEmittedErrorFrame = $true
            }
        }
    }
}

# ============================================================================
# Main
# ============================================================================

function Invoke-AllStages {
    Step-OutOfInstallDir
    foreach ($s in $InstallStages) {
        Invoke-Stage -StageDef $s
    }
}

function Main {
    Write-Banner
    Get-Invocation
    Invoke-AllStages
    if ($DryRun) {
        Write-Section "Dry-run complete"
        Write-Sub "Detected/chosen install dir: $InstallDir"
        Write-Sub "Re-run without -DryRun to actually install."
        exit 0
    }
    if (-not $Json) {
        Write-Summary
    } else {
        @{ ok = $true; protocol_version = $InstallStageProtocolVersion } | ConvertTo-Json -Compress | Write-Output
    }
}

# ----------------------------------------------------------------------------
# Entry-point dispatch
# ----------------------------------------------------------------------------
# All branches funnel through one try/catch so errors don't kill an iex/irm
# PowerShell session, and failures in stage-driver mode produce a structured
# JSON error frame instead of a bare exception.

try {
    if ($ProtocolVersion) {
        Write-Output $InstallStageProtocolVersion
        exit 0
    }

    if ($Manifest) {
        $payload = @{
            protocol_version = $InstallStageProtocolVersion
            stages = @($InstallStages | ForEach-Object {
                @{
                    name             = $_.Name
                    title            = $_.Title
                    category         = $_.Category
                    needs_user_input = $_.NeedsUserInput
                }
            })
        }
        $payload | ConvertTo-Json -Depth 5 -Compress | Write-Output
        exit 0
    }

    # Use PSBoundParameters rather than $Stage truthiness so an explicit
    # `-Stage ""` from a misbehaving driver doesn't fall through to the
    # full-install Main path and silently kick off a destructive op.
    # Empty string is a contract violation; surface it as unknown-stage
    # exit 2 with a structured JSON frame.
    if ($PSBoundParameters.ContainsKey("Stage")) {
        $def = Get-InstallStage -Name $Stage
        if (-not $def) {
            $err = @{
                ok     = $false
                stage  = $Stage
                reason = "unknown stage: $Stage. Run install.ps1 -Manifest to list valid stages."
            }
            $err | ConvertTo-Json -Compress | Write-Output
            exit 2
        }
        Get-Invocation
        Step-OutOfInstallDir
        Invoke-Stage -StageDef $def
        exit 0
    }

    Main
} catch {
    if ($Json -or $Stage) {
        # Stage-driver mode: emit a structured error frame (unless
        # Invoke-Stage already emitted one for this same failure).
        if (-not $script:_StageEmittedErrorFrame) {
            $err = @{
                ok     = $false
                stage  = if ($Stage) { $Stage } else { $null }
                reason = "$_"
            }
            $err | ConvertTo-Json -Compress | Write-Output
        }
        exit 1
    }

    # Interactive mode: keep today's friendly recovery hint.
    Write-Host ""
    Write-Err "Installation failed: $_"
    Write-Host ""
    Write-Info "If the error is unclear, try downloading and running the script directly:"
    Write-Host "  Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/ulrichando/jarvis/master/install.ps1' -OutFile install.ps1" -ForegroundColor Yellow
    Write-Host "  .\install.ps1" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}
