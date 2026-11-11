# Regression checks for stale pid-file handling. Runs without Pester so it can
# be executed on any Windows host with PowerShell 7:
#   pwsh -NoProfile -File .\ops\my-legend\Test-StartupPid.ps1
param([string]$WorkDirectory)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Startup.Common.ps1')

if (!$WorkDirectory) {
    $WorkDirectory = Join-Path ([IO.Path]::GetTempPath()) ('legend-pid-test-' + [Guid]::NewGuid().ToString('N'))
}
New-Item -ItemType Directory -Path $WorkDirectory -Force | Out-Null
$current = Get-CimInstance Win32_Process -Filter "ProcessId=$PID"
$currentPath = $current.ExecutablePath
$otherPath = Join-Path $env:SystemRoot 'System32/notepad.exe'
$failures = [System.Collections.Generic.List[string]]::new()

function Assert-True([bool]$Condition, [string]$Message) {
    if ($Condition) { Write-Output ("  ok   " + $Message) }
    else { $failures.Add($Message); Write-Output ("  FAIL " + $Message) }
}

Write-Output ('pid fixtures: ' + $WorkDirectory)

# 1. No record at all: nothing to resolve, nothing created.
$path = Join-Path $WorkDirectory 'absent.pid'
Assert-True ($null -eq (Resolve-LegendPidFile $path $currentPath '')) 'missing record resolves to null'
Assert-True (!(Test-Path -LiteralPath $path)) 'missing record creates no file'

# 2. Recorded process already exited: the record is dropped quietly.
$path = Join-Path $WorkDirectory 'exited.pid'
[IO.File]::WriteAllText($path, '999999')
Assert-True ($null -eq (Resolve-LegendPidFile $path $currentPath '')) 'exited pid resolves to null'
Assert-True (!(Test-Path -LiteralPath $path)) 'exited pid record is removed'
Assert-True (@(Get-ChildItem $WorkDirectory -Filter 'exited.pid.stale-*').Count -eq 0) 'exited pid leaves no backup'

# 3. PID reused by an unrelated program: stale, backed up, startup continues.
$path = Join-Path $WorkDirectory 'reused.pid'
[IO.File]::WriteAllText($path, [string]$PID)
Assert-True ($null -eq (Resolve-LegendPidFile $path $otherPath 'my_legend')) 'reused pid resolves to null'
Assert-True (!(Test-Path -LiteralPath $path)) 'reused pid record is moved aside'
Assert-True (@(Get-ChildItem $WorkDirectory -Filter 'reused.pid.stale-*').Count -eq 1) 'reused pid keeps an evidence backup'

# 4. Live process with the expected identity: reuse, keep the record.
$path = Join-Path $WorkDirectory 'live.pid'
[IO.File]::WriteAllText($path, [string]$PID)
$resolved = Resolve-LegendPidFile $path $currentPath ''
Assert-True ($null -ne $resolved) 'live matching pid is reused'
Assert-True ($resolved.ProcessId -eq $PID) 'live matching pid returns the same process'
Assert-True (Test-Path -LiteralPath $path) 'live matching record is kept'

# 5. Legacy numeric record still resolves for an existing install.
$path = Join-Path $WorkDirectory 'legacy.pid'
[IO.File]::WriteAllText($path, ([string]$PID + "`n"))
Assert-True ($null -ne (Resolve-LegendPidFile $path $currentPath '')) 'legacy numeric record still resolves'

# 6. Same PID but a different start time means the ID was recycled.
$path = Join-Path $WorkDirectory 'recycled.pid'
[IO.File]::WriteAllText($path, ([ordered]@{ pid = $PID; started_ticks = '1' } | ConvertTo-Json -Compress))
Assert-True ($null -eq (Resolve-LegendPidFile $path $currentPath '')) 'recycled start time resolves to null'
Assert-True (@(Get-ChildItem $WorkDirectory -Filter 'recycled.pid.stale-*').Count -eq 1) 'recycled start time keeps an evidence backup'

# 7. New records carry the start time so future checks do not depend on command lines.
$path = Join-Path $WorkDirectory 'written.pid'
Write-LegendPidFile $path $PID
$record = Get-LegendPidRecord $path
Assert-True ($record.pid -eq $PID) 'written record keeps the pid'
Assert-True (![string]::IsNullOrEmpty($record.started_ticks)) 'written record keeps the start time'
Assert-True ($null -ne (Resolve-LegendPidFile $path $currentPath '')) 'written record resolves back to the process'

if ($failures.Count) {
    Write-Output ('FAILED ' + $failures.Count + ' check(s).')
    exit 1
}
Write-Output 'All pid-file checks passed.'
exit 0
