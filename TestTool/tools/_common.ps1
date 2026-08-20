# Shared by everything under tools/. Dot-source it first:
#     . "$PSScriptRoot/_common.ps1"

$ErrorActionPreference = "Continue"

# ---------------------------------------------------------------- platform
# $IsWindows/$IsLinux/$IsMacOS exist only in PowerShell 6+. Under Windows
# PowerShell 5.1 they are $null, so testing them directly would classify 5.1 as
# "not Windows" -- the one platform it can only ever be.
if ($PSVersionTable.PSVersion.Major -ge 6) {
    $PLATFORM = if ($IsWindows) { "windows" } elseif ($IsMacOS) { "macos" } else { "linux" }
} else {
    $PLATFORM = "windows"
}

$EXE = if ($PLATFORM -eq "windows") { ".exe" } else { "" }

# Three tool families, three different names for the same three platforms.
# Keeping the mapping here is the whole point: no script below spells any of
# them out.
$GOOS_DIR  = @{ windows = "windows"; linux = "linux";   macos = "darwin"  }[$PLATFORM]  # compile_tool.sh output layout
$A15_DIR   = @{ windows = "win";     linux = "linux";   macos = "macosx"  }[$PLATFORM]  # Arduino15 packages/*/tools/STM32Tools/*/
$CUBE_PLUG = @{ windows = "win32";   linux = "linux64"; macos = "macos64" }[$PLATFORM]  # CubeIDE externaltools plugin suffix

$cfg = Join-Path $PSScriptRoot (Join-Path ".." (Join-Path "config" "machine.ps1"))
if (-not (Test-Path $cfg)) {
    Write-Host "config/machine.ps1 is missing." -ForegroundColor Red
    Write-Host "Generate it -- this machine's paths are detected, not typed:" -ForegroundColor Yellow
    Write-Host "    python3 tools/init_machine.py        (python on Windows)" -ForegroundColor Yellow
    exit 1
}
. $cfg

