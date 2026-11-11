param([switch]$QuietEntry)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/Startup.Common.ps1"
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$taskRuntime = Join-Path $taskRoot 'runtime/my-legend-story'
$taskLog = Join-Path $taskRuntime 'logs'
$taskPython = Join-Path $taskRuntime '.venv/Scripts/python.exe'
$taskPg = 'C:\Program Files\PostgreSQL\16\bin\pg_ctl.exe'
# Preview reuses the database and artifacts. No generation workers are started.
if (-not (Assert-LegendListener 55439 (Join-Path $taskRuntime 'postgres-data') 'C:\Program Files\PostgreSQL\16\bin\postgres.exe')) {
    if (-not (Test-Path $taskPg)) { throw 'PostgreSQL executable not found; start the existing prototype stack first.' }
    New-Item -ItemType Directory -Path $taskLog -Force | Out-Null
    # pg_ctl starts the postmaster as a child; redirect both so a caller's pipe
    # is never held open by a database that outlives the launcher.
    # pg_ctl starts the postmaster as a child and exits once it is ready, but
    # Start-Process -Wait would follow that long-lived child forever. Wait for
    # the listening port instead, and keep both streams in files.
    Start-LegendProcess -FilePath $taskPg -Arguments ('-D "{0}" -l "{1}" -o "-h 127.0.0.1 -p 55439" -t 30 start' -f (Join-Path $taskRuntime 'postgres-data'), (Join-Path $taskLog 'postgres.log')) `
        -WorkingDirectory $taskRoot `
        -StandardOutput (Join-Path $taskLog 'postgres-pgctl.out.log') `
        -StandardError (Join-Path $taskLog 'postgres-pgctl.err.log') | Out-Null
    Wait-LegendPort 55439 'PostgreSQL'
}
if (-not (Assert-LegendListener 8895 'my_legend_production.story_api' $taskPython)) {
    $taskProtected = ConvertTo-SecureString ([IO.File]::ReadAllText((Join-Path $taskRuntime 'service-secrets.dpapi')).Trim())
    $taskSecrets = [Net.NetworkCredential]::new('', $taskProtected).Password | ConvertFrom-Json
    $taskProtected.Dispose()
    $taskInfo = @{
        MY_LEGEND_WORKSPACE = $taskRoot
        MY_LEGEND_STORY_RUNTIME = Join-Path $taskRuntime 'app'
        MY_LEGEND_STORY_TOKEN = $taskSecrets.service_token
        MY_LEGEND_PRODUCTION_DSN = 'host=127.0.0.1 port=55439 dbname=postgres user=my_legend_story'
        MY_LEGEND_PRODUCTION_RUNTIME = Join-Path $taskRoot 'runtime/my-legend-production/independent'
        MY_LEGEND_PRODUCTION_MODULES = 'story,sound_plan,audio,visual_plan,visual_render,integrate,performance_import,performance_package,performance_context,spine_direct,spine_compile'
        MY_LEGEND_WORKFLOW_PATHS = Join-Path $taskRoot 'runtime/my-legend-production/workflows.json'
        PGPASSWORD = $taskSecrets.db_password
        PYTHONUTF8 = '1'
    }
    try {
        $taskChild = Start-LegendProcess -FilePath $taskPython -Arguments '-m my_legend_production.story_api' -WorkingDirectory $taskRoot `
            -Environment $taskInfo `
            -StandardOutput (Join-Path $taskLog 'story-api.out.log') `
            -StandardError (Join-Path $taskLog 'story-api.err.log')
        Write-Output ('Preview API started: ' + $taskChild.Id)
    }
    finally { $taskInfo.Clear(); $taskSecrets = $null }
}
$taskCli = Join-Path $taskRoot 'source/fingertip-legend-app/node_modules/@dcloudio/vite-plugin-uni/bin/uni.js'
if (-not (Assert-LegendListener 5193 $taskCli (Get-Command node).Source)) {
    Start-Process -FilePath (Get-Command node).Source -ArgumentList @(('"'+$taskCli+'"'),'--host','127.0.0.1','--port','5193') -WorkingDirectory (Join-Path $taskRoot 'source/fingertip-legend-app') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskLog 'spine-h5.stdout.log') -RedirectStandardError (Join-Path $taskLog 'spine-h5.stderr.log') | Out-Null
}
Wait-LegendHttp 'http://127.0.0.1:8895/openapi.json' 'Frontend API'
Wait-LegendHttp 'http://127.0.0.1:5193/' 'Frontend page'
if(!$QuietEntry){Write-Output 'Open http://127.0.0.1:5193/#/pages/performance/index?mode=spine'}
