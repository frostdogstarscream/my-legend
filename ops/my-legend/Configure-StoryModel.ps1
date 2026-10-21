param([string]$Directory)
. "$PSScriptRoot/StoryModel.Common.ps1"
$Directory = Get-StoryModelDirectory $Directory
New-Item -ItemType Directory -Path $Directory -Force | Out-Null
# Keep credentials, profiles and probe evidence out of Git and source deliveries.
[IO.File]::WriteAllText((Join-Path $Directory '.gitignore'), "*`n!.gitignore`n", [Text.UTF8Encoding]::new($false))
$secure = Read-Host 'Enter the MOMA API key (hidden input)' -AsSecureString
if ($secure.Length -eq 0) { throw 'No credential supplied; existing configuration was not changed.' }
$credentialName = 'credential-' + [Guid]::NewGuid().ToString('N') + '.dpapi'
$encrypted = ConvertFrom-SecureString -SecureString $secure
[IO.File]::WriteAllText((Join-Path $Directory $credentialName), $encrypted, [Text.Encoding]::ASCII)
$profilePath = Join-Path $Directory 'profile.json'
if (Test-Path -LiteralPath $profilePath) {
    $backup = Join-Path $Directory ('profile.previous-' + [Guid]::NewGuid().ToString('N') + '.json')
    Copy-Item -LiteralPath $profilePath -Destination $backup
}
$profile = [ordered]@{
    schema_version = 'my-legend.story-provider/1'
    provider = 'moma_openai_compatible'
    base_url = 'https://moma.cmecloud.cn/v1'
    model = 'qwen/deepseek-v4-pro'
    model_tiers = @('FAST', 'BALANCED', 'STRONG', 'LONG', 'STRONG_ALT')
    stream = $false
    max_tokens_ceiling = 131072
    encrypted_key_file = $credentialName
    credential_storage = 'Windows CurrentUser DPAPI'
    configured_at = [DateTime]::UtcNow.ToString('o')
    app_generation_connected = $false
}
Write-StoryModelJson $profilePath $profile
$secure.Dispose()
Write-Output 'Story model configuration saved. Credential encrypted for this Windows user.'
Write-Output 'Gateway: https://moma.cmecloud.cn/v1 ; Model: qwen/deepseek-v4-pro'
Write-Output 'Run Test-StoryModel.ps1 to verify access. The App remains in demo mode.'
