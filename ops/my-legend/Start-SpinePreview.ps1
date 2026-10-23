param()
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$taskRuntime = Join-Path $taskRoot 'runtime/my-legend-story'
$taskLog = Join-Path $taskRuntime 'logs'
$taskPython = Join-Path $taskRuntime '.venv/Scripts/python.exe'
$taskPg = 'C:\Program Files\PostgreSQL\16\bin\pg_ctl.exe'
# Preview reuses the database and artifacts. No generation workers are started.
if (-not (Get-NetTCPConnection -LocalPort 55439 -State Listen -ErrorAction SilentlyContinue)) {
    if (-not (Test-Path $taskPg)) { throw 'PostgreSQL executable not found; start the existing prototype stack first.' }
    & $taskPg -D (Join-Path $taskRuntime 'postgres-data') -l (Join-Path $taskLog 'postgres.log') -o '-h 127.0.0.1 -p 55439' -t 30 start
    if ($LASTEXITCODE) { throw 'PostgreSQL startup failed' }
}
if (-not (Get-NetTCPConnection -LocalPort 8895 -State Listen -ErrorAction SilentlyContinue)) {
    $taskProtected = ConvertTo-SecureString ([IO.File]::ReadAllText((Join-Path $taskRuntime 'service-secrets.dpapi')).Trim())
    $taskSecrets = [Net.NetworkCredential]::new('', $taskProtected).Password | ConvertFrom-Json
    $taskProtected.Dispose()
    $taskInfo = [Diagnostics.ProcessStartInfo]::new()
    $taskInfo.FileName = $taskPython
    $taskInfo.Arguments = '-m my_legend_production.story_api'
    $taskInfo.WorkingDirectory = $taskRoot
    $taskInfo.UseShellExecute = $false
    $taskInfo.CreateNoWindow = $true
    $taskInfo.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $taskInfo.EnvironmentVariables['MY_LEGEND_WORKSPACE'] = $taskRoot
    $taskInfo.EnvironmentVariables['MY_LEGEND_STORY_RUNTIME'] = Join-Path $taskRuntime 'app'
    $taskInfo.EnvironmentVariables['MY_LEGEND_STORY_TOKEN'] = $taskSecrets.service_token
    $taskInfo.EnvironmentVariables['MY_LEGEND_PRODUCTION_DSN'] = 'host=127.0.0.1 port=55439 dbname=postgres user=my_legend_story'
    $taskInfo.EnvironmentVariables['MY_LEGEND_PRODUCTION_RUNTIME'] = Join-Path $taskRoot 'runtime/my-legend-production/independent'
    $taskInfo.EnvironmentVariables['MY_LEGEND_PRODUCTION_MODULES'] = 'story,sound_plan,audio,visual_plan,visual_render,integrate'
    $taskInfo.EnvironmentVariables['MY_LEGEND_WORKFLOW_PATHS'] = Join-Path $taskRoot 'runtime/my-legend-production/workflows.json'
    $taskInfo.EnvironmentVariables['PGPASSWORD'] = $taskSecrets.db_password
    $taskInfo.EnvironmentVariables['PYTHONUTF8'] = '1'
    try { $taskChild = [Diagnostics.Process]::Start($taskInfo); Write-Output ('Preview API started: ' + $taskChild.Id) }
    finally { $taskInfo.EnvironmentVariables.Remove('PGPASSWORD'); $taskInfo.EnvironmentVariables.Remove('MY_LEGEND_STORY_TOKEN'); $taskSecrets = $null }
}
if (-not (Get-NetTCPConnection -LocalPort 5193 -State Listen -ErrorAction SilentlyContinue)) {
    $taskCli = Join-Path $taskRoot 'source/fingertip-legend-app/node_modules/@dcloudio/vite-plugin-uni/bin/uni.js'
    Start-Process -FilePath (Get-Command node).Source -ArgumentList @(('"'+$taskCli+'"'),'--host','127.0.0.1','--port','5193') -WorkingDirectory (Join-Path $taskRoot 'source/fingertip-legend-app') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskLog 'spine-h5.stdout.log') -RedirectStandardError (Join-Path $taskLog 'spine-h5.stderr.log') | Out-Null
}
Write-Output 'Open http://127.0.0.1:5193/#/pages/performance/index?mode=spine'
