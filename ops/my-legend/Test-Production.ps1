param([string]$TestPath='source/my-legend-production/tests --ignore=source/my-legend-production/tests/test_media_integrity.py', [switch]$ProcessCheck,
    [ValidatePattern('^[A-Za-z0-9_.-]+\.xml$')][string]$ReportName='tests-production.xml')
$ErrorActionPreference = 'Stop'
$taskWorkspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$taskRuntime = Join-Path $taskWorkspace 'runtime/my-legend-story'
$taskProtected = ConvertTo-SecureString -String ([IO.File]::ReadAllText((Join-Path $taskRuntime 'service-secrets.dpapi')).Trim())
$taskSecrets = [Net.NetworkCredential]::new('', $taskProtected).Password | ConvertFrom-Json
$taskProtected.Dispose()
$taskProcess = [Diagnostics.ProcessStartInfo]::new()
$taskProcess.FileName = Join-Path $taskRuntime '.venv/Scripts/python.exe'
$taskTemp = Join-Path $taskRuntime ('test-production-' + [Guid]::NewGuid().ToString('N'))
$taskProcess.Arguments = '-X utf8 -m pytest ' + $TestPath + ' -q --tb=short --basetemp="' + $taskTemp + '" --junitxml="' + (Join-Path $taskRuntime $ReportName) + '"'
if ($ProcessCheck) { $taskProcess.Arguments = '-X utf8 "' + (Join-Path $PSScriptRoot 'verify_orchestrator.py') + '"' }
$taskProcess.WorkingDirectory = $taskWorkspace
$taskProcess.UseShellExecute = $false
$taskProcess.EnvironmentVariables['MY_LEGEND_PRODUCTION_TEST_DSN'] = 'host=127.0.0.1 port=55439 dbname=postgres user=my_legend_story'
$taskProcess.EnvironmentVariables['PGPASSWORD'] = $taskSecrets.db_password
try {
    $taskChild = [Diagnostics.Process]::Start($taskProcess)
    $taskChild.WaitForExit()
    $taskExit = $taskChild.ExitCode
} finally {
    $taskProcess.EnvironmentVariables.Remove('PGPASSWORD')
    $taskSecrets = $null
}
exit $taskExit
