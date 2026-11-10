param([switch]$SkipInstall)
# Local development without Docker. Needs a reachable Keycloak with the education realm on
# localhost:8080 (see deployment/README.md, "Keycloak only"). Use deployment/compose.yaml for the full stack.
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
foreach ($port in @(3000,8000,8001)) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    if ($listener) { throw "Port $port is already in use. Stop its application or use the existing Northstar instance." }
}
try { Invoke-WebRequest -Uri 'http://localhost:8080/auth/realms/education/.well-known/openid-configuration' -UseBasicParsing -TimeoutSec 3 | Out-Null }
catch { throw 'Keycloak is not reachable at http://localhost:8080. Start it with the education realm first (deployment/README.md).' }

# Development configuration. The source token is generated per launch and stays in this process tree.
$sourceToken = [Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 }))
$env:SOURCE_SERVICE_TOKEN = $sourceToken
$env:SOURCE_URL = 'http://127.0.0.1:8001'
$env:KAG_DATA_DIR = Join-Path $projectRoot 'backend/data'
$env:KAG_QUEUE_MODE = 'inline'
$env:KAG_VECTOR_BACKEND = 'sqlite'
$env:KAG_GRAPH_BACKEND = 'sqlite'
$env:OIDC_ISSUER = 'https://localhost:8443/auth/realms/education'
$env:OIDC_JWKS_URL = 'http://localhost:8080/auth/realms/education/protocol/openid-connect/certs'
$env:OIDC_TOKEN_URL = 'http://localhost:8080/auth/realms/education/protocol/openid-connect/token'
$env:OIDC_AUDIENCE = 'kag-api'
$env:OIDC_CLIENT_ID = 'kag-web'
$env:WEB_SEARCH_ENABLED = 'false'
$env:KAG_LOAD_DOTENV = 'false'
$env:KAG_RATE_LIMIT_API = '600'

$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
$backend = Join-Path $projectRoot 'backend'
$source = Start-Process -FilePath $pythonPath -ArgumentList '-m','uvicorn','app.source.main:app','--host','127.0.0.1','--port','8001','--no-access-log' -WorkingDirectory $backend -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory 'source.log') -RedirectStandardError (Join-Path $logDirectory 'source-error.log') -PassThru
$api = Start-Process -FilePath $pythonPath -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000','--no-access-log' -WorkingDirectory $backend -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory 'api.log') -RedirectStandardError (Join-Path $logDirectory 'api-error.log') -PassThru
$nodePath = (Get-Command node).Source
$web = Start-Process -FilePath $nodePath -ArgumentList 'node_modules/next/dist/bin/next','dev','--hostname','127.0.0.1','--port','3000' -WorkingDirectory (Join-Path $projectRoot 'frontend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory 'web.log') -RedirectStandardError (Join-Path $logDirectory 'web-error.log') -PassThru
@{ source = $source.Id; api = $api.Id; web = $web.Id } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logDirectory 'processes.json')
$running = @($source.Id, $api.Id, $web.Id)
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
