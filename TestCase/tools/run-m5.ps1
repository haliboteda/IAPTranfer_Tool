# M5 / E7 -- builds, flashes and drives the Serial_Test-vs-Serial conflict case.
#
#   .\run-m5.ps1                 build, flash, then test
#   .\run-m5.ps1 -SkipFlash      the sketch is already on the board, just test
#
# What it proves: after the user's sketch calls Serial.begin(), the core's
# diagnostic port Serial_Test can still RECEIVE.
#
# ⚠️ Receive, not transmit. In the broken configuration Serial_Test still
# prints perfectly -- both objects resolve to UART4, uart_handlers[] keeps one
# handler per peripheral, and the loser only loses its RX path. A check that
# watches for output alone passes on a board that is broken.
#
# So the test writes a byte INTO the RS232 terminal (C06) and waits for the
# sketch to echo it back. No echo = the diagnostic port is deaf = E7 fails.
#
# Exit 0 = Serial_Test received, 1 = it did not, 2 = setup problem.

param(
    [switch]$SkipFlash,
    [string]$Ip,
    [string]$Port,
    [int]$Bytes = 5
)

. "$PSScriptRoot/_common.ps1"

if (-not $Ip)   { $Ip = $BOARD_IP }
if (-not $Port) { $Port = $LOG_PORTS[0] }

$sketch = Join-Path $TOOL_REPO "TestCase/onboard/rs232/M5_SerialConflict"
if (-not (Test-Path $sketch)) { Fail "sketch not found: $sketch"; exit 2 }

# ---------------------------------------------------------------- build ------

if (-not $SkipFlash) {
    if (-not $ARDUINO_CLI -or -not (Test-Path $ARDUINO_CLI)) {
        Fail "arduino-cli not found. Set `$ARDUINO_CLI in config/machine.ps1"; exit 2
    }
    if (-not $Ip) { Fail "need -Ip (or set BOARD_IP in config/machine.ps1)"; exit 2 }

    $fqbn = "OpenPLC_Alpha:stm32:OPEN-PLC:pnum=PLC_H743,usb=CDCgen,xusb=FS,upload_method=cdcMethod,knxrole=dual_device,downgrade=refuse"
    $buildPath = Join-Path ([System.IO.Path]::GetTempPath()) "m5_build"

    Section "building"
    & $ARDUINO_CLI compile --warnings all --config-file $ARDUINO_CLI_CONFIG `
        --fqbn $fqbn --build-path $buildPath $sketch 2>&1 | Select-Object -Last 3
    if ($LASTEXITCODE -ne 0) { Fail "compile failed"; exit 2 }

    $bin = Join-Path $buildPath "M5_SerialConflict.ino.bin"
    if (-not (Test-Path $bin)) { Fail "no .bin at $bin"; exit 2 }

    Section "flashing"
    $iap = Get-GoBin "IAPTool"
    if (-not (Test-Path $iap)) { $iap = Get-IapTool }

    # ⚠️ IAPTool exits when the last byte is sent; the board is only then
    # verifying, erasing and writing from SDRAM. Resetting or testing here lands
    # mid-write and destroys the application. Wait for the board to say so.
    $open = Open-LogPorts @($Port)
    $p = Start-Process -FilePath $iap -ArgumentList @("ether", $bin, $Ip, "--downgrade=allow") `
        -NoNewWindow -PassThru -RedirectStandardOutput (Get-ScratchFile "m5_flash.out") `
        -RedirectStandardError (Get-ScratchFile "m5_flash.err")
    $log = ""
    while (-not $p.HasExited) {
        try { if ($open[$Port].BytesToRead -gt 0) { $log += $open[$Port].ReadExisting() } } catch {}
        Start-Sleep -Milliseconds 50
    }
    $tail = Read-LogPorts $open 15
    foreach ($k in $tail.Keys) { $log += $tail[$k] }

    if ($log -notmatch "Checksum and signature OK") {
        Fail "the board did not accept the image"
        ($log -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 8) | ForEach-Object { Write-Host "    $_" }
        exit 2
    }
    Ok "flashed and accepted"
}

# ----------------------------------------------------------------- test ------

Section "can Serial_Test still RECEIVE after Serial.begin()?"

$sp = New-Object System.IO.Ports.SerialPort $Port, $LOG_BAUD, 'None', 8, 'One'
$sp.ReadTimeout = 500
try { $sp.Open() } catch { Fail "cannot open ${Port}: $($_.Exception.Message)"; exit 2 }

# Let the sketch get to its banner, and prove the port is alive at all: if the
# banner is missing the board is not running this sketch and nothing below
# means anything.
Start-Sleep -Milliseconds 1500
$banner = ""
try { $banner = $sp.ReadExisting() } catch {}
if ($banner -notmatch "\[M5\] ready") {
    Warn "no '[M5] ready' banner seen yet; resetting the board to catch it"
    & (Get-ProgrammerCli) -c port=SWD mode=UR -rst 2>&1 | Out-Null
    Start-Sleep -Milliseconds 2500
    try { $banner += $sp.ReadExisting() } catch {}
}
($banner -split "`r?`n" | Where-Object { $_.Trim() }) | ForEach-Object { Write-Host "    | $_" }

if ($banner -notmatch "\[M5\] ready") {
    $sp.Close()
    if ($SkipFlash) {
        Fail "no banner, and this run did not flash -- is the M5 sketch actually on the board?"
        exit 2
    }
    # The sketch was flashed and accepted moments ago, so it IS running. Silence
    # here is the failure itself, not a setup problem: Serial_Test lost its
    # transmit path as well, which is worse than the documented symptom.
    Fail "E7 FAILS: Serial_Test went silent entirely after Serial4.begin()."
    Fail "Not even the banner survived -- transmit died too, not just receive."
    Fail "(the boot log above is cut off mid-line, which is that happening)"
    exit 1
}
Ok "transmit works (banner received) -- now testing receive"

$sent = 0
$echoed = 0
foreach ($c in @('A', 'B', 'C', 'D', 'E')[0..([Math]::Min($Bytes, 5) - 1)]) {
    try { $sp.Write($c) } catch { Fail "cannot write to $Port : $($_.Exception.Message)"; $sp.Close(); exit 2 }
    $sent++
    Start-Sleep -Milliseconds 400
    $reply = ""
    try { $reply = $sp.ReadExisting() } catch {}
    if ($reply -match ("\[echo\]\s*" + $c)) {
        Ok ("    sent '{0}' -> echoed" -f $c)
        $echoed++
    } else {
        Fail ("    sent '{0}' -> NOTHING came back ({1})" -f $c, ($reply -replace "`r?`n", " "))
    }
}
$sp.Close()

Section "result"
Write-Host "  bytes sent: $sent, echoed: $echoed"
if ($echoed -eq $sent) {
    Ok "E7 holds: Serial_Test still receives after the sketch opened Serial"
    exit 0
}
if ($echoed -eq 0) {
    Fail "E7 FAILS: Serial_Test transmits but is completely deaf."
    Fail "This is the documented symptom of Serial_Test and Serial sharing UART4."
    exit 1
}
Fail "E7 FAILS intermittently: $echoed of $sent echoed -- worse than a clean failure"
exit 1
