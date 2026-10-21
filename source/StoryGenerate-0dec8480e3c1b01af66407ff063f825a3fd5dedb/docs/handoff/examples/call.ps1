param(
    [Parameter(Mandatory=$true)][ValidateSet('GET','POST','PATCH')][string]$Method,
    [Parameter(Mandatory=$true)][string]$Path,
    [string]$BodyFile,
    [string]$OutputFile
)
$ErrorActionPreference = 'Stop'
$base = $env:STORY_API_URL
if (-not $base) { $base = 'http://127.0.0.1:8000' }
$headers = @{}
if ($env:STORY_API_KEY) { $headers.Authorization = 'Bearer ' + $env:STORY_API_KEY }
$params = @{ Method=$Method; Uri=($base.TrimEnd('/')+$Path); Headers=$headers; TimeoutSec=30 }
if ($BodyFile) {
    $params.Body = [System.Text.Encoding]::UTF8.GetBytes((Get-Content -LiteralPath $BodyFile -Raw -Encoding UTF8))
    $params.ContentType = 'application/json; charset=utf-8'
}
$response = Invoke-WebRequest @params
if ($OutputFile) { [IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputFile), $response.Content, [Text.UTF8Encoding]::new($false)) }
$response.Content
