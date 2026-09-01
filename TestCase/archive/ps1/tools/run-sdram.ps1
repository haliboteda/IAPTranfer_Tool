# M3 / E5 -- builds, flashes and reads out the SDRAM wrapper acceptance sketch.
#
#   .\run-sdram.ps1              build, flash, collect
#   .\run-sdram.ps1 -SkipFlash   the sketch is already on the board
#
# The sketch prints "RESULT <name> PASS|FAIL" lines and "MEASURE <name> <n>"
# lines. This script fails on any FAIL, on a missing DONE (which means the
# sketch stopped early -- a hang or a fault), and on zero results collected.
#
# Exit 0 = every check passed, 1 = something failed, 2 = setup problem.

param(
    [switch]$SkipFlash,
    [string]$Ip,
    [string]$Port,
    [int]$CollectSeconds = 25
)

. "$PSScriptRoot/_common.ps1"

if (-not $Ip)   { $Ip = $BOARD_IP }
if (-not $Port) { $Port = $LOG_PORTS[0] }

$sketch = Join-Path $TOOL_REPO "TestCase/onboard/sdram/SDRAM_Acceptance"
if (-not (Test-Path $sketch)) { Fail "sketch not found: $sketch"; exit 2 }

if (-not $SkipFlash) {
    if (-not $ARDUINO_CLI -or -not (Test-Path $ARDUINO_CLI)) {
        Fail "arduino-cli not found. Set `$ARDUINO_CLI in config/machine.ps1"; exit 2
    }
    if (-not $Ip) { Fail "need -Ip (or set BOARD_IP in config/machine.ps1)"; exit 2 }

    $fqbn = "OpenPLC_Alpha:stm32:OPEN-PLC:pnum=PLC_H743,usb=CDCgen,xusb=FS,upload_method=cdcMethod,knxrole=dual_device,downgrade=refuse"
    $buildPath = Join-Path ([System.IO.Path]::GetTempPath()) "sdram_build"

    Section "building"
    $out = & $ARDUINO_CLI compile --warnings all --config-file $ARDUINO_CLI_CONFIG `
        --fqbn $fqbn --build-path $buildPath $sketch 2>&1
    if ($LASTEXITCODE -ne 0) {
        Fail "compile failed"
        ($out | Where-Object { $_ -match "error" }) | ForEach-Object { Write-Host "    $_" }
        exit 2
    }
    ($out | Where-Object { $_ -match "program storage|dynamic memory" }) | ForEach-Object { Write-Host "  $_" }

    $bin = Join-Path $buildPath "SDRAM_Acceptance.ino.bin"
    if (-not (Test-Path $bin)) { Fail "no .bin at $bin"; exit 2 }

    # ⚠️ The .bin must not grow by the size of the buffers. That is the whole
    # point of not using a linker section -- report it so a regression is
    # visible rather than merely absent.
    Write-Host ("  image size: {0} bytes" -f (Get-Item $bin).Length)

    Section "flashing"
    $iap = Get-GoBin "IAPTool"
    if (-not (Test-Path $iap)) { $iap = Get-IapTool }

    # Hold the port open across the whole flash: IAPTool exits before the board
    # has finished writing, and the sketch's output starts right after the
    # board reboots itself.
    $open = Open-LogPorts @($Port)
    $p = Start-Process -FilePath $iap -ArgumentList @("ether", $bin, $Ip, "--downgrade=allow") `
        -NoNewWindow -PassThru -RedirectStandardOutput (Get-ScratchFile "sdram_flash.out") `
        -RedirectStandardError (Get-ScratchFile "sdram_flash.err")
    $log = ""
    while (-not $p.HasExited) {
        try { if ($open[$Port].BytesToRead -gt 0) { $log += $open[$Port].ReadExisting() } } catch {}
        Start-Sleep -Milliseconds 50
    }
    $tail = Read-LogPorts $open $CollectSeconds
    foreach ($k in $tail.Keys) { $log += $tail[$k] }

    if ($log -notmatch "Checksum and signature OK") {
        Fail "the board did not accept the image"
        ($log -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 8) | ForEach-Object { Write-Host "    $_" }
        exit 2
    }
    $serial = $log
} else {
    Section "collecting (reset to restart the sketch)"
    $open = Open-LogPorts @($Port)
    & (Get-ProgrammerCli) -c port=SWD mode=UR -rst 2>&1 | Out-Null
    $buf = Read-LogPorts $open $CollectSeconds
    $serial = ($buf.Values -join "`n")
}

Section "board said"
($serial -split "`r?`n" | Where-Object { $_ -match "RESULT|MEASURE|DONE|SDRAM|===" }) |
    ForEach-Object { Write-Host "    | $_" }

# ---------------------------------------------------------------- verdict ----

$results = [regex]::Matches($serial, "RESULT\s+(\S+)\s+(PASS|FAIL)")
$failed = @($results | Where-Object { $_.Groups[2].Value -eq "FAIL" })

Section "measurements"
foreach ($m in [regex]::Matches($serial, "MEASURE\s+(\S+)\s+(\d+)")) {
    Write-Host ("  {0,-14} {1}" -f $m.Groups[1].Value, $m.Groups[2].Value)
}
$zb = [regex]::Match($serial, "MEASURE zero_bytes (\d+)")
$zu = [regex]::Match($serial, "MEASURE zero_us (\d+)")
if ($zb.Success -and $zu.Success -and [double]$zu.Groups[1].Value -gt 0) {
    $mb = [double]$zb.Groups[1].Value / 1MB
    $ms = [double]$zu.Groups[1].Value / 1000.0
    Write-Host ("  -> zeroing {0:N0} MB took {1:N1} ms ({2:N0} MB/s)" -f $mb, $ms, ($mb / ($ms / 1000.0)))
    Write-Host ("  -> extrapolated for the full 64 MB: {0:N0} ms" -f ($ms * 64.0 / $mb))
}

Section "result"
Write-Host ("  checks: {0} run, {1} failed" -f $results.Count, $failed.Count)
if ($results.Count -eq 0) {
    Fail "no RESULT lines at all -- the sketch never ran, or the port is wrong"
    exit 1
}
if ($serial -notmatch "DONE") {
    Fail "no DONE line -- the sketch stopped partway (hang or fault)"
    exit 1
}
if ($failed.Count -gt 0) {
    $failed | ForEach-Object { Fail ("  FAILED: " + $_.Groups[1].Value) }
    exit 1
}
Ok "all $($results.Count) SDRAM checks passed"
exit 0
