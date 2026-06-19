# jarvis-mode.ps1 — Windows port of bin/jarvis-mode.
#
# Switches the active voice-conversation backend between JARVIS-Claude and the
# two "live" direct modes (Gemini Live / OpenAI Realtime). On Linux those modes
# run as systemd --user transient services launched by bin/jarvis-mode; Windows
# has no systemd, so this script is the equivalent supervisor:
#
#   - loads the LLM keys from ~/.jarvis/keys.env (the tools read them from env),
#   - mutes JARVIS-Claude (+ stops its in-flight TTS) so two voices don't overlap,
#   - launches the direct-mode tool via the voice-agent venv python in a restart
#     loop (mirrors systemd Restart=always — the Live session sends a GoAway and
#     exits cleanly every ~10-15 min, and without a relaunch the mode would
#     silently revert to JARVIS-Claude), with a crash-loop cap, and
#   - on `jarvis` stops the direct mode and unmutes JARVIS-Claude.
#
# Audio I/O is sounddevice (providers/direct_audio.py) honouring the same
# JARVIS_AUDIO_INPUT_DEVICE / _OUTPUT_DEVICE selection (FIFINE in / Echo Studio
# out) as the main voice client.
#
# Usage:  jarvis-mode.ps1 <jarvis|gemini|openai|status>

param([string]$Mode = "status")

$ErrorActionPreference = "SilentlyContinue"
$Mode = $Mode.ToLower()
if ($Mode -eq "gpt") { $Mode = "openai" }

# ── paths ──────────────────────────────────────────────────────────────────
$RepoRoot   = Split-Path -Parent $PSScriptRoot           # bin/ -> repo root
$VenvPy     = Join-Path $RepoRoot "src\voice-agent\.venv\Scripts\python.exe"
$GeminiTool = Join-Path $RepoRoot "bin\jarvis-gemini-tools"
$GptTool    = Join-Path $RepoRoot "bin\jarvis-gpt-tools"
$JarvisDir  = Join-Path $env:USERPROFILE ".jarvis"
$LogDir     = Join-Path $JarvisDir "logs"
$StateFile  = Join-Path $JarvisDir "active-mode"
$SupPidFile = Join-Path $JarvisDir "direct-mode.pid"        # this supervisor's PID
$ChildFile  = Join-Path $JarvisDir "direct-mode-child.pid"  # current python child PID
$StopFile   = Join-Path $JarvisDir "direct-mode.stop"       # stop sentinel
$VoicePort  = if ($env:JARVIS_VOICE_CLIENT_PORT) { $env:JARVIS_VOICE_CLIENT_PORT } else { "8767" }
New-Item -ItemType Directory -Force $LogDir | Out-Null

function Set-Mute([bool]$on) {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$VoicePort/mute" -Method POST `
            -Body (@{ mute = $on } | ConvertTo-Json) -ContentType 'application/json' -TimeoutSec 3 | Out-Null
    } catch {}
}
function Stop-JarvisTts {
    try { Invoke-RestMethod -Uri "http://127.0.0.1:$VoicePort/stop" -Method POST -TimeoutSec 3 | Out-Null } catch {}
}
function Write-ModeState([string]$m) { Set-Content -Path $StateFile -Value $m -NoNewline }

function Load-Keys {
    # Mirror jarvis-mode: keys come from ~/.jarvis/keys.env (then repo .env).
    foreach ($f in @((Join-Path $JarvisDir "keys.env"), (Join-Path $RepoRoot ".env"))) {
        if (Test-Path $f) {
            foreach ($line in Get-Content $f) {
                if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') {
                    $k = $Matches[1]; $v = $Matches[2].Trim().Trim('"').Trim("'")
                    if ($k -match 'API_KEY$' -and -not [string]::IsNullOrWhiteSpace($v)) {
                        Set-Item -Path "Env:$k" -Value $v
                    }
                }
            }
        }
    }
}

function Stop-DirectMode {
    # Tell the supervisor loop not to relaunch, then kill the current child now.
    New-Item -ItemType File -Force $StopFile | Out-Null
    foreach ($pf in @($ChildFile, $SupPidFile)) {
        if (Test-Path $pf) {
            $procId = (Get-Content $pf | Select-Object -First 1)
            if ($procId -match '^\d+$') {
                # kill the process tree (taskkill /T) so the python child + any
                # grandchildren go down with the supervisor.
                & taskkill /PID $procId /T /F 2>$null | Out-Null
            }
            Remove-Item $pf -Force -EA SilentlyContinue
        }
    }
}

