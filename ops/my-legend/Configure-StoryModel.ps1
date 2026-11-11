param(
    [string]$Directory,
    [ValidateSet('opencode_go', 'moma')][string]$Provider,
    [switch]$Force,
    [switch]$DryRun
)
. "$PSScriptRoot/StoryModel.Common.ps1"
$ErrorActionPreference = 'Stop'

# Provider presets live here so a configuration run can never silently fall
# back to a gateway that is no longer used by this project.
$storyProviders = [ordered]@{
    opencode_go = [ordered]@{
        provider = 'opencode_go'
        base_url = 'https://opencode.ai/inference/go/openai/v1'
        model = 'deepseek-v4.1-flash'
    }
    moma = [ordered]@{
        provider = 'moma_openai_compatible'
        base_url = 'https://moma.cmecloud.cn/v1'
        model = 'qwen/deepseek-v4-pro'
    }
}

$targetDirectory = Get-StoryModelDirectory $Directory
$profilePath = Join-Path $targetDirectory 'profile.json'
$current = $null
if (Test-Path -LiteralPath $profilePath) {
    try { $current = Get-Content -LiteralPath $profilePath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { $current = $null }
}

function Get-StoryModelValue($Profile, [string]$Name) {
    # StrictMode makes a missing property fatal; read the profile defensively so
    # a hand-edited file reports instead of crashing.
    if (!$Profile) { return $null }
    $property = $Profile.PSObject.Properties[$Name]
    if ($property) { return $property.Value }
    return $null
}

function Show-CurrentStoryModel {
    if ($current) {
        Write-Output ("Current: provider={0} base_url={1} model={2}" -f (Get-StoryModelValue $current 'provider'), (Get-StoryModelValue $current 'base_url'), (Get-StoryModelValue $current 'model'))
    } else {
        Write-Output 'Current: no readable story model profile.'
    }
}

# Without an explicit provider the script is read-only. This keeps an accidental
# re-run from replacing a working OpenCode Go profile with the legacy MOMA one.
if (!$Provider) {
    Show-CurrentStoryModel
    Write-Output 'No files changed. Pass -Provider opencode_go or -Provider moma to write a profile.'
    exit 0
}

$selection = $storyProviders[$Provider]
$alreadyConfigured = $current -and
    (Get-StoryModelValue $current 'provider') -ceq $selection.provider -and
    (Get-StoryModelValue $current 'base_url') -ceq $selection.base_url -and
    (Get-StoryModelValue $current 'model') -ceq $selection.model

if ($alreadyConfigured) {
    Show-CurrentStoryModel
    Write-Output 'Already configured for this provider; nothing changed.'
    exit 0
}

Show-CurrentStoryModel
Write-Output ("Target:  provider={0} base_url={1} model={2}" -f $selection.provider, $selection.base_url, $selection.model)

if ($DryRun) {
    Write-Output 'Dry run: no files changed.'
    exit 0
}

if (!$Force) {
    $answer = Read-Host 'Type YES to overwrite the story model profile'
    if ($answer -cne 'YES') {
        Write-Output 'Aborted; existing configuration was not changed.'
        exit 1
    }
}

New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
# Keep credentials, profiles and probe evidence out of Git and source deliveries.
[IO.File]::WriteAllText((Join-Path $targetDirectory '.gitignore'), "*`n!.gitignore`n", [Text.UTF8Encoding]::new($false))

$secure = Read-Host ("{0} API key (hidden input)" -f $Provider) -AsSecureString
if ($secure.Length -eq 0) { throw 'No credential supplied; existing configuration was not changed.' }

$credentialName = 'credential-' + [Guid]::NewGuid().ToString('N') + '.dpapi'
$credentialPath = Join-Path $targetDirectory $credentialName
[IO.File]::WriteAllText($credentialPath, (ConvertFrom-SecureString $secure), [Text.Encoding]::ASCII)
$secure.Dispose()

$backupPath = $null
if (Test-Path -LiteralPath $profilePath) {
    $backupPath = Join-Path $targetDirectory ('profile.previous-' + [Guid]::NewGuid().ToString('N') + '.json')
    Copy-Item -LiteralPath $profilePath -Destination $backupPath
}

$profile = [ordered]@{
    schema_version = 'my-legend.story-provider/1'
    provider = $selection.provider
    base_url = $selection.base_url
    model = $selection.model
    model_tiers = @('FAST', 'BALANCED', 'STRONG', 'LONG', 'STRONG_ALT')
    stream = $false
    max_tokens_ceiling = 131072
    encrypted_key_file = $credentialName
    credential_storage = 'Windows CurrentUser DPAPI'
    configured_at = [DateTime]::UtcNow.ToString('o')
    app_generation_connected = $false
}

try {
    Write-StoryModelJson $profilePath $profile
    # Re-read through the same validation the workers use; a profile the
    # launcher cannot load must not replace a working one.
    $verified = Read-StoryModelProfile $targetDirectory
} catch {
    if ($backupPath) { Copy-Item -LiteralPath $backupPath -Destination $profilePath -Force }
    Remove-Item -LiteralPath $credentialPath -Force -ErrorAction SilentlyContinue
    throw "Story model profile was not saved; previous configuration restored. $($_.Exception.Message)"
}

Write-Output ("Story model configured: provider={0} model={1}" -f $verified.provider, $verified.model)
if ($backupPath) { Write-Output ("Previous profile kept as " + $backupPath) }
Write-Output 'Restart the story service and sound/stage workers before the change takes effect.'
