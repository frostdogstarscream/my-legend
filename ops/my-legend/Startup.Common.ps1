# Read-only identity checks. Never terminate an unrelated listener or PID.
function Normalize-LegendPath([string]$Path) {
    if (!$Path) { return '' }
    return ([IO.Path]::GetFullPath($Path)).TrimEnd('\')
}
function Test-LegendExecutable($Entry,[string]$Executable) {
    $target = Normalize-LegendPath $Executable
    for($depth=0;$depth -lt 3 -and $Entry;$depth++){
        if((Normalize-LegendPath $Entry.ExecutablePath) -eq $target){return $true}
        $Entry=Get-CimInstance Win32_Process -Filter "ProcessId=$($Entry.ParentProcessId)" -ErrorAction SilentlyContinue
    }
    return $false
}
function Assert-LegendListener([int]$Port,[string]$Identity,[string]$Executable='') {
    $listeners=@(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -eq $Port)
    foreach($listener in $listeners){
        $entry=Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
        if(!$entry -or !$entry.CommandLine -or !$entry.CommandLine.Replace('\','/').Contains($Identity.Replace('\','/')) -or ($Executable -and !(Test-LegendExecutable $entry $Executable))){
            throw "Port $Port conflict: PID $($listener.OwningProcess) is not the expected $Identity service."
        }
    }
    return $listeners.Count -gt 0
}
# A pid file can outlive its process, and Windows reuses process IDs. A live
# PID alone never proves identity, so records keep the process start time and
# are validated against the expected executable and command line. A recycled
# PID is stale evidence, not a fatal conflict.
function Get-LegendPidRecord([string]$Path) {
    if (!(Test-Path -LiteralPath $Path)) { return $null }
    $raw = ([IO.File]::ReadAllText($Path)).Trim()
    if (!$raw) { return @{ pid = 0; started_ticks = ''; legacy = $true; invalid = $true } }
    if ($raw.StartsWith('{')) {
        try { $value = $raw | ConvertFrom-Json } catch { return @{ pid = 0; started_ticks = ''; legacy = $false; invalid = $true } }
        $recorded = 0
        if (![int]::TryParse([string]$value.pid, [ref]$recorded)) { return @{ pid = 0; started_ticks = ''; legacy = $false; invalid = $true } }
        return @{ pid = $recorded; started_ticks = [string]$value.started_ticks; legacy = $false; invalid = $false }
    }
    $number = 0
    if ([int]::TryParse($raw, [ref]$number)) { return @{ pid = $number; started_ticks = ''; legacy = $true; invalid = $false } }
    return @{ pid = 0; started_ticks = ''; legacy = $true; invalid = $true }
}
function Get-LegendProcessStart([int]$ProcessId) {
    $entry = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (!$entry -or !$entry.CreationDate) { return '' }
    # Ticks avoid ConvertFrom-Json turning an ISO 8601 string back into a
    # culture-formatted DateTime and breaking the comparison.
    return [string]$entry.CreationDate.ToUniversalTime().Ticks
}
function Write-LegendPidFile([string]$Path, [int]$ProcessId) {
    $record = [ordered]@{ pid = $ProcessId; started_ticks = (Get-LegendProcessStart $ProcessId) } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText($Path, $record)
}
function Move-LegendPidFileAside([string]$Path, [string]$Reason) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $safe = ($Reason -replace '[^0-9A-Za-z._-]', '-')
    $destination = "$Path.stale-$stamp-$safe"
    try {
        Move-Item -LiteralPath $Path -Destination $destination -Force
        Write-Warning "Stale pid file $Path ($Reason) kept as $destination"
    } catch {
        Write-Warning "Stale pid file $Path ($Reason) could not be moved: $($_.Exception.Message)"
    }
}
function Test-LegendProcess($Entry, [string]$Executable, [string]$CommandMatch, [switch]$AllowParent) {
    if (!$Entry) { return $false }
    if ($CommandMatch -and (!$Entry.CommandLine -or !$Entry.CommandLine.Replace('\','/').Contains($CommandMatch.Replace('\','/')))) { return $false }
    if ($Executable) {
        # Strict by default: a launcher trampoline and the interpreter it spawns
        # share a command line, so enumeration must not count both as matches.
        if ($AllowParent) { if (!(Test-LegendExecutable $Entry $Executable)) { return $false } }
        elseif ((Normalize-LegendPath $Entry.ExecutablePath) -ne (Normalize-LegendPath $Executable)) { return $false }
    }
    return $true
}
# Returns the recorded process when it still matches. Missing and exited
# records are discarded quietly; a live PID owned by another program is kept
# aside as evidence so the next start is not blocked.
function Resolve-LegendPidFile([string]$Path, [string]$Executable, [string]$CommandMatch) {
    $record = Get-LegendPidRecord $Path
    if (!$record) { return $null }
    if ($record.invalid -or !$record.pid) { Move-LegendPidFileAside $Path 'unreadable'; return $null }
    $entry = Get-CimInstance Win32_Process -Filter "ProcessId=$($record.pid)" -ErrorAction SilentlyContinue
    if (!$entry) { Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue; return $null }
    if (!(Test-LegendProcess $entry $Executable $CommandMatch -AllowParent)) {
        Move-LegendPidFileAside $Path "pid-$($record.pid)-now-$($entry.Name)"; return $null
    }
    if (!$record.legacy -and $record.started_ticks) {
        $actual = Get-LegendProcessStart $record.pid
        if ($actual -and $actual -ne $record.started_ticks) { Move-LegendPidFileAside $Path "pid-$($record.pid)-start-time-changed"; return $null }
    }
    return $entry
}
function Start-LegendProcess {
    # Start-Process writes redirected output straight to a file, so a child
    # never inherits the launcher's stdout and cannot keep a caller's pipe open.
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string]$Arguments,
        [string]$WorkingDirectory,
        [hashtable]$Environment = @{},
        [string]$StandardOutput,
        [string]$StandardError,
        [switch]$Wait
    )
    $start = @{ FilePath = $FilePath; PassThru = $true; WindowStyle = 'Hidden' }
    if ($Arguments) { $start.ArgumentList = $Arguments }
    if ($WorkingDirectory) { $start.WorkingDirectory = $WorkingDirectory }
    if ($Environment.Count) { $start.Environment = $Environment }
    if ($StandardOutput) { $start.RedirectStandardOutput = $StandardOutput }
    if ($StandardError) { $start.RedirectStandardError = $StandardError }
    if ($StandardOutput) {
        # A child that inherits any console handle can keep a caller's pipe open
        # after the launcher exits. Give it an empty, private stdin as well.
        $emptyInput = [IO.Path]::ChangeExtension($StandardOutput, '.stdin')
        [IO.File]::WriteAllText($emptyInput, '')
        $start.RedirectStandardInput = $emptyInput
    }
    if ($Wait) { $start.Wait = $true }
    return Start-Process @start
}
function Wait-LegendPort([int]$Port, [string]$Name, [int]$Attempts = 60) {
    for ($attempt = 0; $attempt -lt $Attempts; $attempt++) {
        if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
            Write-Output "$Name ready"
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "$Name did not start listening on port $Port"
}
function Wait-LegendHttp([string]$Url,[string]$Name) {
    $lastReason='not ready'
    for($attempt=0;$attempt -lt 30;$attempt++){
        try { $reply=Invoke-WebRequest -Uri $Url -TimeoutSec 2;if($reply.StatusCode -eq 200){Write-Output "$Name ready";return} }
        catch {$lastReason=$_.Exception.Message}
        Start-Sleep -Milliseconds 500
    }
    throw "$Name did not become ready: $lastReason"
}