# ── dispatch ─────────────────────────────────────────────────────────────────
switch ($Mode) {
    "status" {
        $cur = if (Test-Path $StateFile) { Get-Content $StateFile -Raw } else { "jarvis" }
        Write-Output "active mode: $($cur.Trim())"
        return
    }
    "jarvis" {
        Stop-DirectMode
        Start-Sleep -Milliseconds 300
        Set-Mute $false          # unmute JARVIS-Claude
        Write-ModeState "jarvis"
        Write-Output "switched to JARVIS-Claude (direct mode stopped, mic unmuted)"
        return
    }
    { $_ -in @("gemini", "openai") } {
        if ($Mode -eq "gemini") {
            $tool = $GeminiTool; $voiceVar = "JARVIS_GEMINI_TOOLS_VOICE"; $voiceDef = "Iapetus"
            $statusVar = "JARVIS_GEMINI_STATUS_PORT"; $statusPort = "8768"
        } else {
            $tool = $GptTool;    $voiceVar = "JARVIS_GPT_TOOLS_VOICE";    $voiceDef = "cedar"
            $statusVar = "JARVIS_GPT_STATUS_PORT";    $statusPort = "8769"
        }
        if (-not (Test-Path $VenvPy)) { Write-Error "venv python missing: $VenvPy"; exit 2 }
        if (-not (Test-Path $tool))   { Write-Error "tool missing: $tool"; exit 2 }

        # stop any prior direct mode, then arm a fresh run
        Stop-DirectMode
        Remove-Item $StopFile -Force -EA SilentlyContinue

        Load-Keys
        # env the tools/audio read
        if (-not (Get-Item "Env:$voiceVar" -EA SilentlyContinue)) { Set-Item "Env:$voiceVar" $voiceDef }
        Set-Item "Env:$statusVar" $statusPort
        if (-not $env:JARVIS_DIRECT_IDLE_TIMEOUT_S) { $env:JARVIS_DIRECT_IDLE_TIMEOUT_S = "300" }
        if (-not $env:JARVIS_GEMINI_WEBCAM) { $env:JARVIS_GEMINI_WEBCAM = "0" }
        if (-not $env:JARVIS_OPENAI_WEBCAM) { $env:JARVIS_OPENAI_WEBCAM = "0" }
        # device selection — fall back to the same names the voice launcher uses
        if (-not $env:JARVIS_AUDIO_INPUT_DEVICE)  { $env:JARVIS_AUDIO_INPUT_DEVICE  = "FIFINE" }
        if (-not $env:JARVIS_AUDIO_OUTPUT_DEVICE) { $env:JARVIS_AUDIO_OUTPUT_DEVICE = "Echo Studio" }

        Set-Mute $true; Stop-JarvisTts
        Write-ModeState $Mode

        # become the supervisor: record our PID, then restart-loop the tool
        $PID | Out-File -FilePath $SupPidFile -Encoding ascii
        $log = Join-Path $LogDir "direct-mode-$Mode.log"
        $fails = 0
        while ($true) {
            if (Test-Path $StopFile) { break }
            $proc = Start-Process -FilePath $VenvPy -ArgumentList "-u", "`"$tool`"" `
                -NoNewWindow -PassThru -RedirectStandardOutput $log -RedirectStandardError "$log.err"
            $proc.Id | Out-File -FilePath $ChildFile -Encoding ascii
            $proc.WaitForExit()
            if (Test-Path $StopFile) { break }
            # Live GoAway is a clean exit (0) → relaunch immediately. Non-zero =
            # crash; cap the loop so a bad/expired key doesn't spin forever.
            if ($proc.ExitCode -ne 0) { $fails++ } else { $fails = 0 }
            if ($fails -ge 10) { break }
            Start-Sleep -Seconds 2
        }
        # supervisor exiting — if we stopped because of a crash-loop (not an
        # explicit switch), revert to JARVIS-Claude so the user isn't left muted.
        Remove-Item $ChildFile, $SupPidFile -Force -EA SilentlyContinue
        if (-not (Test-Path $StopFile)) {
            Set-Mute $false
            Write-ModeState "jarvis"
        }
        return
    }
    default {
        Write-Error "unknown mode: $Mode (expected jarvis|gemini|openai|status)"
        exit 2
    }
}
