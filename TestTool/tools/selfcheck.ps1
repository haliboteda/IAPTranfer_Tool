# Every check that needs no board, in one command.
#
# This is layer 1 of the acceptance checklist (acceptance/checklist.md section
# A). It is cheap enough to run after every edit, and each round of on-board
# debugging costs an order of magnitude more -- so nothing here should ever be
# skipped on the way to the board.
#
#   .\tools\selfcheck.ps1              run everything
#   .\tools\selfcheck.ps1 -Quick       skip the slow ones (fakeboard, C unit tests)
#
# Exit 0 = all pass, 1 = at least one failed. A check whose prerequisite is
# absent (no gcc, no python) reports SKIP and does not fail the run -- but the
# summary always names it, because a silently skipped check reads as a pass.

param([switch]$Quick)

. "$PSScriptRoot/_common.ps1"

$results = New-Object System.Collections.ArrayList

function Step {
    param([string]$Id, [string]$Name, [scriptblock]$Body, [string]$Needs)

    Section "$Id  $Name"

    # $Needs is a command name or an absolute path: a tool installed for one
    # check only (see $HOST_CC) has no business being on PATH.
    if ($Needs -and -not (Get-Command $Needs -ErrorAction SilentlyContinue) -and
                    -not (Test-Path $Needs -ErrorAction SilentlyContinue)) {
        Warn "SKIP - $Needs not found"
        [void]$results.Add([pscustomobject]@{ Id = $Id; Name = $Name; State = "SKIP"; Note = "$Needs missing" })
        return
    }

    $out = & $Body
    $code = $LASTEXITCODE
    if ($out) { $out | ForEach-Object { Write-Host "  $_" } }

    if ($code -eq 0) {
        Ok "PASS"
        [void]$results.Add([pscustomobject]@{ Id = $Id; Name = $Name; State = "PASS"; Note = "" })
    } else {
        Fail "FAIL (exit $code)"
        [void]$results.Add([pscustomobject]@{ Id = $Id; Name = $Name; State = "FAIL"; Note = "exit $code" })
    }
}

# ---------------------------------------------------------------- A0
# What this machine actually has. Runs first because every failure below is
# easier to read once you know whether the thing was even installed -- and
# because on a freshly cloned machine this is the check that says what to go and
# install. Nothing here fails the run: CubeIDE and a serial port are needed to
# reach the board, not to pass the host-side checks.
Section "A0  this machine"

