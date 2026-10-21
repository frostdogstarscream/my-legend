Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-StoryModelDirectory {
    param([string]$Directory)
    if (-not $Directory) {
        $Directory = Join-Path $PSScriptRoot '../../runtime/my-legend-production/story-provider'
    }
    return [IO.Path]::GetFullPath($Directory)
}

function Read-StoryModelProfile {
    param([string]$Directory)
    $profile = Get-Content -LiteralPath (Join-Path $Directory 'profile.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($profile.schema_version -ne 'my-legend.story-provider/1') { throw 'Unsupported story provider profile.' }
    # The saved credential is scoped to the gateway explicitly selected for this project.
    if ($profile.base_url -cne 'https://moma.cmecloud.cn/v1') { throw 'Unexpected story gateway.' }
    if ($profile.model -cne 'qwen/deepseek-v4-pro') { throw 'Unexpected story model.' }
    if ($profile.encrypted_key_file -notmatch '^credential-[a-f0-9]{32}\.dpapi$') { throw 'Invalid credential reference.' }
    return $profile
}

function Read-StoryModelSecret {
    param([string]$Directory, $Profile)
    $ciphertext = Get-Content -LiteralPath (Join-Path $Directory $Profile.encrypted_key_file) -Raw -Encoding ASCII
    # Without -Key, Windows PowerShell encrypts for the current Windows user using DPAPI.
    try { $secure = ConvertTo-SecureString -String $ciphertext.Trim() }
    catch { throw 'Credential cannot be decrypted in this Windows context. Run Configure-StoryModel.ps1 in the same normal user terminal as the worker.' }
    return [Net.NetworkCredential]::new('', $secure).Password
}

function Write-StoryModelJson {
    param([string]$Path, $Value)
    $temporary = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    $json = ConvertTo-Json -InputObject $Value -Depth 12
    [IO.File]::WriteAllText($temporary, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}
