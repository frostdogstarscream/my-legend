param([Parameter(Mandatory)][string[]]$FlowIds,[Parameter(Mandatory)][string]$OutputPath)
$ErrorActionPreference='Stop'
$inspectRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$inspectRuntime=Join-Path $inspectRoot 'runtime/my-legend-story'
$inspectProtected=ConvertTo-SecureString ([IO.File]::ReadAllText((Join-Path $inspectRuntime 'service-secrets.dpapi')).Trim())
$inspectSecrets=[Net.NetworkCredential]::new('', $inspectProtected).Password|ConvertFrom-Json
$inspectProtected.Dispose()
$inspectOutput=[IO.Path]::GetFullPath($OutputPath)
$inspectOutputDirectory=Split-Path -Parent $inspectOutput
if($inspectOutputDirectory){New-Item -ItemType Directory -Path $inspectOutputDirectory -Force|Out-Null}
$inspectInfo=[Diagnostics.ProcessStartInfo]::new()
$inspectInfo.FileName=Join-Path $inspectRuntime '.venv/Scripts/python.exe'
$inspectInfo.UseShellExecute=$false;$inspectInfo.CreateNoWindow=$true
# Drain both streams: a full stderr pipe would block the inspector, and the
# reason for a failure must reach the caller instead of a bare message.
$inspectInfo.RedirectStandardOutput=$true
$inspectInfo.RedirectStandardError=$true
$inspectInfo.ArgumentList.Add('-X');$inspectInfo.ArgumentList.Add('utf8')
$inspectInfo.ArgumentList.Add((Join-Path $PSScriptRoot 'inspect_creation_acceptance.py'))
foreach($fid in $FlowIds){$inspectInfo.ArgumentList.Add('--flow');$inspectInfo.ArgumentList.Add($fid)}
$inspectInfo.ArgumentList.Add('--output');$inspectInfo.ArgumentList.Add($inspectOutput)
$inspectInfo.EnvironmentVariables['MY_LEGEND_SERVICE_TOKEN']=$inspectSecrets.service_token
$inspectInfo.EnvironmentVariables['PGPASSWORD']=$inspectSecrets.db_password
$inspectInfo.EnvironmentVariables['MY_LEGEND_PRODUCTION_DSN']='host=127.0.0.1 port=55439 dbname=postgres user=my_legend_story'
try{
    $inspectProcess=[Diagnostics.Process]::Start($inspectInfo)
    $inspectStdoutTask=$inspectProcess.StandardOutput.ReadToEndAsync()
    $inspectStderrTask=$inspectProcess.StandardError.ReadToEndAsync()
    $inspectProcess.WaitForExit()
    $inspectStdout=$inspectStdoutTask.Result
    $inspectStderr=$inspectStderrTask.Result
    if($inspectProcess.ExitCode){
        $inspectDetail=(($inspectStderr -split "`r?`n")|Where-Object{$_.Trim()}|Select-Object -Last 20) -join [Environment]::NewLine
        if(!$inspectDetail){$inspectDetail=(($inspectStdout -split "`r?`n")|Where-Object{$_.Trim()}|Select-Object -Last 20) -join [Environment]::NewLine}
        throw ("Inspection failed (exit {0}){1}{2}" -f $inspectProcess.ExitCode, [Environment]::NewLine, $inspectDetail.Trim())
    }
    Write-Output ("Acceptance report written to " + $inspectOutput)
}
finally{$inspectInfo.EnvironmentVariables.Remove('PGPASSWORD');$inspectInfo.EnvironmentVariables.Remove('MY_LEGEND_SERVICE_TOKEN');$inspectSecrets=$null}
