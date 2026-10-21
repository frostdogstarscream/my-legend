param([string]$Directory)
. "$PSScriptRoot/StoryModel.Common.ps1"
$Directory = Get-StoryModelDirectory $Directory
$profile = Read-StoryModelProfile $Directory
$secret = Read-StoryModelSecret $Directory $profile
$report = [ordered]@{
    checked_at = [DateTime]::UtcNow.ToString('o')
    endpoint = $profile.base_url + '/chat/completions'
    requested_model = $profile.model
    status = 'not_verified'
    http_status = $null
    request_max_tokens = 256
    http_attempts = 1
    model_responses = 0
    json_validated = $false
    app_generation_connected = $false
    full_story_generation_tested = $false
}
try {
    # One tiny synthetic JSON probe, no user story or automatic retries.
    $payload = @{
        model = $profile.model
        messages = @(
            @{ role = 'system'; content = 'Return only a valid JSON object. Do not include markdown or explanation.' },
            @{ role = 'user'; content = 'API connectivity test. Return exactly this JSON: {"ok":true}' }
        )
        response_format = @{ type = 'json_object' }
        max_tokens = 256
        stream = $false
        thinking = @{ type = 'disabled' }
    } | ConvertTo-Json -Depth 8 -Compress
    $headers = @{ Authorization = 'Bearer ' + $secret }
    # Preserve certificate verification and never forward the credential to a redirect.
    $response = Invoke-WebRequest -Uri $report.endpoint -Method Post -Headers $headers -ContentType 'application/json' -Body $payload -UseBasicParsing -TimeoutSec 120 -MaximumRedirection 0
    $report.http_status = [int]$response.StatusCode
    $data = $response.Content | ConvertFrom-Json
    $report.model_responses = 1
    $report.actual_model = ([string]$data.model).Replace($secret, '[redacted]')
    $finish = [string]$data.choices[0].finish_reason
    $report.finish_reason = if ($finish -in @('stop', 'length', 'content_filter', 'tool_calls')) { $finish } else { 'unknown' }
    $parsed = ([string]$data.choices[0].message.content) | ConvertFrom-Json
    $report.json_validated = ($parsed.ok -is [bool]) -and ($parsed.ok -eq $true) -and (@($parsed.PSObject.Properties).Count -eq 1)
    if ($data.PSObject.Properties.Name -contains 'usage') {
        $usage = [ordered]@{}
        foreach ($field in @('prompt_tokens', 'completion_tokens', 'total_tokens')) {
            if ($data.usage.PSObject.Properties.Name -contains $field) { $usage[$field] = [long]$data.usage.$field }
        }
        $report.usage = $usage
    }
    if ($report.json_validated -and $finish -eq 'stop') { $report.status = 'json_probe_passed' }
    else { $report.status = 'model_output_invalid' }
} catch {
    # Provider bodies and exception messages can contain secrets or request text; never log them.
    if ($_.Exception.PSObject.Properties.Name -contains 'Response' -and $null -ne $_.Exception.Response) {
        $report.http_status = [int]$_.Exception.Response.StatusCode
        $report.status = 'http_error'
    } elseif ($report.model_responses -eq 1) { $report.status = 'model_output_invalid' }
    else { $report.status = 'transport_error' }
} finally {
    $secret = $null
    $headers = $null
}
$name = 'probe-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '-' + [Guid]::NewGuid().ToString('N') + '.json'
Write-StoryModelJson (Join-Path $Directory $name) $report
$report | ConvertTo-Json -Depth 8
if ($report.status -ne 'json_probe_passed') { exit 2 }
