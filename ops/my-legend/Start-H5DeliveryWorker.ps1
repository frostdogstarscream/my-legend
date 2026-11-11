param()
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Startup.Common.ps1')
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$taskRuntime = Join-Path $taskRoot 'runtime/my-legend-story'
$taskPython = Join-Path $taskRuntime '.venv/Scripts/python.exe'
$taskPidPath = Join-Path $taskRuntime 'h5-delivery-worker.pid'
$taskExisting = Resolve-LegendPidFile $taskPidPath $taskPython 'my_legend_production.production.cli worker --only-configured-stages'
if ($taskExisting) {
    Write-Output ('H5 delivery worker already running: ' + $taskExisting.ProcessId)
    exit 0
}
$taskProtected = ConvertTo-SecureString ([IO.File]::ReadAllText((Join-Path $taskRuntime 'service-secrets.dpapi')).Trim())
$taskSecrets = [Net.NetworkCredential]::new('', $taskProtected).Password | ConvertFrom-Json
$taskProtected.Dispose()
$taskInfo = [Diagnostics.ProcessStartInfo]::new()
$taskInfo.FileName = Join-Path $taskRuntime '.venv/Scripts/python.exe'
$taskInfo.Arguments = '-m my_legend_production.production.cli worker --only-configured-stages'
$taskInfo.WorkingDirectory = $taskRoot
$taskInfo.UseShellExecute = $false
$taskInfo.CreateNoWindow = $true
$taskInfo.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
$taskInfo.EnvironmentVariables['MY_LEGEND_WORKSPACE'] = $taskRoot
$taskInfo.EnvironmentVariables['MY_LEGEND_PRODUCTION_DSN'] = 'host=127.0.0.1 port=55439 dbname=postgres user=my_legend_story'
$taskInfo.EnvironmentVariables['MY_LEGEND_PRODUCTION_RUNTIME'] = Join-Path $taskRoot 'runtime/my-legend-production/independent'
$taskInfo.EnvironmentVariables['MY_LEGEND_PRODUCTION_MODULES'] = 'performance_import,performance_package'
$taskInfo.EnvironmentVariables['PGPASSWORD'] = $taskSecrets.db_password
$taskInfo.EnvironmentVariables['PYTHONUTF8'] = '1'
try {
    $taskChild = [Diagnostics.Process]::Start($taskInfo)
    Write-LegendPidFile $taskPidPath $taskChild.Id
    Write-Output ('H5 delivery worker started: ' + $taskChild.Id)
} finally {
    $taskInfo.EnvironmentVariables.Remove('PGPASSWORD')
    $taskSecrets = $null
}
