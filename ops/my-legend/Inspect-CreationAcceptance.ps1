param([Parameter(Mandatory)][string[]]$FlowIds,[Parameter(Mandatory)][string]$OutputPath)
$ErrorActionPreference='Stop'
$inspectRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$inspectRuntime=Join-Path $inspectRoot 'runtime/my-legend-story'
$inspectProtected=ConvertTo-SecureString ([IO.File]::ReadAllText((Join-Path $inspectRuntime 'service-secrets.dpapi')).Trim())
$inspectSecrets=[Net.NetworkCredential]::new('', $inspectProtected).Password|ConvertFrom-Json
$inspectProtected.Dispose()
$inspectInfo=[Diagnostics.ProcessStartInfo]::new()
$inspectInfo.FileName=Join-Path $inspectRuntime '.venv/Scripts/python.exe'
$inspectInfo.UseShellExecute=$false;$inspectInfo.CreateNoWindow=$true
$inspectInfo.ArgumentList.Add('-X');$inspectInfo.ArgumentList.Add('utf8')
$inspectInfo.ArgumentList.Add((Join-Path $PSScriptRoot 'inspect_creation_acceptance.py'))
foreach($fid in $FlowIds){$inspectInfo.ArgumentList.Add('--flow');$inspectInfo.ArgumentList.Add($fid)}
$inspectInfo.ArgumentList.Add('--output');$inspectInfo.ArgumentList.Add([IO.Path]::GetFullPath($OutputPath))
$inspectInfo.EnvironmentVariables['MY_LEGEND_SERVICE_TOKEN']=$inspectSecrets.service_token
$inspectInfo.EnvironmentVariables['PGPASSWORD']=$inspectSecrets.db_password
$inspectInfo.EnvironmentVariables['MY_LEGEND_PRODUCTION_DSN']='host=127.0.0.1 port=55439 dbname=postgres user=my_legend_story'
try{$inspectProcess=[Diagnostics.Process]::Start($inspectInfo);$inspectProcess.WaitForExit();if($inspectProcess.ExitCode){throw 'Inspection failed'}}
finally{$inspectInfo.EnvironmentVariables.Remove('PGPASSWORD');$inspectInfo.EnvironmentVariables.Remove('MY_LEGEND_SERVICE_TOKEN');$inspectSecrets=$null}
