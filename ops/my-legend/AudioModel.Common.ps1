. "$PSScriptRoot/StoryModel.Common.ps1"
function Get-AudioModelDirectory {
    return [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../runtime/my-legend-production/audio-provider'))
}
function Read-AudioModelProfile {
    $directory = Get-AudioModelDirectory
    $profile = Get-Content -LiteralPath (Join-Path $directory 'profile.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($profile.schema_version -ne 'my-legend.audio-provider/1' -or $profile.provider -ne 'qianwen_ai_audio') { throw 'Unsupported audio provider profile.' }
    if ($profile.endpoint -cne 'https://maas.qianwenaiapi.com/api/v1/services/audio/tts/SpeechSynthesizer') { throw 'Unexpected audio endpoint.' }
    if ($profile.tts_model -cne 'qwen-audio-3.1-tts-flash' -or $profile.sfx_model -cne 'qwen-audio-3.1-tts-next') { throw 'Unexpected audio model.' }
    if ($profile.encrypted_key_file -notmatch '^credential-[a-f0-9]{32}\.dpapi$') { throw 'Invalid credential reference.' }
    return $profile
}
function Read-AudioModelSecret {
    param($Profile)
    return Read-StoryModelSecret (Get-AudioModelDirectory) $Profile
}
