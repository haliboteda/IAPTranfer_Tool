# Build the bootloader headless, flash it over ST-Link, capture the boot log,
# and give the T0 verdict (is the SDRAM staging buffer usable).
#
#   .\flash-bootloader.ps1                  build + flash + watch
#   .\flash-bootloader.ps1 -SkipBuild       flash what is already built
#   .\flash-bootloader.ps1 -ResetOnly       only reset and watch; never writes flash
#   .\flash-bootloader.ps1 -Seconds 20      watch longer
#
# CubeIDE must be CLOSED for a build: a headless build cannot take a locked
# workspace. -ResetOnly and -SkipBuild do not care.

param(
    [switch]$SkipBuild,
    [switch]$ResetOnly,
    [int]$Seconds = 12,
    [string[]]$Ports
)

. "$PSScriptRoot\_common.ps1"

if ($ResetOnly) { $SkipBuild = $true }
if (-not $Ports) { $Ports = $LOG_PORTS }

$ELF = Join-Path $BOOT_REPO "Debug\open_plc_cube_ide.elf"
$BIN = Join-Path $BOOT_REPO "Debug\open_plc_cube_ide.bin"
$CLI = Get-ProgrammerCli

# ------------------------------------------------------------------ build
if (-not $SkipBuild) {
    Section "Build"
    $out = & (Get-CubeIdeExe) --launcher.suppressErrors -nosplash `
        -application org.eclipse.cdt.managedbuilder.core.headlessbuild `
        -data $WORKSPACE -build "open_plc_cube_ide/Debug" 2>&1
    $out | Select-String -Pattern "Build Finished|error:|Error " | ForEach-Object { Write-Host $_ }
    if (($out -join "`n") -match "Build Finished\. (\d+) errors" -and $Matches[1] -ne "0") {
        Fail "build reported errors - stopping"; exit 1
    }
    if (-not (Test-Path $ELF)) { Fail "no .elf produced - stopping"; exit 1 }
}

if (Test-Path $BIN) {
    $len = (Get-Item $BIN).Length
    # 128K is the bootloader's whole sector; the linker script caps FLASH there.
    Write-Host ("bin = {0:N0} B of 131,072  ({1:P1} used, {2:N0} B free)" -f $len, ($len / 131072), (131072 - $len))
}

# ---------------------------------------------------------- target present
Section "Target check"
Assert-TargetReachable $CLI

# ------------------------------------------- open ports, then flash / reset
Section $(if ($ResetOnly) { "Reset + capture" } else { "Flash + capture" })
$open = Open-LogPorts $Ports

if ($ResetOnly) {
    & $CLI -c port=SWD mode=UR -rst 2>&1 |
        Select-String -Pattern "Error|Reset" | ForEach-Object { Write-Host $_ }
} else {
    & $CLI -c port=SWD mode=UR -w $ELF -rst 2>&1 |
        Select-String -Pattern "Download|verified|Error|Reset" | ForEach-Object { Write-Host $_ }
}

$buf = Read-LogPorts $open $Seconds
foreach ($k in $buf.Keys) {
    Section "$k  ($($buf[$k].Length) bytes)"
    if ($buf[$k].Length -gt 0) { Write-Host $buf[$k] }
}

# -------------------------------------------------------------- T0 verdict
Section "T0 verdict"
$all = ($buf.Values -join "`n")
if ($all -match "SDRAM staging buffer OK") {
    Ok "PASS - staging buffer usable. Next: T1 (normal upload)."
} elseif ($all -match "SDRAM SELF-TEST FAILED") {
    Fail "FAIL - SDRAM self-test failed. Fix FMC / power-up sequence before testing uploads."
} elseif ($all -match "APP Mod") {
    # MX_FMC_Init() lives in Phase 2 of main(), which only runs when the board
    # stays in the bootloader. A board with a valid application jumps at
    # main.c:185 and never reaches the self-test -- absence of the SDRAM line
    # here means "not reached", not "failed".
    Warn "INCONCLUSIVE - the board booted its application, so Phase 2 never ran."
    Warn "  The SDRAM self-test only runs when the board stays in the bootloader."
    Warn "  Hold BOOT0 through the startup window, or use IAPTool to request upload mode."
} elseif ($all.Trim().Length -eq 0) {
    Warn "No serial output at all."
    Warn "  Either every log port was busy (see above), or nothing is wired to UART4."
    Warn "  SWO/ITM carries the same log if the RS232 route is unavailable."
} else {
    Warn "Serial output arrived but no SDRAM line - is this the new bootloader?"
}
