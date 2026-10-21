param([Parameter(Mandatory=$true)][string]$RequestPath,[Parameter(Mandatory=$true)][string]$Root,[switch]$IndependentAudio)
. "$PSScriptRoot/AudioModel.Common.ps1"
$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$profile = Read-AudioModelProfile
$info = [Diagnostics.ProcessStartInfo]::new()
$info.FileName = Join-Path $workspace 'runtime/my-legend-production/.venv/Scripts/python.exe'
$taskModule = if ($IndependentAudio) { 'my_legend_production.production.audio_job' } else { 'my_legend_production.performance_production' }
$info.Arguments = '-X utf8 -m ' + $taskModule + ' --request "' + [IO.Path]::GetFullPath($RequestPath) + '" --root "' + [IO.Path]::GetFullPath($Root) + '" --paths "' + (Join-Path $workspace 'runtime/my-legend-production/workflows.json') + '"'
$info.WorkingDirectory = $workspace
$info.UseShellExecute = $false
$info.EnvironmentVariables['DASHSCOPE_API_KEY'] = Read-AudioModelSecret $profile
$info.EnvironmentVariables['LANGSMITH_TRACING'] = 'false'
$info.EnvironmentVariables['PYTHONUTF8'] = '1'
foreach ($taskSecretName in @('PGPASSWORD','MY_LEGEND_DB_PASSWORD','MY_LEGEND_PRODUCTION_DSN','MY_LEGEND_STORY_TOKEN','MY_LEGEND_SERVICE_TOKEN','DRAMA_LLM_API_KEY')) { $info.EnvironmentVariables.Remove($taskSecretName) }
try {
    $process = [Diagnostics.Process]::Start($info)
    $process.WaitForExit()
    $result = $process.ExitCode
} finally { $info.EnvironmentVariables.Remove('DASHSCOPE_API_KEY') }
exit $result
