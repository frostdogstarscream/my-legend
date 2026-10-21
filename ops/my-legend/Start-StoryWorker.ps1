param(
    [Parameter(Mandatory=$true)][string]$EngineRoot,
    [string]$Python = 'python',
    [string]$Directory
)
. "$PSScriptRoot/StoryModel.Common.ps1"
$Directory = Get-StoryModelDirectory $Directory
$profile = Read-StoryModelProfile $Directory
$engineRootPath = [IO.Path]::GetFullPath($EngineRoot)
# Keep this script ASCII-compatible with Windows PowerShell 5's source decoding.
$engineCandidates = @(Get-ChildItem -LiteralPath $engineRootPath -Directory -Filter '4-*' | Where-Object {
    Test-Path -LiteralPath (Join-Path $_.FullName 'drama_engine/service/worker.py')
})
if ($engineCandidates.Count -ne 1) {
    throw 'EngineRoot must be the StoryGenerate checkout; the original engine is not bundled in this adapter project.'
}
$engineCode = $engineCandidates[0].FullName
if (-not $env:STORY_DATABASE_URL -or -not $env:STORY_API_KEYS) {
    throw 'Set STORY_DATABASE_URL and STORY_API_KEYS for the story service first. No worker was started.'
}
$processInfo = [Diagnostics.ProcessStartInfo]::new()
$processInfo.FileName = $Python
$processInfo.Arguments = '-m drama_engine.service worker'
$processInfo.WorkingDirectory = [IO.Path]::GetFullPath($EngineRoot)
$processInfo.UseShellExecute = $false
$processInfo.EnvironmentVariables['PYTHONPATH'] = $engineCode
$processInfo.EnvironmentVariables['APP_ENV'] = 'prod'
$processInfo.EnvironmentVariables['DRAMA_LLM_BASE_URL'] = $profile.base_url
$processInfo.EnvironmentVariables['STORY_LLM_BASE_URL'] = $profile.base_url
$processInfo.EnvironmentVariables['DRAMA_LLM_MODEL'] = $profile.model
foreach ($tier in $profile.model_tiers) {
    $processInfo.EnvironmentVariables['STORY_LLM_' + $tier + '_MODEL'] = $profile.model
}
$secret = Read-StoryModelSecret $Directory $profile
$processInfo.EnvironmentVariables['DRAMA_LLM_API_KEY'] = $secret
$processInfo.EnvironmentVariables['STORY_LLM_API_KEY'] = $secret
Write-Output 'Starting StoryGenerate Worker with MOMA / qwen/deepseek-v4-pro.'
try {
    $worker = [Diagnostics.Process]::Start($processInfo)
    $worker.WaitForExit()
    $workerExitCode = $worker.ExitCode
} finally {
    $processInfo.EnvironmentVariables.Remove('DRAMA_LLM_API_KEY')
    $processInfo.EnvironmentVariables.Remove('STORY_LLM_API_KEY')
    $secret = $null
}
exit $workerExitCode
