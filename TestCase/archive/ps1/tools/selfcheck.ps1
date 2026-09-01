# Every check that needs no board, in one command.
#
# This is layer 1 of the acceptance checklist (acceptance/checklist.md section
# A). It is cheap enough to run after every edit, and each round of on-board
# debugging costs an order of magnitude more -- so nothing here should ever be
# skipped on the way to the board.
#
#   .\tools\selfcheck.ps1              run everything
#   .\tools\selfcheck.ps1 -Quick       skip the slow ones (fakeboard, C unit tests)
#   .\tools\selfcheck.ps1 -List        say what each step is, run nothing
#
# Exit 0 = all pass, 1 = at least one failed. A check whose prerequisite is
# absent (no gcc, no python) reports SKIP and does not fail the run -- but the
# summary always names it, because a silently skipped check reads as a pass.
#
# Step ids ARE the case ids (2026-08-22). They used to be A0..A14, a numbering of
# their own -- and every one of those numbers was only ever an alias for a case
# that already had an id: A12 was DG1, A13 was P4, A2 was H2. Worse, A1/A2/A3/A7
# collided with requirement ids of the same name, so "A7 passed" had four possible
# meanings. Dropping the alias deletes a whole namespace and one of those
# collisions. The map from the old numbers is in
# open_plc_cube_ide/docs/ID-MAP.md.

param([switch]$Quick, [switch]$List)

. "$PSScriptRoot/_common.ps1"

$results = New-Object System.Collections.ArrayList

$STATUS_DOC   = "open_plc_cube_ide/docs/STATUS.md"
$CRITERIA_DOC = "TestCase/TEST-CASES.md"

# What each step is, in run order. This is the ONE place the step list lives:
# -List prints it, and Step looks up Covers here and throws if a step is missing,
# so the two can never drift apart.
#
# Covers is the requirement id in STATUS.md that this case is the evidence for.
# A case that covers nothing should not exist.
$CATALOG = @(
    [pscustomobject]@{ Id = "ENV";    Covers = "-";                   Name = "this machine has the toolchain" }
    [pscustomobject]@{ Id = "H1";     Covers = "C5";                  Name = "host Go tests (crypto primitives, key derivation, challenge/response)" }
    [pscustomobject]@{ Id = "H3";     Covers = "-";                   Name = "go vet over the whole module" }
    [pscustomobject]@{ Id = "P1";     Covers = "D7";                  Name = "firmware version agrees in all three places" }
    [pscustomobject]@{ Id = "P2";     Covers = "D8 A6 A7 C7 E1 E6";   Name = "cross-repo mirrored code has not diverged" }
    [pscustomobject]@{ Id = "P3";     Covers = "D9";                  Name = "Arduino core: live matches the git repo" }
    [pscustomobject]@{ Id = "P6";     Covers = "C10";                 Name = "the published-root warning still recognises the published root" }
    [pscustomobject]@{ Id = "P7";     Covers = "-";                   Name = "STATUS.md and TEST-CASES.md name the same set of cases" }
    [pscustomobject]@{ Id = "P8";     Covers = "-";                   Name = "no claim is written out in more than one document" }
    [pscustomobject]@{ Id = "P9";     Covers = "-";                   Name = "every path a document names actually exists" }
    [pscustomobject]@{ Id = "H2";     Covers = "C5";                  Name = "host C unit tests (real sha256.c / iap_keyderive.c / iap_auth.c)" }
    [pscustomobject]@{ Id = "K1-K6";  Covers = "C8";                  Name = "IAPTool key-match logic against a stand-in board" }
    [pscustomobject]@{ Id = "X1-X2";  Covers = "C9";                  Name = "crypto cross-check against independent implementations" }
    [pscustomobject]@{ Id = "DG1";    Covers = "C6";                  Name = "downgrade guard: older image refused, and not uploaded" }
    [pscustomobject]@{ Id = "P4";     Covers = "E6";                  Name = "Arduino variant assertions (the FMC reserved-pin table)" }
)

if ($List) {
    Section "selfcheck steps"
    Write-Host "  step ids are case ids; 'covers' points into $STATUS_DOC"
    Write-Host "  pass/fail criteria for each case: $CRITERIA_DOC"
    Write-Host ""
    $iw = ($CATALOG | ForEach-Object { $_.Id.Length }     | Measure-Object -Maximum).Maximum
    $cw = ($CATALOG | ForEach-Object { $_.Covers.Length } | Measure-Object -Maximum).Maximum
    foreach ($s in $CATALOG) {
        Write-Host ("  {0,-$iw}  covers {1,-$cw}  {2}" -f $s.Id, $s.Covers, $s.Name)
    }
    Write-Host ""
    Write-Host ("  {0} steps. -Quick skips H2 / K1-K6 / X1-X2 / DG1 / P4." -f $CATALOG.Count)
    Write-Host "  P7, P8 and P9 check the documents, not the firmware."
    exit 0
}

