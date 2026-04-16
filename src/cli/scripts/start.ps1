param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$RemainingArgs
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $scriptDir '..')).Path
$bunResolver = Join-Path $scriptDir 'bunw.ps1'

function Import-DotEnvFile {
  param([string]$Path)

  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    return
  }

  foreach ($line in Get-Content -LiteralPath $Path) {
    if ([string]::IsNullOrWhiteSpace($line)) {
      continue
    }

    $trimmed = $line.Trim()
    if ($trimmed.StartsWith('#')) {
      continue
    }

    if ($trimmed -match '^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
      $name = $matches[1]
      $value = $matches[2].Trim()

      if (
        ($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))
      ) {
        $value = $value.Substring(1, $value.Length - 2)
      }

      [System.Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
  }
}

Import-DotEnvFile -Path (Join-Path $root '.env.local')

$providers = @('deepseek', 'groq', 'openai', 'gemini', 'ollama')
$selectedProvider = $env:JARVIS_PROVIDER
if ([string]::IsNullOrWhiteSpace($selectedProvider)) {
  $selectedProvider = 'deepseek'
}

$forwardArgs = @()
if ($RemainingArgs.Count -gt 0) {
  $firstArg = $RemainingArgs[0].ToLowerInvariant()
  if ($providers -contains $firstArg) {
    $selectedProvider = $firstArg
    if ($RemainingArgs.Count -gt 1) {
      $forwardArgs = $RemainingArgs[1..($RemainingArgs.Count - 1)]
    }
  } else {
    $forwardArgs = $RemainingArgs
  }
}

$jarvisPermissionMode = if ($env:JARVIS_PERMISSION_MODE) { $env:JARVIS_PERMISSION_MODE } else { 'bypassPermissions' }
$jarvisSandboxEnabled = if ($env:JARVIS_SANDBOX_ENABLED) { $env:JARVIS_SANDBOX_ENABLED } else { '0' }
$jarvisFlagSettings = if ($jarvisSandboxEnabled -eq '1') { '{"sandbox":{"enabled":true}}' } else { '{"sandbox":{"enabled":false}}' }

$env:ANTHROPIC_BASE_URL = 'http://localhost:4000'
$env:ANTHROPIC_API_KEY = 'jarvis-proxy'
$env:JARVIS_PROVIDER = $selectedProvider
$env:JARVIS_MODEL_REGISTRY_ENABLED = '1'
$env:JARVIS_DISABLE_AUTH = if ($env:JARVIS_DISABLE_AUTH) { $env:JARVIS_DISABLE_AUTH } else { '1' }
$env:CLAUDE_CODE_MAX_OUTPUT_TOKENS = '8000'
$env:ENABLE_TOOL_SEARCH = 'true'
$env:IS_DEMO = '1'
$env:DISABLE_INSTALLATION_CHECKS = '1'

$bunPath = & $bunResolver -PrintPath
if (-not $bunPath) {
  throw 'Unable to resolve Bun runtime'
}

$tempDir = if ($env:TEMP) { $env:TEMP } elseif ($env:TMP) { $env:TMP } else { $scriptDir }
$proxyStdout = Join-Path $tempDir 'jarvis-proxy.log'
$proxyStderr = Join-Path $tempDir 'jarvis-proxy.err.log'
$proxyProcess = Start-Process -FilePath $bunPath -ArgumentList @((Join-Path $root 'src/proxy/server.ts')) -RedirectStandardOutput $proxyStdout -RedirectStandardError $proxyStderr -PassThru

try {
  for ($i = 0; $i -lt 15; $i++) {
    try {
      Invoke-WebRequest -Uri 'http://localhost:4000/health' -UseBasicParsing -TimeoutSec 2 | Out-Null
      break
    } catch {
      Start-Sleep -Seconds 1
    }
  }

  $cliArgs = @(
    '--define', 'MACRO.VERSION="2.1.107"',
    '--define', 'MACRO.BUILD_TIME=""',
    '--define', 'MACRO.PACKAGE_URL="@anthropic-ai/claude-code"',
    '--define', 'MACRO.NATIVE_PACKAGE_URL="@anthropic-ai/claude-code-native"',
    '--define', 'MACRO.ISSUES_EXPLAINER="report the issue at https://github.com/anthropics/claude-code/issues"',
    '--define', 'MACRO.FEEDBACK_CHANNEL="https://github.com/anthropics/claude-code/issues"',
    '--define', 'MACRO.VERSION_CHANGELOG=null',
    (Join-Path $root 'src/entrypoints/cli.tsx'),
    '--settings', $jarvisFlagSettings,
    '--permission-mode', $jarvisPermissionMode
  )

  & $bunPath @cliArgs @forwardArgs
  exit $LASTEXITCODE
} finally {
  if ($proxyProcess -and -not $proxyProcess.HasExited) {
    Stop-Process -Id $proxyProcess.Id -ErrorAction SilentlyContinue
  }
}
