# Read-only identity checks. Never terminate an unrelated listener or PID.
function Test-LegendExecutable($Entry,[string]$Executable) {
    for($depth=0;$depth -lt 3 -and $Entry;$depth++){
        if($Entry.ExecutablePath -eq $Executable){return $true}
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
function Wait-LegendHttp([string]$Url,[string]$Name) {
    $lastReason='not ready'
    for($attempt=0;$attempt -lt 30;$attempt++){
        try { $reply=Invoke-WebRequest -Uri $Url -TimeoutSec 2;if($reply.StatusCode -eq 200){Write-Output "$Name ready";return} }
        catch {$lastReason=$_.Exception.Message}
        Start-Sleep -Milliseconds 500
    }
    throw "$Name did not become ready: $lastReason"
}
