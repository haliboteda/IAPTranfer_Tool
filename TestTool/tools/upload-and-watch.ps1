# Run a real upload through IAPTool while capturing the board's serial log, then
# judge it against what SDRAM staging is supposed to do.
#
#   .\upload-and-watch.ps1 -Bin <file.bin>              over ethernet, IP from config
#   .\upload-and-watch.ps1 -Bin <file.bin> -Ip 1.2.3.4
#   .\upload-and-watch.ps1 -Bin <file.bin> -Cdc COM6    over USB CDC
#   .\upload-and-watch.ps1 -Bin <file.bin> -Downgrade allow
#
# The upload is driven by the shipping IAPTool, not by a reimplementation here:
# what gets exercised has to be the code path customers use.

param(
    [Parameter(Mandatory = $true)][string]$Bin,
    [string]$Ip,
    [string]$Cdc,
    [string]$Downgrade = "allow",
    [int]$TailSeconds = 6,
    [string[]]$Ports
)

. "$PSScriptRoot/_common.ps1"

if (-not $Ports) { $Ports = $LOG_PORTS }
if (-not $Ip -and -not $Cdc) { $Ip = $BOARD_IP }
if (-not $Ip -and -not $Cdc) { Fail "need -Ip or -Cdc (or set BOARD_IP / CDC_PORT in config)"; exit 1 }
if (-not (Test-Path $Bin)) { Fail "no such image: $Bin"; exit 1 }
$iapTool = Get-IapTool

# The CDC port is the board talking to us; it cannot also be a passive log port.
if ($Cdc) { $Ports = $Ports | Where-Object { $_ -ne $Cdc } }

Write-Host ("image: {0}  ({1:N0} B)" -f $Bin, (Get-Item $Bin).Length)

Section "Log ports"
$open = Open-LogPorts $Ports

Section "Upload"
$argv = if ($Cdc) { @("cdc", $Bin, $Cdc) } else { @("ether", $Bin, $Ip) }
$argv += "--downgrade=$Downgrade"
Write-Host "IAPTool $($argv -join ' ')"

# Drain the serial port while IAPTool runs. Without this the driver's buffer
# overruns on a long upload and the interesting lines are the ones lost.
$iapOut = Get-ScratchFile "iaptool.out"
$iapErr = Get-ScratchFile "iaptool.err"
$proc = Start-Process -FilePath $iapTool -ArgumentList $argv -NoNewWindow -PassThru `
    -RedirectStandardOutput $iapOut -RedirectStandardError $iapErr

$buf = @{}; foreach ($k in $open.Keys) { $buf[$k] = "" }
while (-not $proc.HasExited) {
    foreach ($k in @($open.Keys)) {
        try { if ($open[$k].BytesToRead -gt 0) { $buf[$k] += $open[$k].ReadExisting() } } catch {}
    }
    Start-Sleep -Milliseconds 60
}
# The board reboots into the application after a good upload, so keep listening.
$deadline = (Get-Date).AddSeconds($TailSeconds)
while ((Get-Date) -lt $deadline) {
    foreach ($k in @($open.Keys)) {
        try { if ($open[$k].BytesToRead -gt 0) { $buf[$k] += $open[$k].ReadExisting() } } catch {}
    }
    Start-Sleep -Milliseconds 60
}
foreach ($k in @($open.Keys)) { try { $open[$k].Close() } catch {} }

# HasExited going true is not enough for ExitCode to be readable on a -PassThru
# process object; without this the verdict below reports a blank exit code.
$proc.WaitForExit()

Section "IAPTool output (exit $($proc.ExitCode))"
Get-Content $iapOut -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
Get-Content $iapErr -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }

foreach ($k in $buf.Keys) {
    Section "$k  ($($buf[$k].Length) bytes)"
    if ($buf[$k].Length -gt 0) { Write-Host $buf[$k] }
}

# ---------------------------------------------------------------- verdict
Section "Verdict"
$all = ($buf.Values -join "`n")

if ($all -match "SDRAM staging buffer OK") { Ok "  self-test: staging buffer usable" }
elseif ($all -match "SDRAM SELF-TEST FAILED") { Fail "  self-test: FAILED" }
else { Warn "  self-test line not seen (did the board stay in the bootloader?)" }

if ($all -match "Staging in SDRAM") { Ok "  staged instead of erasing up front" }
else { Warn "  no 'Staging in SDRAM' - is this the new bootloader?" }

# The whole point of staging: the erase must come after verification, not before.
$iErase = $all.IndexOf("Erasing application region")
$iVerif = $all.IndexOf("Transfer complete, verifying")
if ($iErase -ge 0 -and $iVerif -ge 0 -and $iErase -gt $iVerif) {
    Ok "  erase happened AFTER verification - this is the change working"
} elseif ($iErase -ge 0 -and $iVerif -lt 0) {
    Warn "  erased without a verification line before it"
} elseif ($iErase -lt 0) {
    Warn "  no erase line - upload did not reach the commit step"
}

if ($proc.ExitCode -eq 0) { Ok "  IAPTool exit 0" } else { Fail "  IAPTool exit $($proc.ExitCode)" }
