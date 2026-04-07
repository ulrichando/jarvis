$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

Set-Location $ProjectRoot

$UseDocker = $false
$BackendJob = $null

# Check if Docker is available
try {
    docker info > $null 2>&1
    $UseDocker = $true
} catch {
    $UseDocker = $false
}

function Wait-Backend {
    param([int]$MaxSeconds = 30)
    for ($i = 0; $i -lt $MaxSeconds; $i++) {
        try {
            $response = Invoke-WebRequest -Uri "http://localhost:8765/api/providers" -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
            if ($response.StatusCode -eq 200) {
                return $true
            }
        } catch {
            # not ready yet
        }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Stop-All {
    Write-Host ""
    Write-Host "[JARVIS] Shutting down..."
    if ($UseDocker) {
        Set-Location $ProjectRoot
        docker compose down
    } elseif ($null -ne $BackendJob) {
        Stop-Job -Job $BackendJob -ErrorAction SilentlyContinue
        Remove-Job -Job $BackendJob -ErrorAction SilentlyContinue
    }
}

try {
    if ($UseDocker) {
        Write-Host "[JARVIS] Docker detected — starting backend via docker compose..."
        docker compose up -d
    } else {
        Write-Host "[JARVIS] Docker not available — starting Python server in background..."
        $BackendJob = Start-Job -ScriptBlock {
            Set-Location $using:ProjectRoot
            python -m src.server.web_server
        }
        Write-Host "[JARVIS] Python server started (Job ID: $($BackendJob.Id))"
    }

    Write-Host "[JARVIS] Waiting for backend..."
    $ready = Wait-Backend -MaxSeconds 30
    if (-not $ready) {
        Write-Host "[JARVIS] Warning: Backend did not respond in time, proceeding anyway..."
    } else {
        Write-Host "[JARVIS] Backend ready at http://localhost:8765"
    }

    $CliDir = Join-Path $ProjectRoot "src\cli-ts"
    Set-Location $CliDir

    if (-not (Test-Path "node_modules")) {
        Write-Host "[JARVIS] Installing Node dependencies..."
        npm install
    }

    Write-Host "[JARVIS] Starting CLI..."
    npx tsx src/index.tsx

} finally {
    Stop-All
}
