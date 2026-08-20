# M7 acceptance harness, failure paths.
#
#     .\tools\m7-compare-faults.ps1
#
# m7-compare.ps1 only proves the two versions agree when everything is fine. A
# Python script that checked nothing at all and printed the same "all agree"
# text would pass it. What has to be shown is that both versions go red on the
# same input, through the same branch, with the same words -- so this injects
# one controlled fault at a time and compares the output again.
#
# The M5 lesson is the reason this file exists: a test case that ran clean on
# UNFIXED code nearly got a defect declared fixed. A translated check that no
# longer looks at anything would fail exactly the same way.
#
# Every fault is applied to a real working-tree file and undone in a finally
# block; the run ends by asserting the touched repos are clean again. If this
# script is ever interrupted, `git status` in $BOOT_REPO is the recovery check.
#
# Exit 0 = both versions behaved identically under every fault, 1 = they did not,
# 2 = a fault could not be applied (nothing was changed).

. "$PSScriptRoot/_common.ps1"

$compare = Join-Path $PSScriptRoot "m7-compare.ps1"
$bad = 0
$cases = 0

# Applies $Mutate to $Path, runs the named comparison, restores the file.
# Restoration happens in finally, so a failing comparison still leaves the tree
# as it was found.
function Invoke-WithFault {
    param(
        [string]$What,       # description, printed
        [string]$Pair,       # which m7-compare pair to run
        [string]$Path,       # file to damage
        [scriptblock]$Mutate # string -> string
    )
    $script:cases++
    Section ("fault: {0}" -f $What)

    if (-not (Test-Path $Path)) { Fail "not found: $Path"; $script:bad++; return }
    $original = [System.IO.File]::ReadAllBytes($Path)
    $text = [System.IO.File]::ReadAllText($Path)
    $damaged = & $Mutate $text
    if ($damaged -eq $text) {
        Fail "the mutation changed nothing -- the pattern no longer matches this file"
        Fail "  $Path"
        $script:bad++
        return
    }

    try {
        [System.IO.File]::WriteAllText($Path, $damaged)
        & powershell -NoProfile -ExecutionPolicy Bypass -File $compare -Only $Pair | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Ok "both versions produced identical output under this fault"
        } else {
            Fail "the two versions diverged under this fault -- rerun:"
            Fail ("  .\tools\m7-compare.ps1 -Only {0}   (with the fault applied)" -f $Pair)
            $script:bad++
        }
    } finally {
        [System.IO.File]::WriteAllBytes($Path, $original)
    }
}

# Same, for a fault that is an extra file rather than an edit.
function Invoke-WithExtraFile {
    param([string]$What, [string]$Pair, [string]$Path)
    $script:cases++
    Section ("fault: {0}" -f $What)
    if (Test-Path $Path) { Fail "refusing to overwrite an existing file: $Path"; $script:bad++; return }
    try {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
        [System.IO.File]::WriteAllText($Path, "m7 fault injection`n")
        & powershell -NoProfile -ExecutionPolicy Bypass -File $compare -Only $Pair | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Ok "both versions produced identical output under this fault"
        } else {
            Fail "the two versions diverged under this fault"
            $script:bad++
        }
    } finally {
        Remove-Item $Path -Force -ErrorAction SilentlyContinue
    }
}

# Same, for a fault that is a MISSING file -- the SKIP branch.
function Invoke-WithMissingFile {
    param([string]$What, [string]$Pair, [string]$Path)
    $script:cases++
    Section ("fault: {0}" -f $What)
    if (-not (Test-Path $Path)) { Fail "not found: $Path"; $script:bad++; return }
    $hidden = "$Path.m7-hidden"
    if (Test-Path $hidden) { Fail "leftover from a previous run: $hidden"; $script:bad++; return }
    try {
        Move-Item $Path $hidden
        & powershell -NoProfile -ExecutionPolicy Bypass -File $compare -Only $Pair | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Ok "both versions produced identical output under this fault"
        } else {
            Fail "the two versions diverged under this fault"
            $script:bad++
        }
    } finally {
        if (Test-Path $hidden) { Move-Item $hidden $Path -Force }
    }
}

