# Compiles every example sketch shipped with the core's own libraries.
#
#   .\build.ps1                 all of them
#   .\build.ps1 -Only SDRAM     only libraries whose name matches
#
# Why this exists: example sketches are the first thing a new user compiles and
# the last thing anybody re-checks. They rot silently -- an API gets renamed,
# the examples keep referring to the old name, and nobody notices until someone
# opens one and it does not build. Nothing else in the test suite would catch
# that, because the examples are not part of any application build.
#
# Scope: only libraries under the core that this project owns. Upstream
# STM32duino libraries carry hundreds of examples for boards this variant is
# not, and compiling those would report failures nobody intends to fix.
#
# Exit 0 = every example compiled, 1 = at least one did not, 2 = prerequisites
# missing.

param(
    [string]$Only,
    [switch]$KeepBuildDirs
)

. "$PSScriptRoot\..\..\tools\_common.ps1"

if (-not $ARDUINO_CLI -or -not (Test-Path $ARDUINO_CLI)) {
    Fail "arduino-cli not found. Set `$ARDUINO_CLI in config/machine.ps1"
    exit 2
}
if (-not (Test-Path $ARDUINO_CLI_CONFIG)) {
    Fail "arduino-cli config not found at $ARDUINO_CLI_CONFIG"
    exit 2
}

# Libraries this project owns. Upstream ones are deliberately not listed.
$ownLibraries = @("OpenPLC_SDRAM", "OpenPLC_IAP", "OpenPLC_Net", "OpenPLC_KNX")

$fqbn = "OpenPLC_Alpha:stm32:OPEN-PLC:pnum=PLC_H743,usb=CDCgen,xusb=FS,upload_method=cdcMethod,knxrole=dual_device,downgrade=refuse"

$sketches = @()
foreach ($lib in $ownLibraries) {
    if ($Only -and $lib -notlike "*$Only*") { continue }
    $dir = Join-Path $CORE_LIVE "libraries\$lib\examples"
    if (-not (Test-Path $dir)) { continue }
    # An Arduino example is a directory holding a .ino of the same name.
    foreach ($d in (Get-ChildItem $dir -Directory -Recurse)) {
        if (Test-Path (Join-Path $d.FullName "$($d.Name).ino")) {
            $sketches += [pscustomobject]@{ Lib = $lib; Name = $d.Name; Path = $d.FullName }
        }
    }
}

if ($sketches.Count -eq 0) {
    if ($Only) { Warn "no examples matched -Only $Only"; exit 0 }
    # Not a pass: the list above says these libraries have examples. None found
    # means either they were deleted or $CORE_LIVE is wrong.
    Fail "no example sketches found under $CORE_LIVE -- is `$CORE_LIVE correct?"
    exit 2
}

$failed = 0
foreach ($s in $sketches) {
    Section "$($s.Lib) / $($s.Name)"
    $buildPath = Join-Path ([System.IO.Path]::GetTempPath()) ("ex_" + $s.Lib + "_" + $s.Name)

    $out = & $ARDUINO_CLI compile --warnings all --config-file $ARDUINO_CLI_CONFIG `
        --fqbn $fqbn --build-path $buildPath $s.Path 2>&1
    $rc = $LASTEXITCODE

    if ($rc -eq 0) {
        $size = ($out | Select-String -Pattern "Sketch uses (\d+) bytes")
        if ($size) { Ok ("PASS - " + $size.Matches[0].Value) } else { Ok "PASS" }
    } else {
        Fail "FAIL"
        # Errors only. Matching "error|warning" showed a wall of upstream
        # -Wunknown-pragmas noise and truncated before reaching the actual
        # cause -- the failure looked like it had no explanation.
        $errors = @($out | Where-Object { $_ -match "\berror\b|Error during build" })
        if ($errors.Count -gt 0) {
            $errors | Select-Object -First 12 | ForEach-Object { Write-Host "    $_" }
        } else {
            Warn "    no line matched 'error'; last 10 lines of output:"
            ($out | Select-Object -Last 10) | ForEach-Object { Write-Host "    $_" }
        }
        $failed++
    }
    if (-not $KeepBuildDirs) {
        Remove-Item $buildPath -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Section "result"
Write-Host "  $($sketches.Count) example(s) compiled, $failed failed"
if ($failed -gt 0) { Fail "$failed example(s) do not build"; exit 1 }
Ok "every example builds"
exit 0
