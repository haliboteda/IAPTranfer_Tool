# M7 acceptance harness: run a PowerShell check and its Python translation and
# compare the two outputs byte for byte.
#
#     .\tools\m7-compare.ps1                    compare every known pair
#     .\tools\m7-compare.ps1 -Only version      compare one pair
#     .\tools\m7-compare.ps1 -Keep              leave the captured output on disk
#
# Why this exists: the danger in M7 is not "the Python version does not run", it
# is "the Python version runs, passes, and tests something else". Comparing
# verdicts is not enough for that -- a check that silently stopped looking at a
# file still says PASS. So the acceptance criterion is the whole output, not the
# exit code.
#
# Both scripts are run as child processes so Write-Host and print() alike land
# on a real stdout that can be captured.
#
# Exit 0 = every pair matched, 1 = at least one differed.

param(
    [string]$Only = "",
    [switch]$Keep
)

. "$PSScriptRoot/_common.ps1"

# Known is the escape hatch, and it is deliberately narrow: an exact string that
# is ALLOWED to differ, with the reason. Anything not listed here is a failure.
#
# The point of listing them rather than loosening the comparison is that each
# one had to be argued for once, in writing, and shows up in the output every
# time it fires. A fuzzy comparison would hide the next one.
$pairs = @(
    @{ Name = "version";     Ps1 = "check-version-sync.ps1"; Py = "check_version_sync.py" },
    @{ Name = "mirror";      Ps1 = "check-mirror-sync.ps1";  Py = "check_mirror_sync.py"  },
    @{ Name = "core";        Ps1 = "check-core-sync.ps1";    Py = "check_core_sync.py"    },
    @{ Name = "public-root"; Ps1 = "check-public-root.ps1";  Py = "check_public_root.py";
       Known = @(
           @{ From = "(-Print gives it)"; To = "(--print gives it)";
              Why  = "the switch really is named differently; printing -Print from the Python version would send the reader after a switch it does not have" }
       )
    }
)
if ($Only) { $pairs = @($pairs | Where-Object { $_.Name -eq $Only }) }
if ($pairs.Count -eq 0) { Fail "no pair named '$Only'"; exit 2 }

# A0 is not a script pair -- it is common.py --probe against selfcheck's own
# banner -- so it is compared by hand, not here. Saying so beats a silent gap.
$python = if ($PLATFORM -eq "windows") { "python" } else { "python3" }

$bad = 0
foreach ($p in $pairs) {
    Section ("M7 compare: {0}" -f $p.Name)

    $ps1 = Join-Path $PSScriptRoot $p.Ps1
    $py  = Join-Path $PSScriptRoot $p.Py
    foreach ($f in @($ps1, $py)) {
        if (-not (Test-Path $f)) { Fail "missing: $f"; $bad++; continue }
    }
    if (-not (Test-Path $ps1) -or -not (Test-Path $py)) { continue }

    # NO_COLOR keeps the Python side from emitting escapes if it ever runs on a
    # tty; PowerShell child processes never colour a redirected stream.
    $env:NO_COLOR = "1"
    $aRaw = & powershell -NoProfile -ExecutionPolicy Bypass -File $ps1 2>&1
    $aCode = $LASTEXITCODE
    $bRaw = & $python $py 2>&1
    $bCode = $LASTEXITCODE
    Remove-Item Env:\NO_COLOR -ErrorAction SilentlyContinue

    $a = ($aRaw | Out-String) -replace "`r`n", "`n"
    $b = ($bRaw | Out-String) -replace "`r`n", "`n"

    # Rewrite the PowerShell side's known-different strings into the Python
    # form, and say so. A deviation that stops occurring is worth noticing too,
    # so an entry that never fires is reported as stale.
    foreach ($k in @($p.Known)) {
        if (-not $k) { continue }
        if ($a.Contains($k.From)) {
            $a = $a.Replace($k.From, $k.To)
            Warn ("  known deviation, allowed: '{0}' -> '{1}'" -f $k.From, $k.To)
            Warn ("    {0}" -f $k.Why)
        } elseif ($aCode -ne 0) {
            # Only meaningful once the failure path has actually run. Reporting
            # it on every clean run would make the entry permanent noise, and a
            # warning that is always there is one nobody reads.
            Warn ("  known deviation never appeared in this run: '{0}'" -f $k.From)
            Warn ("    if the line is gone for good, delete the entry")
        }
    }

    $aFile = Get-ScratchFile ("m7-{0}.ps1.out" -f $p.Name)
    $bFile = Get-ScratchFile ("m7-{0}.py.out"  -f $p.Name)
    [System.IO.File]::WriteAllText($aFile, $a)
    [System.IO.File]::WriteAllText($bFile, $b)

    if ($aCode -ne $bCode) {
        Fail ("exit code differs: ps1={0} py={1}" -f $aCode, $bCode)
        $bad++
    }

    if ($a -eq $b) {
        Ok ("identical, {0} line(s), exit {1}" -f ($a -split "`n").Count, $aCode)
        if (-not $Keep) { Remove-Item $aFile, $bFile -ErrorAction SilentlyContinue }
        continue
    }

    $bad++
    Fail "output differs"
    $al = $a -split "`n"
    $bl = $b -split "`n"
    $n = [Math]::Max($al.Count, $bl.Count)
    $shown = 0
    for ($i = 0; $i -lt $n; $i++) {
        $x = if ($i -lt $al.Count) { $al[$i] } else { "<missing>" }
        $y = if ($i -lt $bl.Count) { $bl[$i] } else { "<missing>" }
        if ($x -ceq $y) { continue }
        if ($shown -ge 20) { Warn "  ... more differences not shown"; break }
        Warn ("  line {0}" -f ($i + 1))
        Warn ("    ps1: {0}" -f $x)
        Warn ("    py : {0}" -f $y)
        $shown++
    }
    Write-Host ("  full output kept at:")
    Write-Host ("    {0}" -f $aFile)
    Write-Host ("    {0}" -f $bFile)
}

Section "result"
if ($bad -gt 0) {
    Fail ("{0} pair(s) did not match -- the Python version is NOT a drop-in yet" -f $bad)
    exit 1
}
Ok ("all {0} pair(s) produce identical output" -f $pairs.Count)
exit 0