# Says so when config/machine.ps1 was filled in for the other platform.
#
# The template ships with a Windows value and a commented-out Linux value for
# every path, and asks you to delete the one you are not on. Copy it on Debian
# without editing and every path stays a Windows path -- which shows up in A0 as
# eight unrelated MISSING lines and one actively wrong hint, telling you to check
# a serial adapter when the real answer is that COM5 is not a device name on this
# machine. First Debian run, 2026-08-20, hit exactly that.
#
# Prints nothing when the config matches the platform, so a correct machine's A0
# is unchanged. Kept in step with common.py's warn_config_platform().
function Write-ConfigPlatformWarning {
    $named = @("BOOT_REPO", "CORE_REPO", "TOOL_REPO", "CORE_LIVE", "CUBEIDE", "IDE", "A15")
    $wrong = @()
    foreach ($n in $named) {
        # Get-Variable walks the scope chain, which is what reaches the values
        # machine.ps1 dot-sourced into the calling script.
        $v = (Get-Variable -Name $n -ErrorAction SilentlyContinue).Value
        if ($v -isnot [string] -or -not $v) { continue }
        if ($PLATFORM -eq "windows") {
            if ($v.StartsWith("/")) { $wrong += $n }
        } else {
            if ($v -match '^[A-Za-z]:[\\/]' -or $v.Contains("\")) { $wrong += $n }
        }
    }
    if ($wrong.Count -eq 0) { return }
    $other = if ($PLATFORM -eq "windows") { "Linux" } else { "Windows" }
    Warn ("  config/machine.ps1 still holds {0} paths, but this machine is {1}." -f $other, $PLATFORM)
    Warn ("    {0}" -f ($wrong -join ", "))
    Warn  "    The template carries both; delete the block you are NOT on, or the"
    Warn  "    wrong assignment silently wins. Everything below is downstream of this."
}

function Section($t) { Write-Host ""; Write-Host "===== $t" -ForegroundColor Cyan }
function Ok($t)      { Write-Host $t -ForegroundColor Green }
function Warn($t)    { Write-Host $t -ForegroundColor Yellow }
function Fail($t)    { Write-Host $t -ForegroundColor Red }

# ---------------------------------------------------------------- paths
# Scratch files (redirected stdout, oversized test images, phase-1 state). $TEMP
# is Windows-only; on Linux and macOS the variable is empty, which would silently
# turn "$env:TEMP/x.out" into "/x.out" -- a write to the filesystem root.
function Get-ScratchDir {
    foreach ($d in @($env:TEMP, $env:TMPDIR, "/tmp")) {
        if ($d -and (Test-Path $d)) { return $d }
    }
    throw "no writable scratch directory (TEMP, TMPDIR, /tmp all unusable)"
}

function Get-ScratchFile([string]$Name) { Join-Path (Get-ScratchDir) $Name }

# Where compile_tool.sh and `go build -o Output/...` put binaries for THIS host.
function Get-GoBin([string]$Name) {
    Join-Path $TOOL_REPO (Join-Path "Output" (Join-Path $GOOS_DIR "$Name$EXE"))
}

# IAPTool ships inside the Arduino board package, one directory per platform. Its
# version is independent of the core's, so resolve both by wildcard: a package
# update must not require editing config/.
function Get-IapTool {
    if ($IAPTOOL -and (Test-Path $IAPTOOL)) { return $IAPTOOL }
    if (-not $A15) { Fail "IAPTOOL not set and A15 not set in config/machine.ps1"; exit 1 }
    $glob = Join-Path $A15 (Join-Path "packages" (Join-Path "OpenPLC_Alpha" (Join-Path "tools" (Join-Path "STM32Tools" (Join-Path "*" (Join-Path $A15_DIR "IAPTool$EXE"))))))
    $hit = Get-ChildItem $glob -ErrorAction SilentlyContinue | Sort-Object FullName | Select-Object -Last 1
    if (-not $hit) { Fail "IAPTool not found under $A15 for platform '$PLATFORM' (looked in .../STM32Tools/*/$A15_DIR/)"; exit 1 }
    return $hit.FullName
}

# STM32_Programmer_CLI lives in a versioned plugin directory, so resolve it by
# wildcard and take the newest. Hardcoding the version breaks on every CubeIDE
# update -- which is the class of thing config/ exists to avoid.
#
# The non-Windows plugin suffixes are ST's documented naming and are NOT verified
# on this machine; if the lookup fails on Linux, the printed glob is the thing to
# compare against the real install.
function Get-ProgrammerCli {
    $leaf = Join-Path "tools" (Join-Path "bin" "STM32_Programmer_CLI$EXE")
    $glob = Join-Path $CUBEIDE (Join-Path "STM32CubeIDE" (Join-Path "plugins" (Join-Path "com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.${CUBE_PLUG}_*" $leaf)))
    $hit = Get-ChildItem $glob -ErrorAction SilentlyContinue | Sort-Object FullName | Select-Object -Last 1
    if (-not $hit) { Fail "STM32_Programmer_CLI not found. Looked for: $glob"; exit 1 }
    return $hit.FullName
}

# The headless launcher, whose name differs per platform: stm32cubeidec.exe on
# Windows, stm32cubeide elsewhere.
function Get-CubeIdeExe {
    $name = if ($PLATFORM -eq "windows") { "stm32cubeidec.exe" } else { "stm32cubeide" }
    $exe = Join-Path $CUBEIDE (Join-Path "STM32CubeIDE" $name)
    if (-not (Test-Path $exe)) { Fail "$name not found at $exe"; exit 1 }
    return $exe
}

# ---------------------------------------------------------------- serial
# System.IO.Ports ships with Windows PowerShell but is an out-of-band assembly on
# PowerShell 7, where the type is unknown until it is loaded.
function Initialize-SerialPortType {
    if ("System.IO.Ports.SerialPort" -as [type]) { return $true }
    try { Add-Type -AssemblyName System.IO.Ports -ErrorAction Stop; return $true } catch {}
    Fail "System.IO.Ports is unavailable in this PowerShell."
    if ($PLATFORM -ne "windows") {
        Warn "  on Linux: install the runtime package (e.g. apt install libc6-dev) and"
        Warn "  make sure the user is in the dialout group for /dev/tty* access."
    }
    return $false
}

# Which process is holding a serial port. Neither OS offers a direct answer, so
# this is a best-effort name match against the usual terminals -- enough to say
# "close sscom" instead of "access denied".
function Get-PortHolderHint {
    $known = @("sscom", "putty", "xshell", "securecrt", "mobaxterm", "ttermpro", "teraterm",
               "realterm", "termite", "hterm", "AccessPort", "XCOM", "UartAssist", "arduino",
               "minicom", "picocom", "screen", "cu", "tio")
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
    if (-not (Initialize-SerialPortType)) { return $open }
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
            if ($msg -match "denied|Permission") {
                $who = Get-PortHolderHint
                if ($who) { Warn "  a serial terminal is holding it: $who -- close it and retry" }
                if ($PLATFORM -ne "windows") { Warn "  or the user is not in the dialout group" }
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

# ---------------------------------------------------------------- board
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
        if ($PLATFORM -ne "windows") {
            Warn "  on Linux this is also what a missing ST-Link udev rule looks like."
        }
        exit 1
    }
}
