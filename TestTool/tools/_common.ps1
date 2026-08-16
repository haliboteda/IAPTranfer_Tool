# Shared by everything under tools/. Dot-source it first:
#     . "$PSScriptRoot\_common.ps1"

$ErrorActionPreference = "Continue"

$cfg = Join-Path $PSScriptRoot "..\config\machine.ps1"
if (-not (Test-Path $cfg)) {
    Write-Host "config/machine.ps1 is missing." -ForegroundColor Red
    Write-Host "Copy config/machine.example.ps1 to config/machine.ps1 and fill in this machine's paths." -ForegroundColor Yellow
    exit 1
}
. $cfg

function Section($t) { Write-Host ""; Write-Host "===== $t" -ForegroundColor Cyan }
function Ok($t)      { Write-Host $t -ForegroundColor Green }
function Warn($t)    { Write-Host $t -ForegroundColor Yellow }
function Fail($t)    { Write-Host $t -ForegroundColor Red }

# STM32_Programmer_CLI lives in a versioned plugin directory, so resolve it by
# wildcard and take the newest. Hardcoding the version breaks on every CubeIDE
# update -- which is the class of thing config/ exists to avoid.
function Get-ProgrammerCli {
    $glob = Join-Path $CUBEIDE "STM32CubeIDE\plugins\com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.win32_*\tools\bin\STM32_Programmer_CLI.exe"
    $hit = Get-ChildItem $glob -ErrorAction SilentlyContinue | Sort-Object FullName | Select-Object -Last 1
    if (-not $hit) { Fail "STM32_Programmer_CLI not found under $CUBEIDE"; exit 1 }
    return $hit.FullName
}

function Get-CubeIdeExe {
    $exe = Join-Path $CUBEIDE "STM32CubeIDE\stm32cubeidec.exe"
    if (-not (Test-Path $exe)) { Fail "stm32cubeidec.exe not found at $exe"; exit 1 }
    return $exe
}

# Which process is holding a COM port. Windows offers no direct answer, so this
# is a best-effort name match against the usual serial terminals -- enough to
# say "close sscom" instead of "access denied".
function Get-PortHolderHint {
    $known = @("sscom", "putty", "xshell", "securecrt", "mobaxterm", "ttermpro", "teraterm",
               "realterm", "termite", "hterm", "AccessPort", "XCOM", "UartAssist", "arduino")
    $hits = Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $n = $_.ProcessName.ToLower(); $known | Where-Object { $n -like "*$_*" }
    }
    if ($hits) { return ($hits | ForEach-Object { "$($_.ProcessName) (PID $($_.Id))" }) -join ", " }
    return $null
}

# Open every port in $Ports that can be opened; report the ones that cannot and
# name the likely culprit. Returns a hashtable of name -> SerialPort.
function Open-LogPorts([string[]]$Ports) {
    $open = @{}
    foreach ($p in $Ports) {
        if (-not $p) { continue }
        try {
            $h = New-Object System.IO.Ports.SerialPort $p, $LOG_BAUD, 'None', 8, 'One'
            $h.ReadTimeout = 300
            $h.Open()
            $open[$p] = $h
            Write-Host "listening on $p @ $LOG_BAUD"
        } catch {
            $msg = $_.Exception.Message
            Warn "cannot open ${p}: $msg"
            if ($msg -match "denied") {
                $who = Get-PortHolderHint
                if ($who) { Warn "  a serial terminal is holding it: $who -- close it and retry" }
            }
        }
    }
    return $open
}

# Drain the given ports for $Seconds and return name -> captured text.
function Read-LogPorts($Open, [int]$Seconds) {
    $buf = @{}; foreach ($k in $Open.Keys) { $buf[$k] = "" }
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        foreach ($k in @($Open.Keys)) {
            try { if ($Open[$k].BytesToRead -gt 0) { $buf[$k] += $Open[$k].ReadExisting() } } catch {}
        }
        Start-Sleep -Milliseconds 100
    }
    foreach ($k in @($Open.Keys)) { try { $Open[$k].Close() } catch {} }
    return $buf
}

# Refuses to go further when SWD cannot reach the MCU, and says why. A target
# voltage of 0.00V means the board is unpowered or ST-Link VTREF is not wired --
# VTREF being the one people forget while the other three lines are all correct.
function Assert-TargetReachable($Cli) {
    $probe = & $Cli -c port=SWD mode=HOTPLUG 2>&1
    $m = ($probe | Select-String -Pattern "Voltage\s*:\s*(.+)$")
    $volt = if ($m) { $m.Matches.Groups[1].Value.Trim() } else { "unknown" }
    Write-Host "target voltage: $volt"
    if ($probe -match "No STM32 target found") {
        Fail "SWD cannot reach the MCU."
        if ($volt -match "^0\.00") {
            Warn "  0.00V -> board unpowered, or ST-Link VTREF/VDD not wired."
        }
        exit 1
    }
}