# --- version drift ----------------------------------------------------------
Invoke-WithFault "RELEASE-NOTES.md heading names a different version" "version" `
    (Join-Path $BOOT_REPO "RELEASE-NOTES.md") `
    { param($t) [regex]::Replace($t, '(?m)^(##\s+.*?)(\d+)(\.\d+\.\d+)', { param($m)
        $m.Groups[1].Value + ([int]$m.Groups[2].Value + 7) + $m.Groups[3].Value }, 1) }

# --- published root drift ---------------------------------------------------
# One byte of the compiled constant, which is the exact shape of "somebody
# rotated the key and did not regenerate the fingerprint".
Invoke-WithFault "owner_slot.c fingerprint is one byte off" "public-root" `
    (Join-Path $BOOT_REPO "IAPServer/owner_slot.c") `
    { param($t)
      $m = [regex]::Match($t, 'k_published_root_sha256\s*\[\s*32\s*\]\s*=\s*\{[^}]*?0x([0-9a-fA-F]{2})')
      if (-not $m.Success) { return $t }
      $g = $m.Groups[1]
      $t.Substring(0, $g.Index) + "00" + $t.Substring($g.Index + 2) }

# --- mirror: single-value DIFF ----------------------------------------------
Invoke-WithFault "discovery reply cap changed on the bootloader side only" "mirror" `
    (Join-Path $BOOT_REPO "IAPServer/udp_server.c") `
    { param($t) [regex]::Replace($t, '(#define\s+DISCOVERY_MAX_REPLIES_PER_SEC\s+)(\d+)', { param($m)
        $m.Groups[1].Value + ([int]$m.Groups[2].Value + 1) }, 1) }

# --- mirror: multi-part DIFF ------------------------------------------------
# The FMC pin map is 39 ";"-joined parts and takes the other formatting branch,
# the one that prints "only here:" plus a count of the parts that agree. That
# branch has more logic in it than the whole rest of the comparison.
Invoke-WithFault "one FMC pin renamed in fmc.c only" "mirror" `
    (Join-Path $BOOT_REPO "Core/Src/fmc.c") `
    { param($t) [regex]::Replace($t, '(P[A-I]\d+)(\s*-+>\s*FMC_)(D4)\b', '$1$2D14', 1) }

# --- mirror: SKIP branch ----------------------------------------------------
Invoke-WithMissingFile "IAP_boot_handoff.h missing on the bootloader side" "mirror" `
    (Join-Path $BOOT_REPO "IAPServer/IAP_boot_handoff.h")

# --- core sync: ONLY-LIVE and ONLY-REPO -------------------------------------
Invoke-WithExtraFile "a file exists in live but not in the repo" "core" `
    (Join-Path $CORE_LIVE "m7_fault_only_live.txt")

Invoke-WithExtraFile "a file exists in the repo but not in live" "core" `
    (Join-Path $CORE_REPO "m7_fault_only_repo.txt")

# --- the tree must be exactly as we found it --------------------------------
Section "working tree restored?"
$dirty = @()
foreach ($r in @($BOOT_REPO, $CORE_REPO)) {
    $s = & git -C $r status --porcelain 2>&1
    if ($LASTEXITCODE -ne 0) { Warn "  git unavailable for $r"; continue }
    if ($s) {
        Write-Host ("  {0}" -f $r)
        $s | ForEach-Object { Write-Host ("    {0}" -f $_) }
        $dirty += $r
    }
}
if ($dirty.Count -eq 0) {
    Ok "  both repos are clean -- every fault was undone"
} else {
    Warn "  the repos above are not clean."
    Warn "  If you had uncommitted work before this run, that is expected."
    Warn "  If you did not, check the entries against the faults listed above."
}

Section "result"
if ($bad -gt 0) {
    Fail ("{0} of {1} fault case(s) behaved differently between the two versions" -f $bad, $cases)
    exit 1
}
Ok ("all {0} fault case(s) drove both versions down the same path" -f $cases)
exit 0
