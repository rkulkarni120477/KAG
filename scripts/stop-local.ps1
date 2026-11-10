$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$processFile = Join-Path $projectRoot '.runtime/processes.json'
if (-not (Test-Path -LiteralPath $processFile)) { Write-Host 'No recorded Northstar processes.'; exit }
$processes = Get-Content -LiteralPath $processFile -Raw | ConvertFrom-Json
foreach ($processId in @($processes.source,$processes.api,$processes.web)) {
    if (-not $processId) { continue }
    $owned = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    # Verify process arguments before stopping; a recorded PID may have been reused.
    if ($owned -and ($owned.CommandLine -like '*uvicorn*app.main:app*' -or $owned.CommandLine -like '*uvicorn*app.source.main:app*' -or $owned.CommandLine -like '*next*dev*--hostname*127.0.0.1*')) {
        Stop-Process -Id $processId -ErrorAction SilentlyContinue
    }
}
Write-Host 'Stopped the recorded Northstar launcher processes. Check ports if a Node child remains.'
