. (Join-Path $PSScriptRoot 'StoryModel.Common.ps1')

function Get-H5DirectingModelDirectory {
    return [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../runtime/my-legend-production/h5-directing-provider'))
}

function Read-H5DirectingModelProfile {
    param([string]$Directory)
    $profile = Get-Content -LiteralPath (Join-Path $Directory 'profile.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $allowedProvider = ($profile.provider -ceq 'opencode_go' -and
        $profile.base_url -ceq 'https://opencode.ai/inference/go/openai/v1' -and
        $profile.model -cin @('deepseek-v4.1-flash')) -or
        ($profile.provider -ceq 'moma' -and
        $profile.base_url -ceq 'https://moma.cmecloud.cn/v1' -and
        $profile.model -ceq 'qwen/deepseek-v4-pro')
    if ($profile.schema_version -cne 'my-legend.h5-directing-provider/1' -or !$allowedProvider) { throw 'Unexpected H5 directing provider profile.' }
    if ($profile.encrypted_key_file -notmatch '^credential-[a-f0-9]{32}\.dpapi$') { throw 'Invalid credential reference.' }
    return $profile
}
