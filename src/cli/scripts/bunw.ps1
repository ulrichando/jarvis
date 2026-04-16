param(
  [switch]$PrintPath,
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$CommandArgs
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $scriptDir '..')).Path

function Get-NormalizedOs {
  switch ($env:OS) {
    'Windows_NT' { return 'windows' }
    default {
      if ($IsMacOS) { return 'darwin' }
      if ($IsLinux) { return 'linux' }
      return $null
    }
  }
}

function Get-NormalizedArch {
  switch ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture) {
    'X64' { return 'x64' }
    'Arm64' { return 'arm64' }
    default { return $null }
  }
}

function Test-ExecutablePath {
  param([string]$Path)

  return [string]::IsNullOrWhiteSpace($Path) -eq $false -and (Test-Path -LiteralPath $Path -PathType Leaf)
}

function Resolve-BunPath {
  if (Test-ExecutablePath $env:BUN_BIN) {
    return $env:BUN_BIN
  }

  $os = Get-NormalizedOs
  $arch = Get-NormalizedArch
  $candidates = New-Object System.Collections.Generic.List[string]
  $binaryName = if ($os -eq 'windows') { 'bun.exe' } else { 'bun' }

  if ($os -and $arch) {
    $candidates.Add((Join-Path $root "vendor/bun/$os-$arch/$binaryName"))
  }

  $candidates.Add((Join-Path $root "vendor/bun/bin/$binaryName"))
  $candidates.Add((Join-Path $root "tools/bun/bin/$binaryName"))
  $candidates.Add((Join-Path $root ".bun/bin/$binaryName"))

  if ($env:USERPROFILE) {
    $candidates.Add((Join-Path $env:USERPROFILE ".bun/bin/$binaryName"))
  }

  if ($env:HOME) {
    $candidates.Add((Join-Path $env:HOME ".bun/bin/$binaryName"))
  }

  if ($os -eq 'windows') {
    $candidates.Add('C:\Program Files\Bun\bun.exe')
    $candidates.Add('C:\Program Files (x86)\Bun\bun.exe')
  } else {
    $candidates.Add('/usr/local/bin/bun')
    $candidates.Add('/opt/homebrew/bin/bun')
    $candidates.Add('/usr/bin/bun')
  }

  foreach ($candidate in $candidates) {
    if (Test-ExecutablePath $candidate) {
      return $candidate
    }
  }

  $fromPath = Get-Command $binaryName -ErrorAction SilentlyContinue
  if ($fromPath -and (Test-ExecutablePath $fromPath.Source)) {
    return $fromPath.Source
  }

  return $null
}

$bunPath = Resolve-BunPath
if (-not $bunPath) {
  Write-Error "Bun was not found. Provide BUN_BIN, add bun to PATH, or bundle vendor/bun/<os>-<arch>/bun(.exe)."
}

if ($PrintPath) {
  Write-Output $bunPath
  exit 0
}

& $bunPath @CommandArgs
exit $LASTEXITCODE
