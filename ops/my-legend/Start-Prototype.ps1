param([switch]$BackendOnly, [switch]$StatusOnly)
. "$PSScriptRoot/StoryModel.Common.ps1"
$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$runtime = Join-Path $workspace 'runtime/my-legend-story'
$python = Join-Path $runtime '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Install the isolated StoryGenerate environment first; see README.md.' }
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $runtime '.gitignore'), "*`n!.gitignore`n", [Text.UTF8Encoding]::new($false))
$secretPath = Join-Path $runtime 'service-secrets.dpapi'
if (-not (Test-Path -LiteralPath $secretPath)) {
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    $passwordBytes = New-Object byte[] 32
    $tokenBytes = New-Object byte[] 32
    $random.GetBytes($passwordBytes); $random.GetBytes($tokenBytes); $random.Dispose()
    $values = @{ db_password = [Convert]::ToBase64String($passwordBytes); service_token = [Convert]::ToBase64String($tokenBytes) } | ConvertTo-Json -Compress
    $protected = ConvertTo-SecureString -String $values -AsPlainText -Force
    [IO.File]::WriteAllText($secretPath, (ConvertFrom-SecureString $protected), [Text.Encoding]::ASCII)
    $values = $null; $protected.Dispose()
}
$protected = ConvertTo-SecureString -String ([IO.File]::ReadAllText($secretPath).Trim())
$serviceSecrets = [Net.NetworkCredential]::new('', $protected).Password | ConvertFrom-Json
$protected.Dispose()
$providerDirectory = Get-StoryModelDirectory
$profile = Read-StoryModelProfile $providerDirectory
$processInfo = [Diagnostics.ProcessStartInfo]::new()
$processInfo.FileName = $python
$processInfo.Arguments = '"' + (Join-Path $PSScriptRoot 'run_story_stack.py') + '"' + $(if ($BackendOnly) { ' --backend-only' } else { '' }) + $(if ($StatusOnly) { ' --status-only' } else { '' })
$processInfo.WorkingDirectory = $workspace
$processInfo.UseShellExecute = $false
$processInfo.EnvironmentVariables['MY_LEGEND_DB_PASSWORD'] = $serviceSecrets.db_password
$processInfo.EnvironmentVariables['MY_LEGEND_SERVICE_TOKEN'] = $serviceSecrets.service_token
$processInfo.EnvironmentVariables['DRAMA_LLM_API_KEY'] = Read-StoryModelSecret $providerDirectory $profile
$processInfo.EnvironmentVariables['STORY_LLM_BASE_URL'] = $profile.base_url
$processInfo.EnvironmentVariables['DRAMA_LLM_BASE_URL'] = $profile.base_url
$processInfo.EnvironmentVariables['MY_LEGEND_VISUAL_MODEL_ID'] = $profile.model
$processInfo.EnvironmentVariables['MY_LEGEND_AUDIO_MODEL_ID'] = $profile.model
$processInfo.EnvironmentVariables['APP_ENV'] = 'prod'
foreach ($tier in $profile.model_tiers) { $processInfo.EnvironmentVariables['STORY_LLM_' + $tier + '_MODEL'] = $profile.model }
try {
    $supervisor = [Diagnostics.Process]::Start($processInfo)
    $supervisor.WaitForExit()
    $result = $supervisor.ExitCode
} finally {
    foreach ($name in @('DRAMA_LLM_API_KEY', 'MY_LEGEND_DB_PASSWORD', 'MY_LEGEND_SERVICE_TOKEN')) { $processInfo.EnvironmentVariables.Remove($name) }
    $serviceSecrets = $null
}
exit $result
