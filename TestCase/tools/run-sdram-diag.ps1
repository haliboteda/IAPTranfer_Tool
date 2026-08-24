# Runs the bootloader's SDRAM data-bus diagnostic and captures its output.
#
#   .\run-sdram-diag.ps1                 the full report (census, dwell, release time)
#   .\run-sdram-diag.ps1 -Live 120       one error-rate line per second, for 120 s
#   .\run-sdram-diag.ps1 -Out diag.txt   also write the capture to a file
#
# Why this exists: the SDRAM data bus on the Bridge board has no probe point --
# both ends of every DQ net sit under a BGA, and there is no series resistor or
# test pad anywhere on the bus. Firmware is the only instrument available. See
# open_plc_cube_ide/docs/design/HARDWARE-FACTS.md and docs/work/investigations/sdram-d1-report.html.
#
# -Live is the freeze-spray / hot-air test: someone cools or heats ONE package
# while this prints the error rate every second. A rate that follows the can
# names the package; a rate that ignores both points at the trace between them.
#
# The command goes out over TCP, but the report comes back on the SERIAL console
# (the diagnostic uses printf, like every other bootloader log line), so both
# channels are needed. The board must be sitting in the bootloader -- a board
# running its application never initialises the FMC.
#
# Exit 0 = a report was captured, 1 = nothing came back, 2 = setup problem.

param(
    [int]$Live = 0,
    [int]$Seconds = 0,
    [string]$Ip,
    [string]$Port = "56865",
    [string[]]$Ports,
    [string]$Out
)

. "$PSScriptRoot/_common.ps1"

if (-not $Ip) { $Ip = $BOARD_IP }
if (-not $Ip) { Fail "need -Ip (or set BOARD_IP in config/machine.ps1)"; exit 2 }
if (-not $Ports) { $Ports = $LOG_PORTS }
if (-not $Ports) { Fail "no log ports configured - set LOG_PORTS in config/machine.ps1"; exit 2 }

# The full report takes a few seconds of censuses; live mode needs the whole
# window plus the board's own startup slack.
if ($Seconds -le 0) { $Seconds = if ($Live -gt 0) { $Live + 8 } else { 25 } }
$cmd = if ($Live -gt 0) { "sdramlive $Live" } else { "sdramdiag" }

Section "Command"
Write-Host "  sending `"$cmd`" to $Ip`:$Port, capturing $Seconds s on $($Ports -join ', ')"

# Open the serial ports BEFORE sending: the firmware starts printing as soon as
# it has answered, and a port opened afterwards loses the first lines.
$open = Open-LogPorts $Ports
if ($open.Count -eq 0) { Fail "no log port could be opened - is a terminal holding it?"; exit 2 }

$client = New-Object System.Net.Sockets.TcpClient
try {
    $client.Connect($Ip, [int]$Port)
    $s = $client.GetStream()
    $s.ReadTimeout = 4000
    $b = [Text.Encoding]::ASCII.GetBytes($cmd + "`n")
    $s.Write($b, 0, $b.Length)
    $buf = New-Object byte[] 256
    $n = 0
    try { $n = $s.Read($buf, 0, $buf.Length) } catch {}
    $ack = [Text.Encoding]::ASCII.GetString($buf, 0, $n).Trim()
    if ($ack -ne "OK") {
        Warn "the board answered `"$ack`" instead of OK - an older bootloader has no sdramdiag command"
    }
} catch {
    Fail "could not reach the command port: $($_.Exception.Message)"
    Fail "  the board must be in the bootloader (a running application does not serve this port)"
    exit 2
} finally { $client.Close() }

if ($Live -gt 0) {
    Write-Host ""
    Write-Host "  >>> cool or heat ONE package now (U6 = SDRAM, U1 = MCU), one at a time <<<"
    Write-Host ""
}

$cap = Read-LogPorts $open $Seconds
$all = ($cap.Values -join "`n")

foreach ($k in $cap.Keys) {
    if ($cap[$k].Length -gt 0) {
        Section "$k  ($($cap[$k].Length) bytes)"
        Write-Host $cap[$k]
    }
}

if ($Out) {
    Set-Content -Path $Out -Value $all -Encoding utf8
    Write-Host ""
    Write-Host "  capture written to $Out"
}

Section "Verdict"
if ($all -match "end of diagnostic") {
    if ($all -match "VERDICT: (.+)") { Ok "report captured - $($Matches[1])" } else { Ok "report captured" }
    exit 0
} elseif ($all -match "live done") {
    Ok "live run finished - compare the rate against what was cooled or heated when"
    exit 0
} elseif ($all -match "SDRAM DIAG|t=\s*\d+s") {
    Warn "output started but the end marker never arrived - raise -Seconds"
    exit 1
} else {
    Fail "nothing came back on the serial console"
    Fail "  a board that booted its application never runs the FMC init, so there is"
    Fail "  nothing to measure - hold BOOT0 through startup and try again"
    exit 1
}
