param()
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Startup.Common.ps1')
. (Join-Path $PSScriptRoot 'H5DirectingModel.Common.ps1')
$taskModelDirectory = Get-H5DirectingModelDirectory
if (Test-Path -LiteralPath (Join-Path $taskModelDirectory 'profile.json')) {
    $taskModelProfile = Read-H5DirectingModelProfile $taskModelDirectory
} else {
    $taskModelDirectory = Get-StoryModelDirectory
    $taskModelProfile = Read-StoryModelProfile $taskModelDirectory
}
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$taskRuntime = Join-Path $taskRoot 'runtime/my-legend-story'
$taskPython = Join-Path $taskRuntime '.venv/Scripts/python.exe'
$taskPidPath = Join-Path $taskRuntime 'h5-directing-worker.pid'
$taskExisting = Resolve-LegendPidFile $taskPidPath $taskPython 'my_legend_production.production.cli worker --only-configured-stages'
if ($taskExisting) {
    Write-Output ('H5 directing worker already running: ' + $taskExisting.ProcessId)
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
$taskInfo.EnvironmentVariables['MY_LEGEND_PRODUCTION_MODULES'] = 'performance_context,spine_direct,spine_compile,performance_package'
$taskInfo.EnvironmentVariables['PGPASSWORD'] = $taskSecrets.db_password
$taskInfo.EnvironmentVariables['PYTHONUTF8'] = '1'
$taskInfo.EnvironmentVariables['MY_LEGEND_VISUAL_MODEL_BASE_URL'] = $taskModelProfile.base_url
$taskInfo.EnvironmentVariables['MY_LEGEND_VISUAL_MODEL_ID'] = $taskModelProfile.model
$taskInfo.EnvironmentVariables['MY_LEGEND_VISUAL_MODEL_KEY'] = Read-StoryModelSecret $taskModelDirectory $taskModelProfile
try {
    $taskChild = [Diagnostics.Process]::Start($taskInfo)
    Write-LegendPidFile $taskPidPath $taskChild.Id
    Write-Output ('H5 directing worker started: ' + $taskChild.Id)
} finally {
    $taskInfo.EnvironmentVariables.Remove('PGPASSWORD')
    $taskInfo.EnvironmentVariables.Remove('MY_LEGEND_VISUAL_MODEL_KEY')
    $taskSecrets = $null
}
