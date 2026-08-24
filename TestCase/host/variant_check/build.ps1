# Compiles the variant assertion sketches. A failure here is a broken variant
# header, not a broken sketch.
#
#   .\build.ps1
#
# Needs the Arduino IDE's bundled arduino-cli and the OpenPLC core installed;
# $ARDUINO_CLI and $ARDUINO_CLI_CONFIG in config/machine.ps1 point at them.
#
# Exit 0 = every sketch compiled, 1 = at least one did not, 2 = prerequisites
# missing.

. "$PSScriptRoot/..\..\tools\_common.ps1"

if (-not $ARDUINO_CLI -or -not (Test-Path $ARDUINO_CLI)) {
    Fail "arduino-cli not found. Set `$ARDUINO_CLI in config/machine.ps1"
    exit 2
}
if (-not (Test-Path $ARDUINO_CLI_CONFIG)) {
    Fail "arduino-cli config not found at $ARDUINO_CLI_CONFIG"
    exit 2
}

# Kept in step with docs/test/BUILD-AND-TEST.md. The menu options matter: the
# variant header is selected by pnum, and a wrong FQBN would compile a
# different variant and prove nothing about this one.
$fqbn = "OpenPLC_Alpha:stm32:OPEN-PLC:pnum=PLC_H743,usb=CDCgen,xusb=FS,upload_method=cdcMethod,knxrole=dual_device,downgrade=refuse"

$failed = 0
# Any directory here is treated as a sketch, so skip the ones that are not.
# __pycache__ appears the moment anything imports a module from this tree, and
# arduino-cli dutifully tried to compile it -- a FAIL that says nothing about the
# variant.
$skipDirs = @('__pycache__', '.vscode', 'build')
foreach ($sketch in (Get-ChildItem $PSScriptRoot -Directory | Where-Object { $skipDirs -notcontains $_.Name })) {
    Section "compiling $($sketch.Name)"
    $buildPath = Join-Path ([System.IO.Path]::GetTempPath()) ("variant_check_" + $sketch.Name)

    $out = & $ARDUINO_CLI compile --warnings all `
        --config-file $ARDUINO_CLI_CONFIG --fqbn $fqbn `
        --build-path $buildPath $sketch.FullName 2>&1
    $rc = $LASTEXITCODE

    if ($rc -eq 0) {
        Ok "PASS - the variant's assertions hold"
    } else {
        Fail "FAIL - a static_assert fired, or the sketch does not compile:"
        # static_assert messages are the payload here, so show the error lines
        # rather than the usual last-N-lines summary.
        ($out | Where-Object { $_ -match "error|static_assert|assertion" }) |
            ForEach-Object { Write-Host "    $_" }
        $failed++
    }
    Remove-Item $buildPath -Recurse -Force -ErrorAction SilentlyContinue
}

Section "result"
if ($failed -gt 0) { Fail "$failed sketch(es) failed"; exit 1 }
Ok "all variant assertions hold"
exit 0
