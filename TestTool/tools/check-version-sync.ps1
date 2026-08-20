# Checks that the firmware version agrees everywhere it is written down.
#
# The bootloader and the Arduino core each carry their own copy of the version
# string and nothing links them. When they drifted (bootloader stuck on 0.1.2
# while the core said 0.1.3) the same board reported two versions and looked
# like a failed upgrade. This is release-checklist item B1, automated.
#
# Exit 0 = all agree, 1 = drift, 2 = a file is missing.

. "$PSScriptRoot/_common.ps1"

function Get-One {
    param([string]$Path, [string]$Pattern, [string]$What)
    if (-not (Test-Path $Path)) { Fail "missing: $Path"; exit 2 }
    $m = [regex]::Match((Get-Content $Path -Raw), $Pattern)
    if (-not $m.Success) { Fail "no $What in $Path"; exit 2 }
    return $m.Groups[1].Value.Trim()
}

$bootCfg  = Join-Path $BOOT_REPO "Core\Inc\IAP_config.h"
$boards   = Join-Path $CORE_LIVE "boards.txt"
$relNotes = Join-Path $BOOT_REPO "RELEASE-NOTES.md"

$vBoot  = Get-One $bootCfg '#define\s+OPENPLC_FW_VERSION\s+"([^"]+)"'   "OPENPLC_FW_VERSION"
$vCore  = Get-One $boards  'OPEN-PLC\.build\.fw_version\s*=\s*(\S+)'    "build.fw_version"
$vNotes = Get-One $relNotes '(?m)^##\s+.*?(\d+\.\d+\.\d+)'              "version heading"

Section "firmware version"
Write-Host ("  bootloader  Core/Inc/IAP_config.h      {0}" -f $vBoot)
Write-Host ("  core        boards.txt                 {0}" -f $vCore)
Write-Host ("  notes       RELEASE-NOTES.md heading   {0}" -f $vNotes)

$bad = 0
if ($vBoot -ne $vCore)  { Fail "bootloader and core disagree"; $bad++ }
if ($vBoot -ne $vNotes) { Fail "bootloader and RELEASE-NOTES disagree"; $bad++ }

# The known-issues section outlives the fix more often than not: an entry that
# names an older version is either stale or a genuinely unfixed regression, and
# either way someone has to look.
$stale = @()
$lineNo = 0
foreach ($line in (Get-Content $relNotes)) {
    $lineNo++
    foreach ($m in [regex]::Matches($line, '(\d+\.\d+\.\d+)')) {
        if ($m.Groups[1].Value -ne $vBoot -and $line -match 'reports version|still\s+`?\d') {
            $stale += ("    RELEASE-NOTES.md:{0}  {1}" -f $lineNo, $line.Trim())
        }
    }
}
if ($stale.Count -gt 0) {
    Warn "RELEASE-NOTES.md mentions an older version in what reads like a live issue:"
    $stale | Select-Object -Unique | ForEach-Object { Warn $_ }
    Warn "  -> if it is fixed, delete the entry; a stale known-issue is worse than none"
}

Section "result"
if ($bad -gt 0) { Fail "version drift"; exit 1 }
Ok ("all three agree on {0}" -f $vBoot)
exit 0
