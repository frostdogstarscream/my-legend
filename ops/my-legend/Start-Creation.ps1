param([switch]$RestartApi)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/H5DirectingModel.Common.ps1"
. "$PSScriptRoot/Startup.Common.ps1"
$creationRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$creationRuntime = Join-Path $creationRoot 'runtime/my-legend-story'
$creationPython = Join-Path $creationRuntime '.venv/Scripts/python.exe'
& "$PSScriptRoot/Start-SpinePreview.ps1" -QuietEntry
$creationProtected = ConvertTo-SecureString ([IO.File]::ReadAllText((Join-Path $creationRuntime 'service-secrets.dpapi')).Trim())
$creationSecrets = [Net.NetworkCredential]::new('', $creationProtected).Password | ConvertFrom-Json
$creationProtected.Dispose()
$creationProfileRoot = Get-StoryModelDirectory
$creationProfile = Read-StoryModelProfile $creationProfileRoot
$creationVisualRoot = Get-H5DirectingModelDirectory
$creationVisualProfile = Read-H5DirectingModelProfile $creationVisualRoot
$creationEnv = @{
    MY_LEGEND_WORKSPACE=$creationRoot; MY_LEGEND_STORY_RUNTIME=(Join-Path $creationRuntime 'app')
    MY_LEGEND_STORY_TOKEN=$creationSecrets.service_token; PGPASSWORD=$creationSecrets.db_password
    MY_LEGEND_PRODUCTION_DSN='host=127.0.0.1 port=55439 dbname=postgres user=my_legend_story'
    MY_LEGEND_PRODUCTION_RUNTIME=(Join-Path $creationRoot 'runtime/my-legend-production/independent')
    MY_LEGEND_WORKFLOW_PATHS=(Join-Path $creationRoot 'runtime/my-legend-production/workflows.json')
    MY_LEGEND_MEDIA_PYTHON=(Join-Path $creationRoot 'runtime/my-legend-production/.venv/Scripts/python.exe')
    PYTHONUTF8='1'
}
$engineEnv=@{};$soundEnv=@{};$stageEnv=@{}
function Start-CreationProcess($name, $arguments, $extra, [switch]$Wait) {
    $pidFile=Join-Path $creationRuntime ($name+'.pid')
    if (!$Wait -and (Test-Path -LiteralPath $pidFile)) {
        $savedId=[int]([IO.File]::ReadAllText($pidFile).Trim())
        $existing=Get-CimInstance Win32_Process -Filter "ProcessId=$savedId" -ErrorAction SilentlyContinue
        if ($existing -and $existing.ExecutablePath -eq $creationPython -and $existing.CommandLine.Contains($arguments)) {
            Write-Output "$name already running";return
        }
        if($existing){throw "$name PID conflict: $savedId belongs to a different process. Inspect $pidFile before restarting."}
    }
    if(!$Wait){
        $matching=@(Get-CimInstance Win32_Process | Where-Object {$_.ExecutablePath -eq $creationPython -and $_.CommandLine -and $_.CommandLine.Contains($arguments)})
        if($matching.Count -gt 1){throw "$name has multiple matching processes; inspect before starting another."}
        if($matching.Count -eq 1){[IO.File]::WriteAllText($pidFile,[string]$matching[0].ProcessId);Write-Output "$name reused";return}
    }
    $info=[Diagnostics.ProcessStartInfo]::new();$info.FileName=$creationPython;$info.Arguments=$arguments
    $info.WorkingDirectory=$creationRoot;$info.UseShellExecute=$false;$info.CreateNoWindow=$true;$info.WindowStyle='Hidden'
    foreach($entry in $creationEnv.GetEnumerator()){$info.EnvironmentVariables[$entry.Key]=$entry.Value}
    foreach($entry in $extra.GetEnumerator()){$info.EnvironmentVariables[$entry.Key]=$entry.Value}
    $child=[Diagnostics.Process]::Start($info)
    if($Wait){$child.WaitForExit();if($child.ExitCode){throw "$name failed"}}
    else{[IO.File]::WriteAllText($pidFile,[string]$child.Id);Write-Output "$name started"}
}
try {
    Start-CreationProcess 'flow-migrate' '-m my_legend_production.production.cli migrate' @{} -Wait
    $engineEnv=@{
        STORY_DATABASE_URL=('postgresql+psycopg://my_legend_story:'+ [Uri]::EscapeDataString($creationSecrets.db_password)+'@127.0.0.1:55439/postgres')
        STORY_DATABASE_SCHEMA='my_legend';STORY_API_KEYS=(@{$creationSecrets.service_token=@{principal_id='my-legend-local';admin=$true}}|ConvertTo-Json -Compress)
        STORY_EVIDENCE_DIR=(Join-Path $creationRuntime 'model-evidence'); APP_ENV='prod'
        DRAMA_LLM_API_KEY=(Read-StoryModelSecret $creationProfileRoot $creationProfile)
        DRAMA_LLM_BASE_URL=$creationProfile.base_url;STORY_LLM_BASE_URL=$creationProfile.base_url
    }
    foreach($tier in $creationProfile.model_tiers){$engineEnv['STORY_LLM_'+$tier+'_MODEL']=$creationProfile.model}
    if(!(Assert-LegendListener 8897 'drama_engine.service serve' $creationPython)) {
        Start-CreationProcess 'creation-story-api' '-m drama_engine.service serve --host 127.0.0.1 --port 8897' $engineEnv
    }
    Wait-LegendHttp 'http://127.0.0.1:8897/health' 'Story API'
    Start-CreationProcess 'creation-story-worker' ('"'+(Join-Path $PSScriptRoot 'run_story_worker.py')+'"') $engineEnv
    Start-CreationProcess 'creation-library-sync' ('"'+(Join-Path $PSScriptRoot 'sync_creation_library.py')+'"') @{} -Wait
    $soundEnv=@{
        MY_LEGEND_PRODUCTION_MODULES='story,sound_plan,audio'
        MY_LEGEND_AUDIO_MODEL_BASE_URL=$creationProfile.base_url;MY_LEGEND_AUDIO_MODEL_ID=$creationProfile.model
        MY_LEGEND_AUDIO_MODEL_KEY=(Read-StoryModelSecret $creationProfileRoot $creationProfile)
    }
    Start-CreationProcess 'creation-sound' '-m my_legend_production.production.flow_runtime sound' $soundEnv
    $stageEnv=@{
        MY_LEGEND_PRODUCTION_MODULES='performance_import,performance_context,spine_direct,spine_compile,performance_package'
        MY_LEGEND_VISUAL_MODEL_BASE_URL=$creationVisualProfile.base_url;MY_LEGEND_VISUAL_MODEL_ID=$creationVisualProfile.model
        MY_LEGEND_VISUAL_MODEL_KEY=(Read-StoryModelSecret $creationVisualRoot $creationVisualProfile)
    }
    Start-CreationProcess 'creation-stage' '-m my_legend_production.production.flow_runtime stage' $stageEnv
    Start-CreationProcess 'creation-coordinator' '-m my_legend_production.production.flow_runtime coordinator' @{}
    if($RestartApi){
        $listeners=Get-NetTCPConnection -LocalPort 8895 -State Listen -ErrorAction SilentlyContinue
        foreach($listener in $listeners){
            $process=Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
            if($process.CommandLine -notlike '*my_legend_production.story_api*'){throw 'Port 8895 is occupied by a different service'}
            Stop-Process -Id $process.ProcessId
        }
        & "$PSScriptRoot/Start-SpinePreview.ps1" -QuietEntry
    }
    $creationReady=$false
    for($attempt=0;$attempt -lt 15;$attempt++){
        try {
            $session=Invoke-RestMethod -Uri 'http://127.0.0.1:8895/api/v1/auth/local' -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec 3
            $headers=@{Authorization=('Bearer '+$session.data.accessToken)}
            $capabilities=Invoke-RestMethod -Uri 'http://127.0.0.1:8895/api/v1/production-capabilities' -Headers $headers -TimeoutSec 3
            if($capabilities.data.h5_flow.configured){$creationReady=$true;break}
        } catch { }
        Start-Sleep -Seconds 1
    }
    if(!$creationReady){$missing=($capabilities.data.h5_flow.services.PSObject.Properties|Where-Object {!$_.Value}|ForEach-Object Name)-join ', ';throw "Creation services not ready: $missing. Inspect runtime/my-legend-production/independent/logs."}
    $workerId=[int]([IO.File]::ReadAllText((Join-Path $creationRuntime 'creation-story-worker.pid')).Trim())
    $worker=Get-CimInstance Win32_Process -Filter "ProcessId=$workerId"
    if(!$worker -or $worker.ExecutablePath -ne $creationPython -or !$worker.CommandLine.Contains('run_story_worker.py')){throw 'Story worker is not running with the expected identity.'}
    Write-Output 'Creation services verified. Open http://127.0.0.1:5193/#/pages/create/index'
} finally {
    $creationEnv.Clear();$creationSecrets=$null
    if($engineEnv){$engineEnv.Clear()};if($soundEnv){$soundEnv.Clear()};if($stageEnv){$stageEnv.Clear()}
}
