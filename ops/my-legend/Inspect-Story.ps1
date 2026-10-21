Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$runtime = Join-Path $workspace 'runtime/my-legend-story'
$protected = ConvertTo-SecureString -String ([IO.File]::ReadAllText((Join-Path $runtime 'service-secrets.dpapi')).Trim())
$secrets = [Net.NetworkCredential]::new('', $protected).Password | ConvertFrom-Json
$protected.Dispose()
$info = [Diagnostics.ProcessStartInfo]::new()
$info.FileName = Join-Path $runtime '.venv/Scripts/python.exe'
$info.Arguments = '-X utf8 "' + (Join-Path $PSScriptRoot 'inspect_story.py') + '"'
$info.UseShellExecute = $false
$info.EnvironmentVariables['MY_LEGEND_SERVICE_TOKEN'] = $secrets.service_token
$process = [Diagnostics.Process]::Start($info)
$process.WaitForExit()
$info.EnvironmentVariables.Remove('MY_LEGEND_SERVICE_TOKEN')
exit $process.ExitCode
