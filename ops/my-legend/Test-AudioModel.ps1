. "$PSScriptRoot/AudioModel.Common.ps1"
$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$profile = Read-AudioModelProfile
$info = [Diagnostics.ProcessStartInfo]::new()
$info.FileName = Join-Path $workspace 'runtime/my-legend-production/.venv/Scripts/python.exe'
$info.Arguments = '-X utf8 "' + (Join-Path $PSScriptRoot 'probe_audio.py') + '"'
$info.WorkingDirectory = $workspace
$info.UseShellExecute = $false
$info.EnvironmentVariables['DASHSCOPE_API_KEY'] = Read-AudioModelSecret $profile
try {
    $process = [Diagnostics.Process]::Start($info)
    $process.WaitForExit()
    $result = $process.ExitCode
} finally { $info.EnvironmentVariables.Remove('DASHSCOPE_API_KEY') }
exit $result
