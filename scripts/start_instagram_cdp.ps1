[CmdletBinding()]
param(
    [int]$Port = 9333,
    [string]$ProfileDir,
    [string]$ChromePath,
    [ValidateRange(1, 60)][int]$WaitSeconds = 15
)

$ErrorActionPreference = "Stop"
if (-not $ProfileDir) { $ProfileDir = Join-Path (Split-Path $PSScriptRoot -Parent) "outputs\instagram-cdp-profile" }
function Test-CdpPort([int]$LocalPort) {
    return @(Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue).Count -gt 0
}
function Write-JsonResult([hashtable]$Value, [int]$ExitCode = 0) {
    $Value | ConvertTo-Json -Compress -Depth 5
    exit $ExitCode
}

$ProfileDir = [IO.Path]::GetFullPath($ProfileDir)
if (Test-CdpPort $Port) {
    Write-JsonResult @{status="ready"; port=$Port; already_listening=$true; started=$false; profile_dir=$ProfileDir; extension_id=$null}
}
$profileUsers = @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and $_.CommandLine.IndexOf($ProfileDir, [StringComparison]::OrdinalIgnoreCase) -ge 0
})
if ($profileUsers.Count) {
    Write-JsonResult @{status="profile_locked"; port=$Port; profile_dir=$ProfileDir; processes=@($profileUsers.ProcessId); action="Close only this dedicated Instagram Chrome window, then rerun."} 3
}
if (-not $ChromePath) {
    $ChromePath = @(
        (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
        (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
}
if (-not $ChromePath) { Write-JsonResult @{status="chrome_not_found"; port=$Port; profile_dir=$ProfileDir} 2 }
New-Item -ItemType Directory -Path $ProfileDir -Force | Out-Null
$arguments = @(
    "--remote-debugging-port=$Port",
    "--remote-debugging-address=127.0.0.1",
    "--user-data-dir=`"$ProfileDir`"",
    "--no-first-run",
    "--disable-extensions",
    "about:blank"
)
$process = Start-Process -FilePath $ChromePath -ArgumentList $arguments -PassThru
for ($second=1; $second -le $WaitSeconds; $second++) {
    Start-Sleep 1
    if (Test-CdpPort $Port) {
        Write-JsonResult @{status="ready"; port=$Port; already_listening=$false; started=$true; process_id=$process.Id; profile_dir=$ProfileDir; waited_seconds=$second; extension_id=$null}
    }
}
Write-JsonResult @{status="cdp_not_listening"; port=$Port; started=$true; profile_dir=$ProfileDir; process_id=$process.Id} 5
