param([string]$Directory)
. "$PSScriptRoot/StoryModel.Common.ps1"
if (-not $Directory) { $Directory = Join-Path $PSScriptRoot '../../runtime/my-legend-production/audio-provider' }
$Directory = [IO.Path]::GetFullPath($Directory)
New-Item -ItemType Directory -Path $Directory -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $Directory '.gitignore'), "*`n!.gitignore`n", [Text.UTF8Encoding]::new($false))
$secure = Read-Host 'Enter the Qianwen AI audio API key (hidden input; not the MOMA text key)' -AsSecureString
if ($secure.Length -eq 0) { throw 'No credential supplied; existing configuration was not changed.' }
try {
    $credentialName = 'credential-' + [Guid]::NewGuid().ToString('N') + '.dpapi'
    [IO.File]::WriteAllText((Join-Path $Directory $credentialName), (ConvertFrom-SecureString $secure), [Text.Encoding]::ASCII)
    $profilePath = Join-Path $Directory 'profile.json'
    if (Test-Path -LiteralPath $profilePath) {
        Copy-Item -LiteralPath $profilePath -Destination (Join-Path $Directory ('profile.previous-' + [Guid]::NewGuid().ToString('N') + '.json'))
    }
    Write-StoryModelJson $profilePath ([ordered]@{
        schema_version = 'my-legend.audio-provider/1'
        provider = 'qianwen_ai_audio'
        endpoint = 'https://maas.qianwenaiapi.com/api/v1/services/audio/tts/SpeechSynthesizer'
        tts_model = 'qwen-audio-3.1-tts-flash'
        sfx_model = 'qwen-audio-3.1-tts-next'
        encrypted_key_file = $credentialName
        credential_storage = 'Windows CurrentUser DPAPI'
        configured_at = [DateTime]::UtcNow.ToString('o')
        account_verified = $false
    })
} finally { $secure.Dispose() }
Write-Output 'Audio credential encrypted for this Windows user. No generation has been submitted.'
Write-Output 'Provider: Qianwen AI audio. Model and voice availability still require an authenticated probe.'
