param(
    [switch]$PreviewOnly,
    [switch]$NoBrowser,
    [ValidateSet('characters', 'create', 'stories', 'home')]
    [string]$Page = 'characters'
)

$ErrorActionPreference = 'Stop'
$frontendRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$frontendUrl = 'http://127.0.0.1:5193/#/pages/' + $Page + '/index'
$frontendLogDirectory = Join-Path $frontendRoot 'runtime/my-legend-story/logs'
$frontendTranscriptStarted = $false
$frontendExitCode = 0
$frontendLog = ''

Push-Location -LiteralPath $frontendRoot
try {
    if ($PSVersionTable.PSVersion -lt [Version]'7.4') {
        throw '需要 PowerShell 7.4 或更高版本（启动器用 Start-Process -Environment 写独立日志）。请双击项目根目录的“启动交互式前端.cmd”，或设置 LEGEND_PWSH 指向 pwsh.exe。'
    }
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    Write-Host '正在启动“我的传说”交互式前端…' -ForegroundColor Cyan
    $frontendRequired = @(
        'runtime/my-legend-story/.venv/Scripts/python.exe',
        'runtime/my-legend-story/service-secrets.dpapi',
        'runtime/my-legend-spine/life-v2-current.json',
        'source/fingertip-legend-app/node_modules/@dcloudio/vite-plugin-uni/bin/uni.js',
        'source/fingertip-legend-app/.env.local'
    )
    foreach ($frontendRelativePath in $frontendRequired) {
        if (!(Test-Path -LiteralPath (Join-Path $frontendRoot $frontendRelativePath))) {
            throw "缺少启动文件：$frontendRelativePath。请先恢复本项目的运行环境。"
        }
    }
    if (!(Get-Command node -ErrorAction SilentlyContinue)) { throw '找不到 Node.js，请确认它已安装并加入 PATH。' }
    New-Item -ItemType Directory -Path $frontendLogDirectory -Force | Out-Null
    $frontendLog = Join-Path $frontendLogDirectory ('interactive-start-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $PID + '.log')
    Start-Transcript -LiteralPath $frontendLog | Out-Null
    $frontendTranscriptStarted = $true
    if ($PreviewOnly) {
        Write-Host '模式：角色、生活动作与已有作品预览'
        & "$PSScriptRoot/Start-SpinePreview.ps1" -QuietEntry
    } else {
        Write-Host '模式：完整创作、声音与舞台服务'
        & "$PSScriptRoot/Start-Creation.ps1"
    }
    Write-Host "`n前端已就绪：$frontendUrl" -ForegroundColor Green
    Write-Host "启动日志：$frontendLog"
    Write-Host '服务在后台运行，关闭这个启动窗口后仍可使用。'
    if (!$NoBrowser) {
        try { Start-Process -FilePath $frontendUrl | Out-Null }
        catch { Write-Warning "浏览器未能自动打开，请手动访问：$frontendUrl" }
    }
} catch {
    $frontendExitCode = 1
    Write-Host "`n启动失败：$($_.Exception.Message)" -ForegroundColor Red
    if ($frontendLog) { Write-Host "启动日志：$frontendLog" }
    Write-Host '请查看上面的具体错误；修复后可再次双击启动入口。'
} finally {
    if ($frontendTranscriptStarted) { Stop-Transcript | Out-Null }
    Pop-Location
}
exit $frontendExitCode
