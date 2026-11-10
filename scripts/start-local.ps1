param([switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create Python environment' }
}
if (-not $SkipInstall) {
    & '.venv/Scripts/python.exe' -m pip install -r backend/requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed' }
    Push-Location -LiteralPath frontend
    try { npm ci --no-fund --no-audit; if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' } }
    finally { Pop-Location }
}
$logDirectory = Join-Path $projectRoot '.runtime'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$running = @()
foreach ($port in @(3000,8000)) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    if ($listener) { throw "Port $port is already in use. Stop its application or use the existing Northstar instance." }
}
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
$api = Start-Process -FilePath $pythonPath -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000','--no-access-log' -WorkingDirectory (Join-Path $projectRoot 'backend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory 'api.log') -RedirectStandardError (Join-Path $logDirectory 'api-error.log') -PassThru
$running += $api.Id
$nodePath = (Get-Command node).Source
$web = Start-Process -FilePath $nodePath -ArgumentList 'node_modules/next/dist/bin/next','dev','--hostname','127.0.0.1','--port','3000' -WorkingDirectory (Join-Path $projectRoot 'frontend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory 'web.log') -RedirectStandardError (Join-Path $logDirectory 'web-error.log') -PassThru
$running += $web.Id
@{ api = $api.Id; web = $web.Id } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logDirectory 'processes.json')
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/v1/health' -TimeoutSec 2
            $page = Invoke-WebRequest -Uri 'http://127.0.0.1:3000' -UseBasicParsing -TimeoutSec 3
            if ($health.status -eq 'ok' -and $page.StatusCode -eq 200) { $ready = $true; break }
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw "Application did not become ready. Check logs in $logDirectory" }
    Write-Host 'Northstar is running at http://localhost:3000'
    Write-Host 'API documentation: http://localhost:8000/docs'
    Write-Host 'Stop with: powershell -File scripts/stop-local.ps1'
} catch {
    foreach ($processId in $running) { Stop-Process -Id $processId -ErrorAction SilentlyContinue }
    throw
}
