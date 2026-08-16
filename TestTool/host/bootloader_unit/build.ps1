# Builds and runs the host-side IAP security test harness against the real
# bootloader source in open_plc_cube_ide/IAPServer. Needs gcc or clang on
# PATH (MinGW-w64 or LLVM); override with $env:CC = "clang".

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

# Where the bootloader repo is comes from config/machine.ps1, not from counting
# "..\" upwards: that chain silently broke the moment this directory moved, and
# it was wrong on any machine with a different layout anyway.
$cfg = Join-Path $Here "..\..\config\machine.ps1"
if (-not (Test-Path $cfg)) {
    Write-Error "config/machine.ps1 is missing - copy config/machine.example.ps1 and fill it in."
    exit 1
}
. $cfg
$IapServer = Join-Path $BOOT_REPO "IAPServer"

$cc = $null
foreach ($cand in @($env:CC, "gcc", "clang")) {
    if ($cand -and (Get-Command $cand -ErrorAction SilentlyContinue)) { $cc = $cand; break }
}
if (-not $cc) {
    Write-Error "No C compiler (gcc/clang) found on PATH. Install MinGW-w64 or LLVM/clang and re-run, or run build.sh under WSL/Git Bash."
    exit 1
}

& $cc -std=c11 -Wall -Wextra -O0 -g `
    -I "$Here\stubs" `
    -I "$IapServer" `
    "$Here\stubs\hal_stub.c" `
    "$Here\stubs\bootloader_state_stub.c" `
    "$IapServer\sha256.c" `
    "$IapServer\iap_keyderive.c" `
    "$IapServer\iap_auth.c" `
    "$Here\test_main.c" `
    -o "$Here\iap_hosttest.exe"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& "$Here\iap_hosttest.exe"
exit $LASTEXITCODE
