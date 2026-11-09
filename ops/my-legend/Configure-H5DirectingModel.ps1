param([ValidateSet('opencode_go','moma')][string]$Provider='opencode_go')
. (Join-Path $PSScriptRoot 'H5DirectingModel.Common.ps1')
$taskDirectory = Get-H5DirectingModelDirectory
$taskSecure = $null
if ($Provider -eq 'moma') {
    $taskSourceDirectory = Get-StoryModelDirectory
    $taskSourceProfile = Read-StoryModelProfile $taskSourceDirectory
    # Reuse the already configured local credential without printing or persisting plaintext.
    $taskCheck = Read-StoryModelSecret $taskSourceDirectory $taskSourceProfile
    if (!$taskCheck) { throw 'Empty MOMA credential; existing configuration unchanged.' }
    $taskCheck = $null
} else {
    $taskSecure = Read-Host 'OpenCode Go API key (hidden input)' -AsSecureString
    if ($taskSecure.Length -eq 0) { throw 'Empty credential; existing configuration unchanged.' }
}
try {
    New-Item -ItemType Directory -Path $taskDirectory -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $taskDirectory '.gitignore'), "*`n!.gitignore`n")
    $taskCredential = 'credential-' + [Guid]::NewGuid().ToString('N') + '.dpapi'
    if ($Provider -eq 'moma') {
        Copy-Item -LiteralPath (Join-Path $taskSourceDirectory $taskSourceProfile.encrypted_key_file) -Destination (Join-Path $taskDirectory $taskCredential)
    } else {
        [IO.File]::WriteAllText((Join-Path $taskDirectory $taskCredential), (ConvertFrom-SecureString $taskSecure), [Text.Encoding]::ASCII)
    }
    $taskProfilePath = Join-Path $taskDirectory 'profile.json'
    if (Test-Path -LiteralPath $taskProfilePath) {
        Copy-Item -LiteralPath $taskProfilePath -Destination (Join-Path $taskDirectory ('profile.previous-' + [Guid]::NewGuid().ToString('N') + '.json'))
    }
    Write-StoryModelJson $taskProfilePath ([ordered]@{
        schema_version='my-legend.h5-directing-provider/1'
        provider=$Provider
        base_url=$(if ($Provider -eq 'moma') {$taskSourceProfile.base_url} else {'https://opencode.ai/inference/go/openai/v1'})
        model=$(if ($Provider -eq 'moma') {$taskSourceProfile.model} else {'deepseek-v4.1-flash'})
        encrypted_key_file=$taskCredential
        credential_storage='Windows CurrentUser DPAPI'
        configured_at=[DateTime]::UtcNow.ToString('o')
    })
} finally { if ($taskSecure) {$taskSecure.Dispose()} }
Write-Output 'H5 directing profile saved with Windows CurrentUser DPAPI; running workers are unchanged.'