function Get-Covers([string]$Id) {
    $row = $CATALOG | Where-Object { $_.Id -eq $Id }
    if (-not $row) { throw "step '$Id' is not in `$CATALOG -- add it there too" }
    return $row.Covers
}

function Step {
    param([string]$Id, [string]$Name, [scriptblock]$Body, [string]$Needs)

    $covers = Get-Covers $Id
    Section "$Id  $Name"
    # Say what this proves before running it. "A12 passed" told nobody anything.
    Write-Host "  covers $covers   ($STATUS_DOC)"

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

# ---------------------------------------------------------------- ENV
# What this machine actually has. Runs first because every failure below is
# easier to read once you know whether the thing was even installed -- and
# because on a freshly cloned machine this is the check that says what to go and
# install. Nothing here fails the run: CubeIDE and a serial port are needed to
# reach the board, not to pass the host-side checks.
Section "ENV  this machine"

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
ProbeCmd "go"       "go"         "H1/H3 and every IAPTool build need it"
ProbeCmd "python"   "python"     "K1-K6 / X1-X2 / DG1 need it"
Probe "arduino-cli" $ARDUINO_CLI "P4 and command-line app builds need it"
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
    [void]$results.Add([pscustomobject]@{ Id = "ENV"; Name = "this machine has the toolchain"; State = "SKIP"; Note = ($missing -join ", ") })
} else {
    Ok "  everything config points at exists"
    [void]$results.Add([pscustomobject]@{ Id = "ENV"; Name = "this machine has the toolchain"; State = "PASS"; Note = "" })
}

Push-Location $TOOL_REPO

Step "H1" "host Go tests (crypto primitives, key derivation, challenge/response)" {
    go test ./TestCase/... 2>&1
} "go"

Step "H3" "go vet over the whole module" {
    go vet ./... 2>&1
} "go"

Step "P1" "firmware version agrees in all three places" {
    & "$PSScriptRoot/check-version-sync.ps1" 2>&1
}

Step "P2" "cross-repo mirrored code has not diverged" {
    & "$PSScriptRoot/check-mirror-sync.ps1" 2>&1
}

Step "P3" "Arduino core: live matches the git repo" {
    & "$PSScriptRoot/check-core-sync.ps1" 2>&1
}

Step "P6" "the published-root warning still recognises the published root" {
    & "$PSScriptRoot/check-public-root.ps1" 2>&1
}

# These two guard the documents rather than the product, and they exist only in
# Python -- they were written after M7 started, so there is no PowerShell twin to
# be a baseline for. Calling the .py from here keeps the two versions' verdicts
# comparable, which is what M7 step 4 actually requires.
Step "P7" "STATUS.md and TEST-CASES.md name the same set of cases" {
    & (Get-PythonExe) "$PSScriptRoot/check_status_sync.py" 2>&1
}

Step "P8" "no claim is written out in more than one document" {
    & (Get-PythonExe) "$PSScriptRoot/check_doc_dupes.py" 2>&1
}

Step "P9" "every path a document names actually exists" {
    & (Get-PythonExe) "$PSScriptRoot/check_doc_paths.py" 2>&1
}

if (-not $Quick) {
    # $HOST_CC from config wins; otherwise fall back to whatever "gcc" resolves
    # to on PATH, so a machine with neither still reports SKIP by name.
    $ccNeed = "gcc"
    if ($HOST_CC) { $ccNeed = $HOST_CC }

    Step "H2" "host C unit tests (real sha256.c / iap_keyderive.c / iap_auth.c)" {
        & "$PSScriptRoot/..\host\bootloader_unit\build.ps1" 2>&1
    } $ccNeed

    Step "K1-K6" "IAPTool key-match logic against a stand-in board" {
        & "$PSScriptRoot/..\host\fakeboard\run-cases.ps1" 2>&1
    } "python"

    Step "X1-X2" "crypto cross-check against independent implementations" {
        & "$PSScriptRoot/..\host\crypto_ref\run-checks.ps1" -Rounds 8 2>&1
    } "python"

    Step "DG1" "downgrade guard: older image refused, and not uploaded" {
        & "$PSScriptRoot/..\host\fakeboard\run-downgrade.ps1" 2>&1
    } "python"

    Step "P4" "Arduino variant assertions (the FMC reserved-pin table)" {
        & "$PSScriptRoot/..\host\variant_check\build.ps1" 2>&1
    } $ARDUINO_CLI
}

Pop-Location

Section "summary"
$w  = ($results | ForEach-Object { $_.Name.Length } | Measure-Object -Maximum).Maximum
$iw = ($results | ForEach-Object { $_.Id.Length }   | Measure-Object -Maximum).Maximum
foreach ($r in $results) {
    $line = "{0,-$iw} {1,-$w}  {2}" -f $r.Id, $r.Name, $r.State
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
Ok "host-side checks pass; next is acceptance/checklist.md CHK-A4 (build) and CHK-A5 (flash)"
exit 0