Write-Host ("  platform            {0}   (PowerShell {1}, {2})" -f `
    $PLATFORM, $PSVersionTable.PSVersion, $PSVersionTable.PSEdition)
Write-ConfigPlatformWarning

$missing = @()
function Probe($label, $path, $why) {
    if ($path -and (Test-Path $path -ErrorAction SilentlyContinue)) {
        Write-Host ("  {0,-19} {1}" -f $label, $path)
    } else {
        Warn ("  {0,-19} MISSING - {1}" -f $label, $why)
        $script:missing += $label
    }
}
function ProbeCmd($label, $cmd, $why) {
    $c = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($c) { Write-Host ("  {0,-19} {1}" -f $label, $c.Source) }
    else    { Warn ("  {0,-19} MISSING - {1}" -f $label, $why); $script:missing += $label }
}

Probe "BOOT_REPO"   $BOOT_REPO   "bootloader repo; set it in config/machine.ps1"
Probe "CORE_REPO"   $CORE_REPO   "Arduino core repo; set it in config/machine.ps1"
Probe "TOOL_REPO"   $TOOL_REPO   "this repo; set it in config/machine.ps1"
Probe "CORE_LIVE"   $CORE_LIVE   "install the board package in the Arduino IDE first"
ProbeCmd "go"       "go"         "A1/A3 and every IAPTool build need it"
ProbeCmd "python"   "python"     "A10/A11/A12 need it"
Probe "arduino-cli" $ARDUINO_CLI "A13 and command-line app builds need it"
Probe "CubeIDE"     $CUBEIDE     "needed to build and flash the bootloader, not for the checks below"

# The programmer and IAPTool are resolved by wildcard, so report what the lookup
# found rather than what config says -- that is the value the scripts will use.
$cli = Get-ChildItem (Join-Path $CUBEIDE (Join-Path "STM32CubeIDE" (Join-Path "plugins" (Join-Path "com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.${CUBE_PLUG}_*" (Join-Path "tools" (Join-Path "bin" "STM32_Programmer_CLI$EXE")))))) -ErrorAction SilentlyContinue |
    Sort-Object FullName | Select-Object -Last 1
if ($cli) { Write-Host ("  {0,-19} {1}" -f "programmer CLI", $cli.FullName) }
else      { Warn ("  {0,-19} MISSING - no '{1}' plugin under CubeIDE" -f "programmer CLI", $CUBE_PLUG); $missing += "programmer CLI" }

$iap = Get-ChildItem (Join-Path $A15 (Join-Path "packages" (Join-Path "OpenPLC_Alpha" (Join-Path "tools" (Join-Path "STM32Tools" (Join-Path "*" (Join-Path $A15_DIR "IAPTool$EXE"))))))) -ErrorAction SilentlyContinue |
    Sort-Object FullName | Select-Object -Last 1
if ($iap) { Write-Host ("  {0,-19} {1}" -f "shipped IAPTool", $iap.FullName) }
else      { Warn ("  {0,-19} MISSING - no IAPTool for '{1}' under the board package" -f "shipped IAPTool", $A15_DIR); $missing += "shipped IAPTool" }

Write-Host ("  {0,-19} {1}" -f "log ports (config)", ($LOG_PORTS -join ", "))
foreach ($p in $LOG_PORTS) {
    if ($PLATFORM -eq "windows" -or -not $p) { continue }
    # COMn is not a device name here. Saying "check the adapter and the dialout
    # group" for one sends the reader after hardware when the config is what
    # needs editing.
    if ($p -match '^COM\d+$') {
        Warn ("  {0,-19} {1} is a Windows port name -- config/machine.ps1 still has the Windows block" -f "", $p)
    } elseif (-not (Test-Path $p)) {
        Warn ("  {0,-19} {1} does not exist -- check the adapter and the dialout group" -f "", $p)
    }
}

if ($missing.Count -gt 0) {
    Warn ("  -> {0} thing(s) missing on this machine: {1}" -f $missing.Count, ($missing -join ", "))
    Warn "     see open_plc_cube_ide/CLAUDE.md for what each one is and where to get it"
    [void]$results.Add([pscustomobject]@{ Id = "A0"; Name = "this machine has the toolchain"; State = "SKIP"; Note = ($missing -join ", ") })
} else {
    Ok "  everything config points at exists"
    [void]$results.Add([pscustomobject]@{ Id = "A0"; Name = "this machine has the toolchain"; State = "PASS"; Note = "" })
}

Push-Location $TOOL_REPO

Step "A1" "host Go tests (crypto primitives, key derivation, challenge/response)" {
    go test ./TestTool/... 2>&1
} "go"

Step "A3" "go vet over the whole module" {
    go vet ./... 2>&1
} "go"

Step "A7" "firmware version agrees in all three places" {
    & "$PSScriptRoot/check-version-sync.ps1" 2>&1
}

Step "A8" "cross-repo mirrored code has not diverged" {
    & "$PSScriptRoot/check-mirror-sync.ps1" 2>&1
}

Step "A9" "Arduino core: live matches the git repo" {
    & "$PSScriptRoot/check-core-sync.ps1" 2>&1
}

Step "A14" "the published-root warning still recognises the published root" {
    & "$PSScriptRoot/check-public-root.ps1" 2>&1
}

if (-not $Quick) {
    # $HOST_CC from config wins; otherwise fall back to whatever "gcc" resolves
    # to on PATH, so a machine with neither still reports SKIP by name.
    $ccNeed = "gcc"
    if ($HOST_CC) { $ccNeed = $HOST_CC }

    Step "A2" "host C unit tests (real sha256.c / iap_keyderive.c / iap_auth.c)" {
        & "$PSScriptRoot/..\host\bootloader_unit\build.ps1" 2>&1
    } $ccNeed

    Step "A10" "IAPTool key-match logic against a stand-in board" {
        & "$PSScriptRoot/..\host\fakeboard\run-cases.ps1" 2>&1
    } "python"

    Step "A11" "crypto cross-check against independent implementations" {
        & "$PSScriptRoot/..\host\crypto_ref\run-checks.ps1" -Rounds 8 2>&1
    } "python"

    Step "A12" "downgrade guard (DG1): older image refused, and not uploaded" {
        & "$PSScriptRoot/..\host\fakeboard\run-downgrade.ps1" 2>&1
    } "python"

    Step "A13" "Arduino variant assertions (E6: the FMC reserved-pin table)" {
        & "$PSScriptRoot/..\host\variant_check\build.ps1" 2>&1
    } $ARDUINO_CLI
}

Pop-Location

Section "summary"
$w = ($results | ForEach-Object { $_.Name.Length } | Measure-Object -Maximum).Maximum
foreach ($r in $results) {
    $line = "{0,-4} {1,-$w}  {2}" -f $r.Id, $r.Name, $r.State
    if ($r.Note) { $line += " ($($r.Note))" }
    switch ($r.State) {
        "PASS" { Ok   $line }
        "SKIP" { Warn $line }
        "FAIL" { Fail $line }
    }
}

$failed  = @($results | Where-Object { $_.State -eq "FAIL" }).Count
$skipped = @($results | Where-Object { $_.State -eq "SKIP" }).Count

Write-Host ""
if ($failed -gt 0) {
    Fail "$failed failed - do not go to the board until these are green"
    exit 1
}
if ($skipped -gt 0) {
    Warn "$skipped skipped - those areas are unverified on this machine"
}
Ok "host-side checks pass; next is acceptance/checklist.md A4 (build) and A5 (flash)"
exit 0
