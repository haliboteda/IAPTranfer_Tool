# Leave the board sitting in the bootloader, which T1-T4 and S1 all require.
#
#   .\enter-bootloader.ps1
#
# How: hand IAPTool an image larger than the application region. IAPTool reboots
# the running application into the bootloader as its first step, and only then
# offers the image -- which the board refuses at the size check, before erasing
# or staging anything. So the board ends up in the bootloader with nothing
# written.
#
# Every step here is shipping code: IAPTool's real authenticated reboot, and the
# board's real size check (IAPServer/IAP_server.c:206). Nothing about the
# protocol is reimplemented, which is the whole reason to do it this way rather
# than poking the SRAM4 handoff record over SWD.
#
# The alternative is holding BOOT0 through the startup window, which needs hands
# on the board.

param(
    [string]$Ip,
    [int]$Seconds = 6,
    [string[]]$Ports
)

. "$PSScriptRoot/_common.ps1"

if (-not $Ports) { $Ports = $LOG_PORTS }
if (-not $Ip)    { $Ip = $BOARD_IP }
if (-not $Ip)    { Fail "need -Ip (or set BOARD_IP in config)"; exit 1 }

# Anything over IAP_APP_MAX_SIZE (1,835,008) does; the content is never read.
$big = Get-ScratchFile "testtool_oversize.bin"
if (-not (Test-Path $big) -or (Get-Item $big).Length -lt 2000000) {
    $fs = [System.IO.File]::Create($big); $fs.SetLength(2000000); $fs.Close()
}

Section "Requesting bootloader"
$open = Open-LogPorts $Ports

$ebOutFile = Get-ScratchFile "eb.out"
$ebErrFile = Get-ScratchFile "eb.err"
$proc = Start-Process -FilePath (Get-IapTool) -ArgumentList @("ether", $big, $Ip, "--downgrade=allow") `
    -NoNewWindow -PassThru -RedirectStandardOutput $ebOutFile -RedirectStandardError $ebErrFile

$buf = @{}; foreach ($k in $open.Keys) { $buf[$k] = "" }
while (-not $proc.HasExited) {
    foreach ($k in @($open.Keys)) {
        try { if ($open[$k].BytesToRead -gt 0) { $buf[$k] += $open[$k].ReadExisting() } } catch {}
    }
    Start-Sleep -Milliseconds 60
}
$buf2 = Read-LogPorts $open $Seconds
foreach ($k in $buf2.Keys) { $buf[$k] += $buf2[$k] }
$proc.WaitForExit()

$all = ($buf.Values -join "`n")
foreach ($k in $buf.Keys) { if ($buf[$k].Length -gt 0) { Section "serial $k"; Write-Host $buf[$k] } }

Section "Verdict"
# The refusal line is the proof that nothing was written, not just that the
# upload failed somewhere.
if ($all -match "Invalid flash size") {
    Ok "board is in the bootloader; the oversized image was refused before anything was touched"
    exit 0
} elseif ($all -match "UPLOAD Mod") {
    Warn "board entered upload mode, but the refusal line was not seen - check the log"
    exit 0
} else {
    Fail "board does not appear to be in the bootloader"
    Get-Content $ebErrFile -ErrorAction SilentlyContinue | Select-Object -Last 5 | ForEach-Object { Write-Host $_ }
    exit 1
}
