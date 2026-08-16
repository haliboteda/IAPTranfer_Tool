# Run one TestTool case while capturing the board's serial log, and optionally
# reset afterwards to see what the board does on the next boot.
#
#   .\run-case.ps1 -Case N1
#   .\run-case.ps1 -Case S1 -Bin <file.bin> -ThenReset
#
# -ThenReset is what turns S1 into G1: S1 proves the board refuses a bad image,
# the reset afterwards proves the previously-installed application still boots.
# Before SDRAM staging that second half was impossible -- a rejected upload had
# already destroyed the running application.

param(
    [Parameter(Mandatory = $true)][string]$Case,
    [string]$Ip,
    [string]$Bin,
    [string]$PasswordFile,
    [string]$IapTool,
    [switch]$ThenReset,
    [int]$ResetWatchSeconds = 8,
    [string[]]$Ports
)

. "$PSScriptRoot\_common.ps1"

if (-not $Ports) { $Ports = $LOG_PORTS }
if (-not $Ip)    { $Ip = $BOARD_IP }
if (-not $Ip)    { Fail "need -Ip (or set BOARD_IP in config)"; exit 1 }
if (-not $IapTool)      { $IapTool = $IAPTOOL }
if (-not $PasswordFile) { $PasswordFile = Join-Path (Split-Path -Parent $IapTool) "keys\iap_fixed_password.txt" }

$TT = Join-Path $TOOL_REPO "Output\windows\TestTool.exe"
if (-not (Test-Path $TT)) { Fail "TestTool.exe not built - run: go build -o Output/windows/TestTool.exe ./TestTool"; exit 1 }

$argv = @($Case, "--ip=$Ip", "--iaptool=$IapTool")
if ($Bin) { $argv += "--bin=$Bin" }
if (Test-Path $PasswordFile) { $argv += "--password-file=$PasswordFile" }

Section "Case $Case"
$open = Open-LogPorts $Ports
Write-Host "TestTool $($argv -join ' ')"

$proc = Start-Process -FilePath $TT -ArgumentList $argv -NoNewWindow -PassThru `
    -RedirectStandardOutput "$env:TEMP\tt.out" -RedirectStandardError "$env:TEMP\tt.err"

$buf = @{}; foreach ($k in $open.Keys) { $buf[$k] = "" }
while (-not $proc.HasExited) {
    foreach ($k in @($open.Keys)) {
        try { if ($open[$k].BytesToRead -gt 0) { $buf[$k] += $open[$k].ReadExisting() } } catch {}
    }
    Start-Sleep -Milliseconds 60
}
Start-Sleep -Milliseconds 800
foreach ($k in @($open.Keys)) {
    try { if ($open[$k].BytesToRead -gt 0) { $buf[$k] += $open[$k].ReadExisting() } } catch {}
    try { $open[$k].Close() } catch {}
}
$proc.WaitForExit()

Section "TestTool output (exit $($proc.ExitCode))"
Get-Content "$env:TEMP\tt.out" -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
Get-Content "$env:TEMP\tt.err" -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }

foreach ($k in $buf.Keys) {
    if ($buf[$k].Length -gt 0) { Section "serial $k"; Write-Host $buf[$k] }
}

if (-not $ThenReset) { exit $proc.ExitCode }

# ---------------------------------------------------------------- G1 half
Section "Reset, then watch the next boot"
$open2 = Open-LogPorts $Ports
& (Get-ProgrammerCli) -c port=SWD mode=UR -rst 2>&1 |
    Select-String -Pattern "Error|Reset" | ForEach-Object { Write-Host $_ }
$buf2 = Read-LogPorts $open2 $ResetWatchSeconds
foreach ($k in $buf2.Keys) {
    if ($buf2[$k].Length -gt 0) { Section "serial $k (after reset)"; Write-Host $buf2[$k] }
}

Section "Verdict"
$boot = ($buf2.Values -join "`n")
if ($boot -match "APP Mod") {
    Ok "PASS - the previously-installed application still boots after a rejected upload."
    Ok "       The application region was never touched. This is SDRAM staging working."
} elseif ($boot -match "no valid application|App signature invalid") {
    Fail "FAIL - the board considers its application invalid, so the upload damaged it."
    Fail "       That is the pre-staging behaviour; staging did not take effect."
} elseif ($boot.Trim().Length -eq 0) {
    Warn "no serial output after reset - cannot judge"
} else {
    Warn "board booted but into neither state cleanly - read the log above"
}
